"""Unified strategy-facing API (yfinance-like surface)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional, Sequence, Union

import pandas as pd

from halalquant.base import BaseDataProvider
from halalquant.providers._sec_edgar import SECEdgarProvider
from halalquant.providers._yfinance import YFinanceProvider
from halalquant.purification._purifier import Purifier
from halalquant.screening._aaoifi import AAOIFIScreener
from halalquant.screening._sector_filter import SectorFilter
from halalquant.utils._pit_adjustments import as_of_filter
from halalquant.utils.validation import validate_date_range, validate_symbols

DateLikeInput = Union[str, date]


def download(
    tickers: Union[str, Sequence[str]],
    start: Optional[DateLikeInput] = None,
    end: Optional[DateLikeInput] = None,
    provider: Optional[BaseDataProvider] = None,
) -> pd.DataFrame:
    """
    Fetch OHLCV history for one or more tickers via yfinance.

    Returns a long DataFrame with normalized column names. Nothing is written
    to disk and no vendor API key is required.
    """
    symbols = validate_symbols(tickers)
    start_date, end_date = validate_date_range(start, end)
    client = provider or YFinanceProvider()
    return client.get_prices(symbols, start=start_date, end=end_date)


def get_halal_universe(
    tickers: Union[str, Sequence[str]],
    as_of: Optional[DateLikeInput] = None,
    standard: str = "aaoifi",
    provider: Optional[BaseDataProvider] = None,
    filings: Optional[SECEdgarProvider] = None,
    apply_sector_filter: bool = True,
) -> pd.DataFrame:
    """
    Fetch fundamentals and return compliant tickers plus screening metrics.

    Market data (prices, sector) comes from yfinance. US filings come from
    SEC EDGAR. Screening math runs in this library.
    """
    symbols = validate_symbols(tickers)
    market = provider or YFinanceProvider()
    statements = filings or SECEdgarProvider()

    if apply_sector_filter:
        sector_map: dict[str, str] = {}
        if hasattr(market, "get_sector_map"):
            try:
                sector_map = market.get_sector_map(symbols)
            except (ValueError, OSError):
                sector_map = {}
        sector_filter = SectorFilter()
        symbols = sector_filter.filter_symbols(symbols, sector_map=sector_map or None)

    empty = pd.DataFrame(
        columns=[
            "symbol",
            "as_of",
            "is_compliant",
            "debt_ratio",
            "cash_ratio",
            "receivables_ratio",
            "standard",
            "reason",
        ]
    )
    if not symbols:
        return empty

    fundamentals = statements.get_balance_sheet(symbols, as_of=as_of)
    cutoff = as_of or date.today()
    if not fundamentals.empty:
        fundamentals = as_of_filter(fundamentals, as_of=str(cutoff)[:10])
        fundamentals = _fill_market_caps_from_prices(market, fundamentals, as_of=cutoff)

    if standard.lower() == "aaoifi":
        screener = AAOIFIScreener()
    else:
        from halalquant.screening._djim import DJIMScreener

        screener = DJIMScreener()

    return screener.evaluate_compliance(fundamentals)


def purify_dividends(
    tickers: Union[str, Sequence[str]],
    start: Optional[DateLikeInput] = None,
    end: Optional[DateLikeInput] = None,
    provider: Optional[BaseDataProvider] = None,
    filings: Optional[SECEdgarProvider] = None,
) -> pd.DataFrame:
    """
    Fetch dividends from yfinance and income from SEC, then purify.

    Uses interest income / revenue as a conservative impure-income proxy when
    a finer breakdown is unavailable. Each dividend is matched to the latest
    income statement that was already filed on the ex-date.
    """
    symbols = validate_symbols(tickers)
    start_date, end_date = validate_date_range(start, end)
    market = provider or YFinanceProvider()
    statements = filings or SECEdgarProvider()

    if hasattr(market, "get_dividends"):
        dividends = market.get_dividends(symbols, start=start_date, end=end_date)
    else:
        dividends = pd.DataFrame()
    income = statements.get_income_statement(symbols, as_of=end_date)
    joined = _match_income_to_dividends(dividends, income)

    empty_cols = [
        "symbol",
        "ex_date",
        "dividend",
        "adj_dividend",
        "record_date",
        "payment_date",
        "report_date",
        "total_revenue",
        "non_compliant_income",
        "impure_ratio",
        "purification_amount",
    ]
    if joined.empty:
        return pd.DataFrame(columns=empty_cols)

    purifier = Purifier()
    joined["impure_ratio"] = purifier.impure_income_ratio(
        joined["non_compliant_income"],
        joined["total_revenue"],
    )
    joined["purification_amount"] = purifier.purification_amount(
        joined["dividend"],
        joined["non_compliant_income"],
        joined["total_revenue"],
    )
    return joined[empty_cols]


def _match_income_to_dividends(
    dividends: pd.DataFrame,
    income: pd.DataFrame,
) -> pd.DataFrame:
    """Attach the latest filed income statement known on each ex-date."""
    if dividends.empty:
        return dividends.copy()

    out = dividends.copy()
    out["total_revenue"] = pd.NA
    out["non_compliant_income"] = pd.NA
    out["report_date"] = pd.NA
    if income.empty:
        return out

    income = income.copy()
    income["_filed"] = pd.to_datetime(income["filed_date"], errors="coerce")
    rows: list[dict] = []
    for _, div in out.iterrows():
        payload = div.to_dict()
        ex_ts = pd.Timestamp(div["ex_date"])
        known = income[
            (income["symbol"] == div["symbol"]) & (income["_filed"] <= ex_ts)
        ]
        if not known.empty:
            latest = known.sort_values(["_filed", "report_date"]).iloc[-1]
            payload["total_revenue"] = latest["total_revenue"]
            payload["non_compliant_income"] = latest["non_compliant_income"]
            payload["report_date"] = latest["report_date"]
        rows.append(payload)
    return pd.DataFrame(rows)


def _fill_market_caps_from_prices(
    client: BaseDataProvider,
    fundamentals: pd.DataFrame,
    as_of: DateLikeInput,
) -> pd.DataFrame:
    """Fill missing market cap using shares outstanding × trailing prices."""
    out = fundamentals.copy()
    needs_cap = out["market_cap"].isna() if "market_cap" in out.columns else pd.Series(True, index=out.index)
    needs_24 = (
        out["market_cap_24m"].isna() if "market_cap_24m" in out.columns else pd.Series(True, index=out.index)
    )
    if not (needs_cap | needs_24).any():
        return out
    if "shares_outstanding" not in out.columns or out["shares_outstanding"].isna().all():
        return out

    symbols = list(out["symbol"].unique())
    end = pd.Timestamp(str(as_of)[:10]).date()
    start = end - timedelta(days=int(24 * 30.44) + 14)
    try:
        prices = client.get_prices(symbols, start=start, end=end)
    except (ValueError, NotImplementedError):
        return out
    if prices.empty:
        return out

    prices = prices.copy()
    prices["date"] = pd.to_datetime(prices["date"])
    window = pd.Timedelta(days=int(24 * 30.44))

    market_caps: list[Optional[float]] = []
    market_caps_24m: list[Optional[float]] = []
    for _, row in out.iterrows():
        shares = row.get("shares_outstanding")
        existing_spot = row.get("market_cap")
        existing_avg = row.get("market_cap_24m")
        if pd.isna(shares) or float(shares) <= 0:
            market_caps.append(existing_spot if not pd.isna(existing_spot) else None)
            market_caps_24m.append(existing_avg if not pd.isna(existing_avg) else None)
            continue
        shares_f = float(shares)
        report = row.get("report_date") or as_of
        end_ts = pd.Timestamp(report)
        sym_prices = prices[prices["symbol"] == row["symbol"]]
        known = sym_prices[sym_prices["date"] <= end_ts]
        if known.empty:
            market_caps.append(existing_spot if not pd.isna(existing_spot) else None)
            market_caps_24m.append(existing_avg if not pd.isna(existing_avg) else None)
            continue
        close = pd.to_numeric(known["close"], errors="coerce")
        spot = float(close.iloc[-1]) * shares_f
        trail = known[known["date"] >= (end_ts - window)]
        trail_close = pd.to_numeric(trail["close"], errors="coerce")
        avg = float(trail_close.mean()) * shares_f if not trail_close.empty else spot
        market_caps.append(existing_spot if not pd.isna(existing_spot) else spot)
        market_caps_24m.append(existing_avg if not pd.isna(existing_avg) else avg)

    out["market_cap"] = market_caps
    out["market_cap_24m"] = market_caps_24m
    return out
