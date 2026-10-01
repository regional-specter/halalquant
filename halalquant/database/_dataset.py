"""Prepare a warm AAOIFI metrics dataset on disk."""

from __future__ import annotations

from datetime import date
from typing import Callable, Optional, Sequence, Union

import pandas as pd

from halalquant.base import BaseDataProvider, DateLike
from halalquant.database._cache import LocalCache, default_cache_path, resolve_cache
from halalquant.database._events import index_cached_filings, statements_to_filings
from halalquant.database._universe import (
    list_universe,
    map_universe_activity,
    members_between,
    sp500_stints,
)
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
DEFAULT_HISTORY_START = date(2010, 1, 1)
BENCHMARKS = ("SPY", "SPUS")


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
        start_date = DEFAULT_HISTORY_START if DEFAULT_HISTORY_START <= end_date else end_date

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
        current_symbols = validate_symbols(list(members["symbol"]))
        sector_map = {
            str(row["symbol"]): map_universe_activity(row.get("sector"), row.get("industry"))
            for _, row in members.iterrows()
        }
        symbols = list(current_symbols)
        stints = _load_stints(universe, log)
        if not stints.empty:
            store.replace_stints(stints, "sp500")
            window = members_between(stints, start_date, end_date)
            symbols = validate_symbols(list(dict.fromkeys([*current_symbols, *window["symbol"].astype(str)])))
            for _, row in window.iterrows():
                symbol = str(row["symbol"])
                if symbol not in sector_map or not sector_map.get(symbol):
                    mapped = map_universe_activity(row.get("sector"), row.get("industry"))
                    if mapped:
                        sector_map[symbol] = mapped
            log(
                f"Point-in-time {universe} window {start_date} → {end_date}: "
                f"{len(symbols)} names (daily constituent history, not CRSP)"
            )
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
    if tickers is None:
        live_rows = universe_rows[universe_rows["symbol"].isin(current_symbols)].copy()
        store.db.replace_universe(live_rows, universe)
    else:
        store.write_universe(universe_rows)

    work = kept if apply_sector_filter else symbols
    if not work:
        log("Nothing left after the sector screen.")
        return _summary_frame(universe_rows, store)

    price_start = (pd.Timestamp(start_date) - pd.Timedelta(days=lookback().days)).date()
    log(f"Caching prices {price_start} → {end_date}…")
    store.get_prices(_with_benchmarks(work), start=price_start, end=end_date, force_refresh=force_refresh)

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
    store.write_meta({f"dividends_through:{symbol}": end_date.isoformat() for symbol in work})
    index_cached_filings(store, work)

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
        start_date = DEFAULT_HISTORY_START
    members = store.read_universe()
    if members.empty:
        raise ValueError("No universe in the cache. Run prepare_dataset first.")
    work = list(members.loc[members["sector_allowed"], "symbol"])
    work = list(dict.fromkeys([*work, *_window_allowed(store, start_date, end_date)]))
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
        "schema_version": "0.4",
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


def refresh_dataset(
    as_of: Optional[DateLike] = None,
    tickers: Optional[Union[str, Sequence[str]]] = None,
    universe: str = "sp500",
    start: Optional[DateLike] = None,
    freq: Optional[str] = "ME",
    cache: Union[bool, str, LocalCache] = True,
    provider: Optional[BaseDataProvider] = None,
    filings: Optional[BaseDataProvider] = None,
    apply_sector_filter: bool = True,
    progress: Union[bool, ProgressFn] = True,
) -> pd.DataFrame:
    """
    Fill gaps since the last prepare. Does not rebuild the S&P 500 from scratch.

    Prices are appended (including SPY and SPUS). Dividends are fetched only
    past the last refresh. Companyfacts are re-downloaded only when the SEC
    submissions accession for that CIK changed and a new 10-Q/10-K is newer
    than the cache. Metric panels are rebuilt for those names and for any new
    calendar stamp.
    """
    log = _progress_logger(progress)
    as_of_text = None if as_of is None or str(as_of).strip().lower() == "today" else as_of
    _, as_of_date = validate_date_range(as_of_text, as_of_text)
    store = resolve_cache(cache, provider=provider, filings=filings)
    if store is None:
        store = LocalCache(provider=provider, filings=filings)

    meta = store.read_meta()
    if tickers is None and store.read_universe().empty and not meta.get("prepared_at"):
        log("Cache is empty; running prepare_dataset.")
        prepared = prepare_dataset(
            tickers=tickers,
            universe=universe,
            start=start or meta.get("start") or DEFAULT_HISTORY_START,
            end=as_of_date,
            freq=freq,
            cache=store,
            provider=provider,
            filings=filings,
            apply_sector_filter=apply_sector_filter,
            progress=progress,
        )
        return _refresh_view(prepared, store)

    if tickers is None:
        symbols = _refresh_live_symbols(store, universe, apply_sector_filter, log)
        _refresh_stints(store, universe, log)
    else:
        universe = "custom"
        symbols = validate_symbols(tickers)
    if not symbols:
        log("No symbols to refresh.")
        return pd.DataFrame(columns=["symbol", "n_new_bars", "n_new_filings", "n_new_dividends", "facts_redownloaded"])

    history_start = _history_start(start, meta, as_of_date)
    price_start = (pd.Timestamp(history_start) - pd.Timedelta(days=lookback().days)).date()
    price_symbols = _with_benchmarks(symbols)
    log(f"Appending prices through {as_of_date} ({len(price_symbols)} names, including SPY and SPUS)…")
    new_bars = store.append_prices(price_symbols, start=price_start, end=as_of_date)
    log("Appending dividends…")
    new_divs = store.append_dividends(symbols, start=history_start, end=as_of_date)

    log("Checking SEC submissions for new 10-Q/10-K filings…")
    changed, submission_rows = _filings_needing_facts(store, symbols, log)
    before_filings = _filing_counts(store, symbols)
    if changed:
        log(f"Re-downloading companyfacts for {len(changed)} names…")
        invalidate = getattr(store.filings, "invalidate_facts", None)
        if callable(invalidate):
            invalidate(changed)
        _chunked_fetch(
            changed,
            25,
            "filings",
            log,
            lambda chunk: store.get_balance_sheet(chunk, as_of=None, force_refresh=True),
        )
        _chunked_fetch(
            changed,
            25,
            "income",
            log,
            lambda chunk: store.get_income_statement(chunk, as_of=None, force_refresh=True),
        )
    if submission_rows is not None and not submission_rows.empty:
        store.write_filings(statements_to_filings(submission_rows))
    index_cached_filings(store, symbols)
    after_filings = _filing_counts(store, symbols)

    _refresh_metrics(store, symbols, changed, history_start, as_of_date, freq, log)
    snapshot = normalize_freq(freq) if freq else "annual"
    store.write_meta(
        {
            **_meta(universe, history_start, as_of_date, snapshot, symbols),
            "refreshed_at": date.today().isoformat(),
        }
    )
    rows = []
    for symbol in list(dict.fromkeys([*symbols, *BENCHMARKS])):
        rows.append(
            {
                "symbol": symbol,
                "n_new_bars": int(new_bars.get(symbol, 0)),
                "n_new_filings": max(0, after_filings.get(symbol, 0) - before_filings.get(symbol, 0)),
                "n_new_dividends": int(new_divs.get(symbol, 0)),
                "facts_redownloaded": int(symbol in changed),
            }
        )
    log(f"Refresh wrote cache → {store.db.path}")
    return pd.DataFrame(rows)


def _progress_logger(progress: Union[bool, ProgressFn]) -> ProgressFn:
    if callable(progress):
        return progress
    if progress:
        return lambda msg: print(f"[halalquant] {msg}", flush=True)
    return lambda msg: None


def _with_benchmarks(symbols: Sequence[str]) -> list[str]:
    out = list(symbols)
    for symbol in BENCHMARKS:
        if symbol not in out:
            out.append(symbol)
    return out


def _load_stints(universe: str, log: ProgressFn) -> pd.DataFrame:
    key = str(universe).strip().lower()
    if key not in {"sp500", "s&p500", "s&p 500"}:
        return pd.DataFrame()
    try:
        return sp500_stints()
    except (OSError, ValueError) as exc:
        log(f"Point-in-time membership unavailable ({exc}); using the current list.")
        return pd.DataFrame()


def _refresh_stints(store: LocalCache, universe: str, log: ProgressFn) -> None:
    """Re-download membership once a week. A failed download keeps the cached stints."""
    meta = store.read_meta()
    stamp = meta.get(f"stints_at:{universe}")
    if stamp and not store.read_stints(universe).empty:
        try:
            if (date.today() - pd.Timestamp(stamp).date()).days < 7:
                return
        except (TypeError, ValueError):
            pass
    stints = _load_stints(universe, log)
    if stints.empty:
        return
    store.replace_stints(stints, universe)
    store.write_meta({f"stints_at:{universe}": date.today().isoformat()})
    log(f"Stored {len(stints)} {universe} membership stints.")


def _window_allowed(store: LocalCache, start: date, end: date, universe: str = "sp500") -> list[str]:
    stints = store.read_stints(universe)
    if stints is None or stints.empty:
        return []
    window = members_between(stints, start, end)
    sectors = store.db.read_sector_map(list(window["symbol"]))
    sector_filter = SectorFilter()
    return [
        str(symbol)
        for symbol in window["symbol"]
        if sectors.get(str(symbol)) and sector_filter.is_sector_allowed(sectors[str(symbol)])
    ]


def backfill_universe(
    start: Optional[DateLike] = None,
    end: Optional[DateLike] = None,
    universe: str = "sp500",
    freq: Optional[str] = "ME",
    cache: Union[bool, str, LocalCache] = True,
    provider: Optional[BaseDataProvider] = None,
    filings: Optional[BaseDataProvider] = None,
    progress: Union[bool, ProgressFn] = True,
) -> pd.DataFrame:
    """
    Fetch facts for index members in ``[start, end]`` that the cache lacks.

    Use after ``prepare_dataset`` ran on the current list only. Leavers that
    no longer trade often have no Yahoo prices and no SEC ticker mapping; the
    summary shows which names are still empty.
    """
    log = _progress_logger(progress)
    start_date, end_date = validate_date_range(start, end)
    store = resolve_cache(cache, provider=provider, filings=filings)
    if store is None:
        store = LocalCache(provider=provider, filings=filings)
    stints = _load_stints(universe, log)
    if stints.empty:
        stints = store.read_stints(universe)
    else:
        store.replace_stints(stints, universe)
        store.write_meta({f"stints_at:{universe}": date.today().isoformat()})
    if stints is None or stints.empty:
        raise ValueError("No membership stints. Check network access to the constituent history.")
    window = members_between(stints, start_date, end_date)
    symbols = validate_symbols(list(window["symbol"].astype(str)))
    priced = set(store.db.read_price_dates(symbols)["symbol"].astype(str))
    missing = [s for s in symbols if s not in priced]
    log(f"{len(symbols)} {universe} members in window, {len(missing)} without cached prices")

    known = store.db.read_sector_map(symbols)
    unlabeled = [s for s in symbols if not known.get(s)]
    if unlabeled:
        log(f"Filling {len(unlabeled)} sector labels from Yahoo…")
        labels = store.get_sector_map(unlabeled, force_refresh=False)
        if labels:
            store.db.write_sector_map(labels)
    allowed = set(_window_allowed(store, start_date, end_date, universe))
    work = [s for s in missing if s in allowed]
    log(f"{len(work)} missing names pass the activity screen")
    if work:
        price_start = (pd.Timestamp(start_date) - pd.Timedelta(days=lookback().days)).date()
        store.get_prices(work, start=price_start, end=end_date, force_refresh=False)
        _chunked_fetch(work, 25, "filings", log, lambda chunk: store.get_balance_sheet(chunk, as_of=None, force_refresh=False))
        _chunked_fetch(work, 25, "income", log, lambda chunk: store.get_income_statement(chunk, as_of=None, force_refresh=False))
        _chunked_fetch(
            work,
            40,
            "dividends",
            log,
            lambda chunk: store.get_dividends(chunk, start=start_date, end=end_date, force_refresh=False),
        )
        index_cached_filings(store, work)
        balance = store.get_balance_sheet(work, as_of=None, force_refresh=False)
        income = store.get_income_statement(work, as_of=None, force_refresh=False)
        if not balance.empty:
            px_start, px_end = price_window_for_fundamentals(balance, as_of=end_date)
            prices = store.get_prices(work, start=px_start, end=px_end, force_refresh=False)
            _write_metric_panels(store, balance, income, prices, start_date, end_date, freq, log)

    rows = pd.DataFrame({"universe": universe, "symbol": symbols})
    rows["sector"] = rows["symbol"].map(store.db.read_sector_map(symbols))
    rows["sector_allowed"] = rows["symbol"].isin(allowed)
    return _summary_frame(rows, store)


def _history_start(start: Optional[DateLike], meta: dict, as_of_date: date) -> date:
    if start is not None:
        start_date, _ = validate_date_range(start, as_of_date)
        return start_date
    cached = meta.get("start")
    if cached:
        try:
            return pd.Timestamp(cached).date()
        except (TypeError, ValueError):
            pass
    return DEFAULT_HISTORY_START if DEFAULT_HISTORY_START <= as_of_date else as_of_date


def _refresh_live_symbols(store: LocalCache, universe: str, apply_sector_filter: bool, log: ProgressFn) -> list[str]:
    try:
        current = list_universe(universe)
    except (OSError, ValueError) as exc:
        log(f"Could not refresh the live {universe} list ({exc}); using the cache.")
        current = store.read_universe(universe)
    if current is None or current.empty:
        return []
    sector_map = {}
    if "sector" in current.columns:
        for _, row in current.iterrows():
            sector_map[str(row["symbol"])] = map_universe_activity(row.get("sector"), row.get("industry"))
    symbols = validate_symbols(list(current["symbol"]))
    if apply_sector_filter and sector_map:
        symbols = SectorFilter().filter_symbols(symbols, sector_map=sector_map)
    rows = pd.DataFrame(
        {
            "universe": universe,
            "symbol": validate_symbols(list(current["symbol"])),
            "sector": [sector_map.get(symbol) for symbol in validate_symbols(list(current["symbol"]))],
            "sector_allowed": [
                symbol in symbols for symbol in validate_symbols(list(current["symbol"]))
            ],
        }
    )
    store.db.replace_universe(rows, universe)
    if sector_map:
        store.db.write_sector_map(sector_map)
    return symbols


def _filing_counts(store: LocalCache, symbols: Sequence[str]) -> dict[str, int]:
    frame = store.read_filings(list(symbols))
    if frame is None or frame.empty:
        return {}
    return {str(symbol): int(count) for symbol, count in frame.groupby(frame["symbol"].astype(str)).size().items()}


def _filings_needing_facts(
    store: LocalCache,
    symbols: Sequence[str],
    log: ProgressFn,
) -> tuple[list[str], pd.DataFrame]:
    """
    Names whose submissions accession grew and whose newest 10-Q/10-K is not
    in the cache yet. Unchanged ETags and unchanged accessions do not
    re-download companyfacts.
    """
    balance = store.db.read_balance_sheets(list(symbols))
    known: dict[str, pd.Timestamp] = {}
    if not balance.empty:
        filed = pd.to_datetime(balance["filed_date"], errors="coerce")
        for symbol, idx in balance.groupby(balance["symbol"].astype(str)).groups.items():
            newest = filed.loc[idx].max()
            if pd.notna(newest):
                known[str(symbol)] = newest

    sec = store.filings if hasattr(store.filings, "submission_index") else getattr(store.filings, "sec", None)
    frames: list[pd.DataFrame] = []
    changed: list[str] = []
    if sec is not None and hasattr(sec, "submission_index") and hasattr(sec, "has_cik"):
        meta = store.read_meta()
        for symbol in symbols:
            try:
                if not sec.has_cik(symbol):
                    continue
            except (ValueError, OSError):
                continue
            etag = meta.get(f"submissions_etag:{symbol}")
            try:
                frame, accession, new_etag, not_modified = sec.submission_index(symbol, etag=etag)
            except (OSError, ValueError) as exc:
                log(f"submissions {symbol} failed: {type(exc).__name__}")
                continue
            updates: dict[str, str] = {}
            if new_etag:
                updates[f"submissions_etag:{symbol}"] = str(new_etag)
            if not_modified:
                if updates:
                    store.write_meta(updates)
                continue
            previous = meta.get(f"submissions_accession:{symbol}")
            if accession:
                updates[f"submissions_accession:{symbol}"] = str(accession)
            if updates:
                store.write_meta(updates)
            if frame is not None and not frame.empty:
                frames.append(frame)
            if accession and previous == accession:
                continue
            if _newer_than_cache(frame, known.get(symbol)):
                changed.append(symbol)
        return changed, _concat_frames(frames)

    recent = getattr(store.filings, "recent_filings", None)
    if not callable(recent):
        return [], pd.DataFrame()
    try:
        frame = recent(list(symbols))
    except TypeError:
        pieces = [recent(symbol) for symbol in symbols]
        frame = _concat_frames(pieces)
    if frame is None or frame.empty:
        return [], pd.DataFrame()
    for symbol, rows in frame.groupby(frame["symbol"].astype(str)):
        if _newer_than_cache(rows, known.get(str(symbol))):
            changed.append(str(symbol))
    return changed, frame


def _newer_than_cache(frame: Optional[pd.DataFrame], known: Optional[pd.Timestamp]) -> bool:
    if frame is None or frame.empty or "filed_date" not in frame.columns:
        return False
    newest = pd.to_datetime(frame["filed_date"], errors="coerce").max()
    if pd.isna(newest):
        return False
    if known is None or pd.isna(known):
        return True
    return bool(newest > known)


def _concat_frames(frames: Sequence[pd.DataFrame]) -> pd.DataFrame:
    usable = [frame for frame in frames if frame is not None and not frame.empty]
    if not usable:
        return pd.DataFrame()
    return pd.concat(usable, ignore_index=True)


def _refresh_metrics(
    store: LocalCache,
    symbols: Sequence[str],
    changed: Sequence[str],
    start: date,
    end: date,
    freq: Optional[str],
    log: ProgressFn,
) -> None:
    snapshot = normalize_freq(freq) if freq else None
    rebuild: dict[str, date] = {}
    if snapshot:
        existing = store.read_metrics(list(symbols), freq=snapshot)
        last_stamp = (
            pd.to_datetime(existing["as_of"], errors="coerce").max()
            if existing is not None and not existing.empty
            else pd.NaT
        )
        due = _latest_calendar_stamp(end, snapshot)
        if due is not None and (pd.isna(last_stamp) or last_stamp.date() < due):
            stamp_start = start if pd.isna(last_stamp) else (last_stamp + pd.Timedelta(days=1)).date()
            for symbol in symbols:
                rebuild[symbol] = stamp_start
    for symbol in changed:
        filed = _earliest_new_filing(store, symbol)
        day = filed or start
        rebuild[symbol] = min(rebuild.get(symbol, day), day)
    if not rebuild:
        log("Metrics already cover this as-of; skipped panel rebuild.")
        return
    groups: dict[date, list[str]] = {}
    for symbol, day in rebuild.items():
        groups.setdefault(day, []).append(symbol)
    for day, group in groups.items():
        log(f"Rebuilding metrics for {len(group)} names from {day}…")
        balance = store.get_balance_sheet(group, as_of=None, force_refresh=False)
        income = store.get_income_statement(group, as_of=None, force_refresh=False)
        if balance.empty:
            continue
        px_start, px_end = price_window_for_fundamentals(balance, as_of=end)
        prices = store.get_prices(group, start=px_start, end=px_end, force_refresh=False)
        _write_metric_panels(store, balance, income, prices, day, end, freq, log)


def _earliest_new_filing(store: LocalCache, symbol: str) -> Optional[date]:
    frame = store.read_filings([symbol])
    if frame is None or frame.empty:
        return None
    filed = pd.to_datetime(frame["filed_date"], errors="coerce").min()
    if pd.isna(filed):
        return None
    return filed.date()


def _latest_calendar_stamp(end: date, freq: str) -> Optional[date]:
    stamps = pd.date_range(end=end, periods=1, freq=freq)
    if len(stamps) == 0:
        return None
    stamp = stamps[-1].date()
    if stamp > end:
        return None
    return stamp


def _refresh_view(prepared: pd.DataFrame, store: LocalCache) -> pd.DataFrame:
    if prepared is None or prepared.empty:
        return pd.DataFrame(columns=["symbol", "n_new_bars", "n_new_filings", "n_new_dividends", "facts_redownloaded"])
    counts = _filing_counts(store, list(prepared["symbol"]))
    rows = []
    for _, row in prepared.iterrows():
        symbol = str(row["symbol"])
        rows.append(
            {
                "symbol": symbol,
                "n_new_bars": int(row.get("n_prices", 0) or 0),
                "n_new_filings": int(counts.get(symbol, 0)),
                "n_new_dividends": 0,
                "facts_redownloaded": 0,
            }
        )
    return pd.DataFrame(rows)
