"""Unified strategy-facing API (yfinance-like surface)."""

from __future__ import annotations

from datetime import date
from typing import Optional, Sequence, Union

import pandas as pd

from halalquant.base import METRIC_COLUMNS, BaseDataProvider, BaseScreener
from halalquant.database._cache import CacheBackedProvider, CacheLike, LocalCache, resolve_cache
from halalquant.database._dataset import prepare_dataset
from halalquant.providers._filings import FilingsProvider
from halalquant.providers._yfinance import YFinanceProvider
from halalquant.purification._purifier import Purifier
from halalquant.screening._aaoifi import AAOIFIScreener
from halalquant.screening._compare import compare_screeners
from halalquant.screening._djim import DJIMScreener
from halalquant.screening._sector_filter import SectorFilter
from halalquant.utils._metrics import (
    build_financial_metrics,
    fill_market_caps_from_prices,
    normalize_freq,
    price_window_for_fundamentals,
)
from halalquant.utils._pit_adjustments import as_of_filter
from halalquant.utils.validation import validate_date_range, validate_symbols

DateLikeInput = Union[str, date]


def download(
    tickers: Union[str, Sequence[str]],
    start: Optional[DateLikeInput] = None,
    end: Optional[DateLikeInput] = None,
    provider: Optional[BaseDataProvider] = None,
    cache: CacheLike = None,
) -> pd.DataFrame:
    """
    Fetch OHLCV history for one or more tickers via yfinance.

    Pass ``cache=True`` (or set ``HALALQUANT_USE_CACHE=1``) to read/write the
    local DuckDB store. Nothing is written to disk by default.
    """
    symbols = validate_symbols(tickers)
    start_date, end_date = validate_date_range(start, end)
    market, _, _ = _clients(provider, None, cache)
    return market.get_prices(symbols, start=start_date, end=end_date)


def get_halal_universe(
    tickers: Union[str, Sequence[str]],
    as_of: Optional[DateLikeInput] = None,
    standard: str = "aaoifi",
    provider: Optional[BaseDataProvider] = None,
    filings: Optional[BaseDataProvider] = None,
    apply_sector_filter: bool = True,
    cache: CacheLike = None,
) -> pd.DataFrame:
    """
    Fetch fundamentals and return compliant tickers plus screening metrics.

    Market data (prices, sector) comes from yfinance. Fundamentals come from
    SEC EDGAR when a CIK exists, otherwise from Yahoo annual statements.
    """
    fundamentals = _prepare_universe_fundamentals(
        tickers,
        as_of=as_of,
        provider=provider,
        filings=filings,
        apply_sector_filter=apply_sector_filter,
        cache=cache,
    )
    return _screener_for(standard).evaluate_compliance(fundamentals)


def compare_standards(
    tickers: Union[str, Sequence[str]],
    as_of: Optional[DateLikeInput] = None,
    provider: Optional[BaseDataProvider] = None,
    filings: Optional[BaseDataProvider] = None,
    apply_sector_filter: bool = True,
    cache: CacheLike = None,
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
        cache=cache,
    )
    return compare_screeners(fundamentals)


def get_financial_metrics(
    tickers: Union[str, Sequence[str]],
    start: Optional[DateLikeInput] = None,
    end: Optional[DateLikeInput] = None,
    provider: Optional[BaseDataProvider] = None,
    filings: Optional[BaseDataProvider] = None,
    freq: Optional[str] = None,
    cache: CacheLike = None,
) -> pd.DataFrame:
    """
    Return screening ratios and income metrics over a date range.

    By default one row per annual filing whose report date falls in
    ``[start, end]`` (only filings already public by ``end``). Pass
    ``freq`` (for example ``"ME"`` or ``"QE"``) to emit calendar
    point-in-time snapshots instead.

    After ``prepare_dataset()``, pass ``cache=True`` to build the panel
    from the local AAOIFI metrics DB instead of SEC EDGAR / Yahoo.
    """
    symbols = validate_symbols(tickers)
    start_date, end_date = validate_date_range(start, end)
    market, statements, store = _clients(provider, filings, cache)
    freq_key = normalize_freq(freq) if freq else "annual"
    empty = pd.DataFrame(columns=list(METRIC_COLUMNS))

    if store is not None:
        cached = store.read_metrics(symbols, start=start_date, end=end_date, freq=freq_key)
        missing = _missing_metric_symbols(cached, symbols, start_date, end_date, freq_key)
        if not missing:
            return _metrics_view(cached)
    else:
        cached = empty
        missing = list(symbols)

    fundamentals = statements.get_balance_sheet(missing, as_of=None)
    if fundamentals.empty:
        return _metrics_view(cached) if store is not None else empty

    prices = _prices_for_fundamentals(market, fundamentals, as_of=end_date)
    income = statements.get_income_statement(missing, as_of=None)
    panel = build_financial_metrics(
        fundamentals,
        income,
        prices,
        start=start_date,
        end=end_date,
        freq=freq,
    )
    if store is not None:
        if not panel.empty:
            store.write_metrics(panel, freq=freq_key)
        combined = store.read_metrics(symbols, start=start_date, end=end_date, freq=freq_key)
        return _metrics_view(combined)
    return panel.reset_index(drop=True)


def purify_dividends(
    tickers: Union[str, Sequence[str]],
    start: Optional[DateLikeInput] = None,
    end: Optional[DateLikeInput] = None,
    provider: Optional[BaseDataProvider] = None,
    filings: Optional[BaseDataProvider] = None,
    cache: CacheLike = None,
) -> pd.DataFrame:
    """
    Fetch dividends from yfinance and income from filings, then purify.

    Uses interest income / revenue as a conservative impure-income proxy when
    a finer breakdown is unavailable. Each dividend is matched to the latest
    income statement that was already public on the ex-date.
    """
    symbols = validate_symbols(tickers)
    start_date, end_date = validate_date_range(start, end)
    market, statements, _ = _clients(provider, filings, cache)

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


def _clients(
    provider: Optional[BaseDataProvider],
    filings: Optional[BaseDataProvider],
    cache: CacheLike,
) -> tuple[BaseDataProvider, BaseDataProvider, Optional[LocalCache]]:
    market = provider or YFinanceProvider()
    statements = filings or FilingsProvider()
    store = resolve_cache(cache, provider=market, filings=statements)
    if store is None:
        return market, statements, None
    wrapped = CacheBackedProvider(store)
    return wrapped, wrapped, store


def _prepare_universe_fundamentals(
    tickers: Union[str, Sequence[str]],
    as_of: Optional[DateLikeInput] = None,
    provider: Optional[BaseDataProvider] = None,
    filings: Optional[BaseDataProvider] = None,
    apply_sector_filter: bool = True,
    cache: CacheLike = None,
) -> pd.DataFrame:
    symbols = validate_symbols(tickers)
    market, statements, _ = _clients(provider, filings, cache)

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


def _metrics_view(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=list(METRIC_COLUMNS))
    out = frame.copy()
    for col in METRIC_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA
    return out[list(METRIC_COLUMNS)].reset_index(drop=True)


def _missing_metric_symbols(
    cached: pd.DataFrame,
    symbols: Sequence[str],
    start: date,
    end: date,
    freq: str,
) -> list[str]:
    if cached is None or cached.empty:
        return list(symbols)
    have: set[str] = set()
    slack = pd.Timedelta(days=7)
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    for symbol, frame in cached.groupby("symbol"):
        if freq == "annual":
            have.add(str(symbol))
            continue
        stamps = pd.to_datetime(frame["as_of"], errors="coerce")
        if stamps.empty or stamps.isna().all():
            continue
        if stamps.min() <= start_ts + slack and stamps.max() >= end_ts - slack:
            have.add(str(symbol))
    return [s for s in symbols if s not in have]


def _prices_for_fundamentals(
    client: BaseDataProvider,
    fundamentals: pd.DataFrame,
    as_of: DateLikeInput,
) -> pd.DataFrame:
    symbols = list(fundamentals["symbol"].unique()) if "symbol" in fundamentals.columns else []
    if not symbols:
        return pd.DataFrame()
    start, end = price_window_for_fundamentals(fundamentals, as_of=as_of)
    try:
        return client.get_prices(symbols, start=start, end=end)
    except (ValueError, NotImplementedError):
        return pd.DataFrame()


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
    grouped = {symbol: frame for symbol, frame in income.groupby("symbol")}
    rows: list[dict] = []
    for _, div in out.iterrows():
        payload = div.to_dict()
        ex_ts = pd.Timestamp(div["ex_date"])
        known = grouped.get(div["symbol"])
        if known is not None:
            known = known[known["_filed"] <= ex_ts]
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
    if prices is None:
        prices = _prices_for_fundamentals(client, fundamentals, as_of=as_of)
    return fill_market_caps_from_prices(
        fundamentals,
        prices,
        as_of=as_of,
        price_as_of=price_as_of,
    )
