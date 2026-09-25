"""Local cache helpers: DuckDB path defaults and cache-before-fetch."""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, Sequence, Union

import pandas as pd

from halalquant.base import BaseDataProvider, DateLike
from halalquant.database._duckdb_driver import DuckDBDriver
from halalquant.database._gaps import price_gap_windows
from halalquant.utils._pit_adjustments import as_of_filter

_PRICE_CHUNK = 40
_DATE_SLACK_DAYS = 7


def default_data_dir() -> Path:
    env = os.getenv("HALALQUANT_DATA_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".halalquant"


def default_cache_path() -> Path:
    """
    Resolve the on-disk DuckDB cache location.

    Order:
    1. ``HALALQUANT_CACHE`` env var (file path)
    2. ``~/.halalquant/cache.duckdb``
    """
    env = os.getenv("HALALQUANT_CACHE")
    if env:
        return Path(env).expanduser()
    return default_data_dir() / "cache.duckdb"


def default_parquet_dir() -> Path:
    env = os.getenv("HALALQUANT_PARQUET_DIR")
    if env:
        return Path(env).expanduser()
    return default_data_dir() / "parquet"


def default_facts_dir() -> Path:
    env = os.getenv("HALALQUANT_SEC_FACTS_DIR")
    if env:
        return Path(env).expanduser()
    return default_data_dir() / "sec_facts"


def env_use_cache() -> bool:
    return os.getenv("HALALQUANT_USE_CACHE", "").strip().lower() in {"1", "true", "yes", "on"}


class LocalCache:
    """
    Cache-before-fetch layer over market and filings providers.

    Prices, statements, dividends, sector labels, and the prepared AAOIFI
    metrics panel are read from DuckDB first; missing symbols are fetched
    from the upstream providers, written back, and optionally mirrored to
    Parquet under ``~/.halalquant/``.
    """

    def __init__(
        self,
        provider: Optional[BaseDataProvider] = None,
        path: Optional[Union[str, Path]] = None,
        parquet_dir: Optional[Union[str, Path]] = None,
        mirror_parquet: bool = True,
        filings: Optional[BaseDataProvider] = None,
    ) -> None:
        if provider is None or filings is None:
            from halalquant.providers import FilingsProvider, YFinanceProvider

            if provider is None:
                provider = YFinanceProvider()
            if filings is None:
                filings = FilingsProvider()
        self.market = provider
        self.filings = filings
        # Back-compat alias used by older LocalCache(provider=...) callers.
        self.provider = self.market
        cache_path = Path(path) if path is not None else default_cache_path()
        if str(cache_path) != ":memory:":
            cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = DuckDBDriver(cache_path)
        self.parquet_dir = Path(parquet_dir) if parquet_dir else default_parquet_dir()
        self.mirror_parquet = mirror_parquet

    def get_prices(
        self,
        symbols: Sequence[str],
        start: DateLike,
        end: DateLike,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        symbols_list = list(symbols)
        start_s = str(start)[:10]
        end_s = str(end)[:10]

        if force_refresh:
            missing = symbols_list
        else:
            missing = self._missing_price_symbols(symbols_list, start_s, end_s)

        if missing:
            frames = []
            for chunk in _chunks(missing, _PRICE_CHUNK):
                frames.append(self.market.get_prices(chunk, start=start, end=end))
            fresh = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            self.db.write_prices(fresh)
            self._maybe_mirror("prices", self.db.read_prices())

        return self.db.read_prices(symbols_list, start=start_s, end=end_s)

    def get_balance_sheet(
        self,
        symbols: Sequence[str],
        as_of: Optional[DateLike] = None,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        symbols_list = list(symbols)
        missing = symbols_list if force_refresh else self._missing_statement_symbols(
            symbols_list, "balance_sheets"
        )
        if missing:
            fresh = self.filings.get_balance_sheet(missing, as_of=None)
            self.db.write_balance_sheets(fresh)
            self.mark_fetched("balance_sheets", missing)
            self._maybe_mirror("balance_sheets", self.db.read_balance_sheets())

        frame = self.db.read_balance_sheets(symbols_list)
        if as_of is not None and not frame.empty:
            frame = as_of_filter(frame, as_of=str(as_of)[:10])
        return frame

    def get_income_statement(
        self,
        symbols: Sequence[str],
        as_of: Optional[DateLike] = None,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        symbols_list = list(symbols)
        missing = symbols_list if force_refresh else self._missing_statement_symbols(
            symbols_list, "income_statements"
        )
        if missing:
            fresh = self.filings.get_income_statement(missing, as_of=None)
            self.db.write_income_statements(fresh)
            self.mark_fetched("income_statements", missing)
            self._maybe_mirror("income_statements", self.db.read_income_statements())

        frame = self.db.read_income_statements(symbols_list)
        if as_of is not None and not frame.empty:
            frame = as_of_filter(frame, as_of=str(as_of)[:10])
        return frame

    def get_dividends(
        self,
        symbols: Sequence[str],
        start: Optional[DateLike] = None,
        end: Optional[DateLike] = None,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        symbols_list = list(symbols)
        start_s = str(start)[:10] if start is not None else None
        end_s = str(end)[:10] if end is not None else None
        getter = getattr(self.market, "get_dividends", None)
        if force_refresh:
            missing = symbols_list
        else:
            missing = self._missing_dividend_symbols(symbols_list, start_s, end_s)

        if missing and callable(getter):
            fresh = getter(missing, start=start, end=end)
            self.db.write_dividends(fresh)
            self.mark_fetched("dividends", missing)
            self._maybe_mirror("dividends", self.db.read_dividends())

        return self.db.read_dividends(symbols_list, start=start_s, end=end_s)

    def get_sector_map(
        self,
        symbols: Sequence[str],
        force_refresh: bool = False,
    ) -> dict[str, str]:
        symbols_list = list(symbols)
        cached = {} if force_refresh else self.db.read_sector_map(symbols_list)
        missing = [s for s in symbols_list if s not in cached]
        getter = getattr(self.market, "get_sector_map", None)
        if missing and callable(getter):
            try:
                fresh = getter(missing) or {}
            except (ValueError, OSError):
                fresh = {}
            if fresh:
                self.db.write_sector_map(fresh)
                self._maybe_mirror("sector_map", self.db.read_table("sector_map"))
                cached.update(fresh)
        return {s: cached[s] for s in symbols_list if s in cached}

    def read_metrics(
        self,
        symbols: Sequence[str],
        start: Optional[DateLike] = None,
        end: Optional[DateLike] = None,
        freq: str = "annual",
    ) -> pd.DataFrame:
        return self.db.read_metrics(
            list(symbols),
            start=str(start)[:10] if start is not None else None,
            end=str(end)[:10] if end is not None else None,
            freq=freq,
        )

    def write_metrics(self, frame: pd.DataFrame, freq: str = "annual") -> None:
        if frame.empty:
            return
        prepared = frame.copy()
        prepared["freq"] = freq
        self.db.write_metrics(prepared)
        self._maybe_mirror("financial_metrics", self.db.read_table("financial_metrics"))

    def write_compliance(self, frame: pd.DataFrame) -> None:
        self.db.write_compliance(frame)
        if self.mirror_parquet:
            self._maybe_mirror("compliance_flags", self.db.read_compliance())

    def write_universe(self, frame: pd.DataFrame) -> None:
        self.db.write_universe(frame)
        self._maybe_mirror("universe_members", self.db.read_universe())

    def read_universe(self, universe: Optional[str] = None) -> pd.DataFrame:
        return self.db.read_universe(universe)

    def write_meta(self, items: dict[str, str]) -> None:
        self.db.write_meta(items)

    def read_meta(self) -> dict[str, str]:
        return self.db.read_meta()

    def export_parquet(
        self,
        table: str,
        path: Optional[Union[str, Path]] = None,
        partition_by_year: bool = False,
    ) -> Path:
        frame = self.db.read_table(table)
        if partition_by_year and table == "prices":
            root = Path(path) if path else self.parquet_dir / "prices"
            if frame.empty:
                root.mkdir(parents=True, exist_ok=True)
                return root
            years = pd.to_datetime(frame["date"], errors="coerce").dt.year
            for year, part in frame.groupby(years):
                if pd.isna(year):
                    continue
                dest = root / f"year={int(year)}" / "part.parquet"
                dest.parent.mkdir(parents=True, exist_ok=True)
                part.to_parquet(dest, index=False)
            return root
        target = Path(path) if path else self.parquet_dir / f"{table}.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(target, index=False)
        return target

    def import_parquet(self, table: str, path: Optional[Union[str, Path]] = None) -> int:
        source = Path(path) if path else self.parquet_dir / f"{table}.parquet"
        if not source.exists():
            return 0
        frame = pd.read_parquet(source)
        writers = {
            "prices": self.db.write_prices,
            "balance_sheets": self.db.write_balance_sheets,
            "income_statements": self.db.write_income_statements,
            "dividends": self.db.write_dividends,
            "financial_metrics": self.db.write_metrics,
            "compliance_flags": self.db.write_compliance,
            "universe_members": self.db.write_universe,
            "filings": self.db.write_filings,
            "universe_stints": self.db.write_stints,
        }
        if table == "sector_map":
            mapping = {
                str(row["symbol"]): str(row["sector"])
                for _, row in frame.iterrows()
                if pd.notna(row.get("sector"))
            }
            self.db.write_sector_map(mapping)
            return len(mapping)
        writer = writers.get(table)
        if writer is None:
            raise ValueError(f"Unknown table: {table}")
        writer(frame)
        return len(frame)

    def _missing_price_symbols(self, symbols: list[str], start: str, end: str) -> list[str]:
        coverage = self.db.price_coverage(symbols)
        by_symbol = coverage.set_index("symbol") if not coverage.empty else pd.DataFrame()
        slack = pd.Timedelta(days=_DATE_SLACK_DAYS)
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        missing: list[str] = []
        for symbol in symbols:
            if symbol not in by_symbol.index:
                missing.append(symbol)
                continue
            row = by_symbol.loc[symbol]
            min_date = pd.Timestamp(row["min_date"])
            max_date = pd.Timestamp(row["max_date"])
            if min_date > start_ts + slack or max_date < end_ts - slack:
                missing.append(symbol)
        return missing

    def _missing_dividend_symbols(
        self,
        symbols: list[str],
        start: Optional[str],
        end: Optional[str],
    ) -> list[str]:
        cached = self.db.read_dividends(symbols, start=start, end=end)
        covered = set(cached["symbol"].unique()) if not cached.empty else set()
        # A name with no dividends is still "covered" once we have attempted a
        # fetch — we store nothing. Re-fetch only symbols never seen in the
        # unfiltered dividend table.
        ever = self.db.read_dividends(symbols)
        seen = set(ever["symbol"].unique()) if not ever.empty else set()
        meta = self.db.read_meta()
        attempted = {
            s for s in symbols if meta.get(f"dividends_fetched:{s}") == "1"
        }
        missing = [s for s in symbols if s not in seen and s not in attempted]
        if start or end:
            # Range filter is applied on read; empty-in-range is not a miss
            # when the symbol was fetched at least once.
            return missing
        return [s for s in symbols if s not in covered and s not in attempted]

    def _missing_statement_symbols(self, symbols: list[str], table: str) -> list[str]:
        if table == "balance_sheets":
            cached = self.db.read_balance_sheets(symbols)
        else:
            cached = self.db.read_income_statements(symbols)
        covered = set(cached["symbol"].unique()) if not cached.empty else set()
        meta = self.db.read_meta()
        attempted = {s for s in symbols if meta.get(f"{table}_fetched:{s}") == "1"}
        missing = [s for s in symbols if s not in covered and s not in attempted]
        return missing

    def append_prices(
        self,
        symbols: Sequence[str],
        start: DateLike,
        end: DateLike,
    ) -> dict[str, int]:
        """Fetch only missing price windows. Returns new bar counts per symbol."""
        symbols_list = list(dict.fromkeys(symbols))
        start_d = pd.Timestamp(start).date()
        end_d = pd.Timestamp(end).date()
        before = self._row_counts(self.db.price_coverage(symbols_list))
        cached = self.db.read_price_dates(symbols_list)
        by_symbol = (
            {symbol: frame["date"].tolist() for symbol, frame in cached.groupby("symbol")}
            if not cached.empty
            else {}
        )
        batches: dict[tuple[date, date], list[str]] = {}
        meta = self.read_meta()
        for symbol in symbols_list:
            for window in price_gap_windows(by_symbol.get(symbol, []), start_d, end_d):
                key = f"prices_gap_empty:{symbol}:{window[0].isoformat()}:{window[1].isoformat()}"
                if meta.get(key) == "1":
                    continue
                batches.setdefault(window, []).append(symbol)

        for (window_start, window_end), window_symbols in batches.items():
            frames = []
            for chunk in _chunks(window_symbols, _PRICE_CHUNK):
                frames.append(self.market.get_prices(chunk, start=window_start, end=window_end))
            fresh = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            self.db.write_prices(fresh)
            if fresh is None or fresh.empty:
                if window_end < date.today():
                    self.db.write_meta(
                        {
                            f"prices_gap_empty:{symbol}:{window_start.isoformat()}:{window_end.isoformat()}": "1"
                            for symbol in window_symbols
                        }
                    )
                continue
            got = set(fresh["symbol"].astype(str)) if "symbol" in fresh.columns else set()
            if window_end < date.today():
                missed = [symbol for symbol in window_symbols if symbol not in got]
                if missed:
                    self.db.write_meta(
                        {
                            f"prices_gap_empty:{symbol}:{window_start.isoformat()}:{window_end.isoformat()}": "1"
                            for symbol in missed
                        }
                    )
        self._maybe_mirror("prices", self.db.read_prices())
        after = self._row_counts(self.db.price_coverage(symbols_list))
        return {symbol: max(0, after.get(symbol, 0) - before.get(symbol, 0)) for symbol in symbols_list}

    def append_dividends(
        self,
        symbols: Sequence[str],
        start: DateLike,
        end: DateLike,
    ) -> dict[str, int]:
        """Fetch dividends only past the last successful refresh for each symbol."""
        symbols_list = list(dict.fromkeys(symbols))
        end_d = pd.Timestamp(end).date()
        start_d = pd.Timestamp(start).date()
        meta = self.read_meta()
        before = self._dividend_counts(symbols_list)
        groups: dict[date, list[str]] = {}
        for symbol in symbols_list:
            through = meta.get(f"dividends_through:{symbol}")
            if through:
                through_d = pd.Timestamp(through).date()
                if through_d >= end_d:
                    continue
                fetch_start = through_d + timedelta(days=1)
            else:
                fetch_start = start_d
            if fetch_start > end_d:
                continue
            groups.setdefault(fetch_start, []).append(symbol)

        getter = getattr(self.market, "get_dividends", None)
        if callable(getter):
            for fetch_start, group in groups.items():
                fresh = getter(group, start=fetch_start, end=end_d)
                self.db.write_dividends(fresh)
                self.mark_fetched("dividends", group)
                self.db.write_meta({f"dividends_through:{symbol}": end_d.isoformat() for symbol in group})
            if groups:
                self._maybe_mirror("dividends", self.db.read_dividends())
        after = self._dividend_counts(symbols_list)
        return {symbol: max(0, after.get(symbol, 0) - before.get(symbol, 0)) for symbol in symbols_list}

    def write_filings(self, frame: pd.DataFrame) -> None:
        self.db.write_filings(frame)
        self._maybe_mirror("filings", self.db.read_filings())

    def read_filings(self, symbols=None, since=None, as_of=None) -> pd.DataFrame:
        return self.db.read_filings(
            list(symbols) if symbols is not None else None,
            since=str(since)[:10] if since is not None else None,
            as_of=str(as_of)[:10] if as_of is not None else None,
        )

    def write_stints(self, frame: pd.DataFrame) -> None:
        self.db.write_stints(frame)
        self._maybe_mirror("universe_stints", self.db.read_stints())

    def read_stints(self, universe: Optional[str] = None) -> pd.DataFrame:
        return self.db.read_stints(universe)

    def mark_fetched(self, table: str, symbols: Sequence[str]) -> None:
        self.db.write_meta({f"{table}_fetched:{s}": "1" for s in symbols})

    def _maybe_mirror(self, table: str, frame: pd.DataFrame) -> None:
        if not self.mirror_parquet or frame is None or frame.empty:
            return
        try:
            self.parquet_dir.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(self.parquet_dir / f"{table}.parquet", index=False)
        except ImportError:
            return

    def close(self) -> None:
        self.db.close()

    @staticmethod
    def _row_counts(coverage: pd.DataFrame) -> dict[str, int]:
        if coverage is None or coverage.empty:
            return {}
        return {str(row["symbol"]): int(row["n_rows"]) for _, row in coverage.iterrows()}

    def _dividend_counts(self, symbols: Sequence[str]) -> dict[str, int]:
        cached = self.db.read_dividends(list(symbols))
        if cached.empty:
            return {}
        return {str(symbol): int(count) for symbol, count in cached.groupby("symbol").size().items()}


class CacheBackedProvider(BaseDataProvider):
    """``BaseDataProvider`` that reads ``LocalCache`` before hitting the network."""

    def __init__(self, cache: LocalCache, force_refresh: bool = False) -> None:
        self.cache = cache
        self.force_refresh = force_refresh

    def get_prices(
        self,
        symbols: Sequence[str],
        start: DateLike,
        end: DateLike,
    ) -> pd.DataFrame:
        return self.cache.get_prices(
            symbols, start=start, end=end, force_refresh=self.force_refresh
        )

    def get_balance_sheet(
        self,
        symbols: Sequence[str],
        as_of: Optional[DateLike] = None,
    ) -> pd.DataFrame:
        return self.cache.get_balance_sheet(
            symbols, as_of=as_of, force_refresh=self.force_refresh
        )

    def get_income_statement(
        self,
        symbols: Sequence[str],
        as_of: Optional[DateLike] = None,
    ) -> pd.DataFrame:
        return self.cache.get_income_statement(
            symbols, as_of=as_of, force_refresh=self.force_refresh
        )

    def get_dividends(
        self,
        symbols: Sequence[str],
        start: Optional[DateLike] = None,
        end: Optional[DateLike] = None,
    ) -> pd.DataFrame:
        return self.cache.get_dividends(
            symbols, start=start, end=end, force_refresh=self.force_refresh
        )

    def get_sector_map(self, symbols: Sequence[str]) -> dict[str, str]:
        return self.cache.get_sector_map(symbols, force_refresh=self.force_refresh)


CacheLike = Union[bool, str, Path, LocalCache, None]


def resolve_cache(
    cache: CacheLike,
    provider: Optional[BaseDataProvider] = None,
    filings: Optional[BaseDataProvider] = None,
) -> Optional[LocalCache]:
    """
    Interpret the public ``cache=`` argument.

    ``False`` / omitted (and no ``HALALQUANT_USE_CACHE``): fetch-only.
    ``True``: default DuckDB path.
    A path or ``LocalCache`` instance is used as-is.
    """
    if cache is False:
        return None
    if cache is None:
        if not env_use_cache():
            return None
        cache = True
    if isinstance(cache, LocalCache):
        if provider is not None:
            cache.market = provider
            cache.provider = provider
        if filings is not None:
            cache.filings = filings
        return cache
    if cache is True:
        return LocalCache(provider=provider, filings=filings)
    return LocalCache(provider=provider, filings=filings, path=cache)


def _chunks(values: Sequence[str], size: int) -> list[list[str]]:
    return [list(values[i : i + size]) for i in range(0, len(values), size)]
