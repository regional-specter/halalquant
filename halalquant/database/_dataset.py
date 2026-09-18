"""Prepare a warm AAOIFI metrics dataset on disk."""

from __future__ import annotations

from datetime import date
from typing import Callable, Optional, Sequence, Union

import pandas as pd

from halalquant.base import BaseDataProvider, DateLike
from halalquant.database._cache import LocalCache, default_cache_path, resolve_cache
from halalquant.database._universe import list_universe, map_universe_activity
from halalquant.screening._aaoifi import AAOIFIScreener
from halalquant.screening._djim import DJIMScreener
from halalquant.screening._sector_filter import SectorFilter
from halalquant.utils._metrics import (
    build_financial_metrics,
    lookback,
    normalize_freq,
    price_window_for_fundamentals,
)
from halalquant.utils.validation import validate_date_range, validate_symbols

ProgressFn = Callable[[str], None]


def prepare_dataset(
    tickers: Optional[Union[str, Sequence[str]]] = None,
    universe: str = "sp500",
    start: Optional[DateLike] = None,
    end: Optional[DateLike] = None,
    freq: Optional[str] = "ME",
    cache: Union[bool, str, LocalCache] = True,
    provider: Optional[BaseDataProvider] = None,
    filings: Optional[BaseDataProvider] = None,
    apply_sector_filter: bool = True,
    force_refresh: bool = False,
    progress: Union[bool, ProgressFn] = True,
) -> pd.DataFrame:
    """
    Fetch once and write a reusable AAOIFI metrics database.

    This is the slow, rate-limited pass: SEC companyfacts, Yahoo prices,
    dividends, and sector labels. After it finishes, ``hq.download`` /
    ``hq.get_financial_metrics`` / ``hq.get_halal_universe`` with
    ``cache=True`` read DuckDB (and Parquet mirrors) instead of the network.

    Parameters
    ----------
    tickers
        Explicit symbols. If omitted, ``universe`` is downloaded (default
        S&P 500).
    universe
        Named list used when ``tickers`` is omitted. Currently ``"sp500"``.
    start, end
        Inclusive price / metrics window. Defaults to five years ending today.
    freq
        Calendar frequency for the prepared point-in-time panel. ``"ME"``
        (month-end) is the default because monthly rebalances are cheap once
        this panel exists. Pass ``None`` for annual filings only.
    cache
        ``True`` writes to ``~/.halalquant/cache.duckdb``. A path or
        ``LocalCache`` instance overrides the location.
    apply_sector_filter
        Drop haram sectors before fetching filings (saves SEC calls).
    force_refresh
        Ignore existing cache rows and refetch.

    Returns
    -------
    DataFrame
        One row per input symbol with sector, inclusion flag, and row counts.
    """
    log = _progress_logger(progress)
    start_date, end_date = validate_date_range(start, end)
    if start is None:
        start_date = (pd.Timestamp(end_date) - pd.DateOffset(years=5)).date()

    store = resolve_cache(cache, provider=provider, filings=filings)
    if store is None:
        store = LocalCache(provider=provider, filings=filings)
    if force_refresh:
        sec = getattr(store.filings, "sec", None)
        if sec is not None and hasattr(sec, "refresh_facts"):
            sec.refresh_facts = True

    if tickers is None:
        log(f"Loading {universe} constituents…")
        members = list_universe(universe)
        symbols = validate_symbols(list(members["symbol"]))
        sector_map = {
            str(row["symbol"]): map_universe_activity(row.get("sector"), row.get("industry"))
            for _, row in members.iterrows()
        }
    else:
        universe = "custom"
        symbols = validate_symbols(tickers)
        members = pd.DataFrame({"symbol": symbols})
        sector_map = {}

    log(f"{len(symbols)} symbols in {universe}")
    missing_sectors = [s for s in symbols if s not in sector_map or not sector_map.get(s)]
    if missing_sectors:
        log(f"Filling {len(missing_sectors)} sector labels from Yahoo…")
        sector_map.update(store.get_sector_map(missing_sectors, force_refresh=force_refresh))
    if sector_map:
        store.db.write_sector_map(sector_map)

    sector_filter = SectorFilter()
    kept = (
        sector_filter.filter_symbols(symbols, sector_map=sector_map or None)
        if apply_sector_filter
        else list(symbols)
    )
    excluded = set(symbols) - set(kept)
    log(
        f"Sector screen kept {len(kept)} / {len(symbols)} "
        f"({len(excluded)} excluded)"
    )

    universe_rows = pd.DataFrame(
        {
            "universe": universe,
            "symbol": symbols,
            "sector": [sector_map.get(s) for s in symbols],
            "sector_allowed": [s not in excluded for s in symbols],
        }
    )
    store.write_universe(universe_rows)

    work = kept if apply_sector_filter else symbols
    if not work:
        log("Nothing left after the sector screen.")
        return _summary_frame(universe_rows, store)

    price_start = (pd.Timestamp(start_date) - pd.Timedelta(days=lookback().days)).date()
    log(f"Caching prices {price_start} → {end_date}…")
    store.get_prices(work, start=price_start, end=end_date, force_refresh=force_refresh)

    log("Caching SEC / Yahoo balance sheets (rate-limited)…")
    _chunked_fetch(work, 25, "filings", log, lambda chunk: store.get_balance_sheet(chunk, as_of=None, force_refresh=force_refresh))
    balance = store.get_balance_sheet(work, as_of=None, force_refresh=False)
    log("Caching income, EBITDA, and cash-flow fields…")
    _chunked_fetch(work, 25, "income", log, lambda chunk: store.get_income_statement(chunk, as_of=None, force_refresh=force_refresh))
    income = store.get_income_statement(work, as_of=None, force_refresh=False)
    log("Caching dividends…")
    _chunked_fetch(
        work,
        40,
        "dividends",
        log,
        lambda chunk: store.get_dividends(chunk, start=start_date, end=end_date, force_refresh=force_refresh),
    )

    if balance.empty:
        log("No balance sheets returned; skipping AAOIFI metrics panel.")
        store.write_meta(_meta(universe, start_date, end_date, freq, work))
        return _summary_frame(universe_rows, store)

    px_start, px_end = price_window_for_fundamentals(balance, as_of=end_date)
    prices = store.get_prices(work, start=px_start, end=px_end, force_refresh=False)

    annual, snapshot_freq = _write_metric_panels(
        store, balance, income, prices, start_date, end_date, freq, log
    )

    store.write_meta(_meta(universe, start_date, end_date, snapshot_freq, work))
    log(f"Wrote cache → {store.db.path}")
    if store.mirror_parquet:
        log(f"Parquet mirrors → {store.parquet_dir}")
    return _summary_frame(universe_rows, store)


def rebuild_metric_panels(
    cache: Union[bool, str, LocalCache] = True,
    start: Optional[DateLike] = None,
    end: Optional[DateLike] = None,
    freq: Optional[str] = "ME",
    progress: Union[bool, ProgressFn] = True,
) -> pd.DataFrame:
    """Rebuild AAOIFI metric tables from cached filings (no network)."""
    log = _progress_logger(progress)
    store = resolve_cache(cache) or LocalCache()
    start_date, end_date = validate_date_range(start, end)
    if start is None:
        start_date = date(2018, 1, 1)
    members = store.read_universe()
    if members.empty:
        raise ValueError("No universe in the cache. Run prepare_dataset first.")
    work = list(members.loc[members["sector_allowed"], "symbol"])
    log(f"Rebuilding metrics for {len(work)} cached names…")
    balance = store.get_balance_sheet(work, as_of=None, force_refresh=False)
    income = store.get_income_statement(work, as_of=None, force_refresh=False)
    px_start, px_end = price_window_for_fundamentals(balance, as_of=end_date)
    prices = store.get_prices(work, start=px_start, end=px_end, force_refresh=False)
    _write_metric_panels(store, balance, income, prices, start_date, end_date, freq, log)
    store.write_meta(
        _meta("sp500", start_date, end_date, normalize_freq(freq) if freq else None, work)
    )
    log(f"Rebuilt metrics in {store.db.path}")
    return _summary_frame(members, store)


def _write_metric_panels(
    store: LocalCache,
    balance: pd.DataFrame,
    income: pd.DataFrame,
    prices: pd.DataFrame,
    start_date: date,
    end_date: date,
    freq: Optional[str],
    log: ProgressFn,
) -> tuple[pd.DataFrame, Optional[str]]:
    annual_start = start_date
    report_dates = pd.to_datetime(balance["report_date"], errors="coerce")
    if report_dates.notna().any():
        annual_start = min(start_date, report_dates.min().date())
    log("Building annual AAOIFI metrics panel…")
    annual = build_financial_metrics(
        balance, income, prices, start=annual_start, end=end_date, freq=None
    )
    store.write_metrics(annual, freq="annual")

    snapshot_freq = normalize_freq(freq) if freq else None
    if snapshot_freq:
        log(f"Building {snapshot_freq} point-in-time AAOIFI panel…")
        monthly = build_financial_metrics(
            balance,
            income,
            prices,
            start=start_date,
            end=end_date,
            freq=snapshot_freq,
        )
        store.write_metrics(monthly, freq=snapshot_freq)

    if not annual.empty:
        latest = (
            annual.sort_values(["symbol", "report_date", "filed_date"])
            .groupby("symbol", as_index=False)
            .tail(1)
        )
        fundamentals = _metrics_as_fundamentals(latest)
        store.write_compliance(AAOIFIScreener().evaluate_compliance(fundamentals))
        store.write_compliance(DJIMScreener().evaluate_compliance(fundamentals))
    return annual, snapshot_freq


def _metrics_as_fundamentals(metrics: pd.DataFrame) -> pd.DataFrame:
    frame = metrics.copy()
    if "report_date" not in frame.columns:
        frame["report_date"] = frame.get("as_of")
    return frame


def _meta(
    universe: str,
    start: date,
    end: date,
    freq: Optional[str],
    symbols: Sequence[str],
) -> dict[str, str]:
    return {
        "universe": universe,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "freq": freq or "annual",
        "n_symbols": str(len(symbols)),
        "cache_path": str(default_cache_path()),
        "prepared_at": date.today().isoformat(),
    }


def _summary_frame(universe_rows: pd.DataFrame, store: LocalCache) -> pd.DataFrame:
    symbols = list(universe_rows["symbol"])
    prices = store.db.read_prices(symbols)
    balance = store.db.read_balance_sheets(symbols)
    income = store.db.read_income_statements(symbols)
    metrics = store.db.read_metrics(symbols)
    n_prices = prices.groupby("symbol").size() if not prices.empty else pd.Series(dtype=int)
    n_balance = balance.groupby("symbol").size() if not balance.empty else pd.Series(dtype=int)
    n_income = income.groupby("symbol").size() if not income.empty else pd.Series(dtype=int)
    n_metrics = metrics.groupby("symbol").size() if not metrics.empty else pd.Series(dtype=int)
    out = universe_rows.copy()
    out["n_prices"] = out["symbol"].map(n_prices).fillna(0).astype(int)
    out["n_balance_sheets"] = out["symbol"].map(n_balance).fillna(0).astype(int)
    out["n_income_statements"] = out["symbol"].map(n_income).fillna(0).astype(int)
    out["n_metrics"] = out["symbol"].map(n_metrics).fillna(0).astype(int)
    return out.reset_index(drop=True)


def _chunked_fetch(symbols: Sequence[str], size: int, label: str, log: ProgressFn, fn) -> None:
    total = len(symbols)
    for i in range(0, total, size):
        chunk = list(symbols[i : i + size])
        log(f"{label} {i + 1}–{min(i + size, total)} / {total}")
        try:
            fn(chunk)
        except Exception as exc:
            log(f"{label} chunk failed ({chunk[0]}–{chunk[-1]}): {type(exc).__name__}: {exc}")


def _progress_logger(progress: Union[bool, ProgressFn]) -> ProgressFn:
    if callable(progress):
        return progress
    if progress:
        return lambda msg: print(f"[halalquant] {msg}", flush=True)
    return lambda msg: None
