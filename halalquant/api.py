"""Unified strategy-facing API (yfinance-like surface)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional, Sequence, Union

import pandas as pd

from halalquant.base import METRIC_COLUMNS, BaseDataProvider, BaseScreener
from halalquant.providers._sec_edgar import SECEdgarProvider
from halalquant.providers._yfinance import YFinanceProvider
from halalquant.purification._purifier import Purifier
from halalquant.screening._aaoifi import AAOIFIScreener, compute_ratios
from halalquant.screening._compare import compare_screeners
from halalquant.screening._djim import DJIMScreener
from halalquant.screening._sector_filter import SectorFilter
from halalquant.utils._pit_adjustments import as_of_filter, known_filings
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
    fundamentals = _prepare_universe_fundamentals(
        tickers,
        as_of=as_of,
        provider=provider,
        filings=filings,
        apply_sector_filter=apply_sector_filter,
    )
    return _screener_for(standard).evaluate_compliance(fundamentals)


def compare_standards(
    tickers: Union[str, Sequence[str]],
    as_of: Optional[DateLikeInput] = None,
    provider: Optional[BaseDataProvider] = None,
    filings: Optional[SECEdgarProvider] = None,
    apply_sector_filter: bool = True,
) -> pd.DataFrame:
    """
    Screen the same tickers under AAOIFI and DJIM and join the verdicts.

    Returns one row per ticker with shared ratios plus `aaoifi_compliant`,
    `djim_compliant`, and `agreement`.
    """
    fundamentals = _prepare_universe_fundamentals(
        tickers,
        as_of=as_of,
        provider=provider,
        filings=filings,
        apply_sector_filter=apply_sector_filter,
    )
    return compare_screeners(fundamentals)


def get_financial_metrics(
    tickers: Union[str, Sequence[str]],
    start: Optional[DateLikeInput] = None,
    end: Optional[DateLikeInput] = None,
    provider: Optional[BaseDataProvider] = None,
    filings: Optional[SECEdgarProvider] = None,
    freq: Optional[str] = None,
) -> pd.DataFrame:
    """
    Return screening ratios and income metrics over a date range.

    By default one row per annual filing whose report date falls in
    ``[start, end]`` (only filings already public by ``end``). Pass
    ``freq`` (for example ``"ME"`` or ``"QE"``) to emit calendar
    point-in-time snapshots instead.
    """
    symbols = validate_symbols(tickers)
    start_date, end_date = validate_date_range(start, end)
    market = provider or YFinanceProvider()
    statements = filings or SECEdgarProvider()

    empty = pd.DataFrame(columns=list(METRIC_COLUMNS))
    fundamentals = statements.get_balance_sheet(symbols, as_of=end_date)
    if fundamentals.empty:
        return empty

    fundamentals = known_filings(fundamentals, as_of=str(end_date)[:10])
    if fundamentals.empty:
        return empty

    prices = _prices_for_fundamentals(market, fundamentals, as_of=end_date)
    income = statements.get_income_statement(symbols, as_of=end_date)
    if not income.empty:
        income = known_filings(income, as_of=str(end_date)[:10])

    if freq:
        pandas_freq = _normalize_freq(freq)
        stamps = pd.date_range(start=start_date, end=end_date, freq=pandas_freq)
        snapshots: list[pd.DataFrame] = []
        for stamp in stamps:
            snap = as_of_filter(fundamentals, as_of=stamp)
            if snap.empty:
                continue
            snap = _fill_market_caps_from_prices(
                market,
                snap,
                as_of=stamp.date(),
                prices=prices,
                price_as_of=stamp.date(),
            )
            snap["as_of"] = stamp.date()
            snapshots.append(snap)
        if not snapshots:
            return empty
        panel = pd.concat(snapshots, ignore_index=True)
    else:
        report_ts = pd.to_datetime(fundamentals["report_date"], errors="coerce")
        start_ts = pd.Timestamp(start_date)
        end_ts = pd.Timestamp(end_date)
        panel = fundamentals[(report_ts >= start_ts) & (report_ts <= end_ts)].copy()
        if panel.empty:
            return empty
        panel = _fill_market_caps_from_prices(
            market,
            panel,
            as_of=end_date,
            prices=prices,
        )
        panel["as_of"] = pd.to_datetime(panel["filed_date"], errors="coerce").dt.date

    ratios = compute_ratios(panel)
    panel["debt_ratio"] = ratios["debt_ratio"].values
    panel["cash_ratio"] = ratios["cash_ratio"].values
    panel["receivables_ratio"] = ratios["receivables_ratio"].values
    panel = _join_income_metrics(panel, income)

    for col in METRIC_COLUMNS:
        if col not in panel.columns:
            panel[col] = pd.NA
    return panel[list(METRIC_COLUMNS)].reset_index(drop=True)


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


def _screener_for(standard: str) -> BaseScreener:
    key = str(standard).strip().lower()
    if key == "aaoifi":
        return AAOIFIScreener()
    if key == "djim":
        return DJIMScreener()
    raise ValueError(f"Unknown screening standard {standard!r}. Use 'aaoifi' or 'djim'.")


def _prepare_universe_fundamentals(
    tickers: Union[str, Sequence[str]],
    as_of: Optional[DateLikeInput] = None,
    provider: Optional[BaseDataProvider] = None,
    filings: Optional[SECEdgarProvider] = None,
    apply_sector_filter: bool = True,
) -> pd.DataFrame:
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

    if not symbols:
        return pd.DataFrame()

    fundamentals = statements.get_balance_sheet(symbols, as_of=as_of)
    cutoff = as_of or date.today()
    if fundamentals.empty:
        return fundamentals
    fundamentals = as_of_filter(fundamentals, as_of=str(cutoff)[:10])
    return _fill_market_caps_from_prices(market, fundamentals, as_of=cutoff)


def _normalize_freq(freq: str) -> str:
    aliases = {"M": "ME", "Q": "QE", "Y": "YE", "A": "YE"}
    return aliases.get(str(freq).upper(), freq)


def _lookback() -> timedelta:
    return timedelta(days=int(24 * 30.44) + 14)


def _prices_for_fundamentals(
    client: BaseDataProvider,
    fundamentals: pd.DataFrame,
    as_of: DateLikeInput,
) -> pd.DataFrame:
    symbols = list(fundamentals["symbol"].unique()) if "symbol" in fundamentals.columns else []
    if not symbols:
        return pd.DataFrame()
    end = pd.Timestamp(str(as_of)[:10]).date()
    start = end - _lookback()
    if "report_date" in fundamentals.columns:
        report_dates = pd.to_datetime(fundamentals["report_date"], errors="coerce")
        if report_dates.notna().any():
            start = min(start, report_dates.min().date() - _lookback())
            end = max(end, report_dates.max().date())
    try:
        return client.get_prices(symbols, start=start, end=end)
    except (ValueError, NotImplementedError):
        return pd.DataFrame()


def _join_income_metrics(panel: pd.DataFrame, income: pd.DataFrame) -> pd.DataFrame:
    out = panel.copy()
    out["total_revenue"] = pd.NA
    out["non_compliant_income"] = pd.NA
    out["impure_ratio"] = pd.NA
    if income.empty or "report_date" not in out.columns:
        return out

    income = income.copy()
    income["_report"] = pd.to_datetime(income["report_date"], errors="coerce")
    income["_filed"] = pd.to_datetime(income["filed_date"], errors="coerce")
    purifier = Purifier()
    revenues: list = []
    impure_income: list = []
    for _, row in out.iterrows():
        as_of_ts = pd.Timestamp(row.get("as_of") or row.get("filed_date"))
        report_ts = pd.to_datetime(row.get("report_date"), errors="coerce")
        known = income[
            (income["symbol"] == row["symbol"]) & (income["_filed"] <= as_of_ts)
        ]
        if known.empty:
            revenues.append(pd.NA)
            impure_income.append(pd.NA)
            continue
        if pd.notna(report_ts):
            same_period = known[known["_report"] == report_ts]
            pick = same_period if not same_period.empty else known
        else:
            pick = known
        latest = pick.sort_values(["_report", "_filed"]).iloc[-1]
        revenues.append(latest.get("total_revenue"))
        impure_income.append(latest.get("non_compliant_income"))
    out["total_revenue"] = revenues
    out["non_compliant_income"] = impure_income
    out["impure_ratio"] = purifier.impure_income_ratio(
        pd.to_numeric(out["non_compliant_income"], errors="coerce"),
        pd.to_numeric(out["total_revenue"], errors="coerce"),
    )
    return out


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
            latest = known.sort_values(["report_date", "_filed"]).iloc[-1]
            payload["total_revenue"] = latest["total_revenue"]
            payload["non_compliant_income"] = latest["non_compliant_income"]
            payload["report_date"] = latest["report_date"]
        rows.append(payload)
    return pd.DataFrame(rows)


def _fill_market_caps_from_prices(
    client: BaseDataProvider,
    fundamentals: pd.DataFrame,
    as_of: DateLikeInput,
    prices: Optional[pd.DataFrame] = None,
    price_as_of: Optional[DateLikeInput] = None,
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

    if prices is None:
        prices = _prices_for_fundamentals(client, out, as_of=as_of)
    if prices is None or prices.empty:
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
        if price_as_of is not None:
            end_ts = pd.Timestamp(str(price_as_of)[:10])
        else:
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
