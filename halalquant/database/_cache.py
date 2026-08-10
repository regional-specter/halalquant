"""Local cache helpers: DuckDB path defaults and cache-before-fetch."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Sequence, Union

import pandas as pd

from halalquant.base import BaseDataProvider, DateLike
from halalquant.database._duckdb_driver import DuckDBDriver
from halalquant.utils._pit_adjustments import as_of_filter


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
    return Path.home() / ".halalquant" / "cache.duckdb"


def default_parquet_dir() -> Path:
    env = os.getenv("HALALQUANT_PARQUET_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".halalquant" / "parquet"


class LocalCache:
    """
    Cache-before-fetch layer over a ``BaseDataProvider``.

    Prices and balance sheets are read from DuckDB first; missing symbols are
    fetched from the upstream provider, written back, and optionally mirrored
    to Parquet.
    """

    def __init__(
        self,
        provider: BaseDataProvider,
        path: Optional[Union[str, Path]] = None,
        parquet_dir: Optional[Union[str, Path]] = None,
        mirror_parquet: bool = True,
    ) -> None:
        self.provider = provider
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
            cached = pd.DataFrame()
        else:
            cached = self.db.read_prices(symbols_list, start=start_s, end=end_s)
            covered = set(cached["symbol"].unique()) if not cached.empty else set()
            missing = [s for s in symbols_list if s not in covered]

        if missing:
            fresh = self.provider.get_prices(missing, start=start, end=end)
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

        if force_refresh:
            missing = symbols_list
        else:
            cached_all = self.db.read_balance_sheets(symbols_list)
            covered = set(cached_all["symbol"].unique()) if not cached_all.empty else set()
            missing = [s for s in symbols_list if s not in covered]

        if missing:
            fresh = self.provider.get_balance_sheet(missing, as_of=None)
            self.db.write_balance_sheets(fresh)
            self._maybe_mirror("balance_sheets", self.db.read_balance_sheets())

        frame = self.db.read_balance_sheets(symbols_list)
        if as_of is not None and not frame.empty:
            frame = as_of_filter(frame, as_of=str(as_of)[:10])
        return frame

    def write_compliance(self, frame: pd.DataFrame) -> None:
        self.db.write_compliance(frame)
        if self.mirror_parquet:
            self._maybe_mirror("compliance_flags", self.db.read_compliance())

    def export_parquet(self, table: str, path: Optional[Union[str, Path]] = None) -> Path:
        frame = self.db.read_table(table)
        target = Path(path) if path else self.parquet_dir / f"{table}.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(target, index=False)
        return target

    def import_parquet(self, table: str, path: Optional[Union[str, Path]] = None) -> int:
        source = Path(path) if path else self.parquet_dir / f"{table}.parquet"
        if not source.exists():
            return 0
        frame = pd.read_parquet(source)
        if table == "prices":
            self.db.write_prices(frame)
        elif table == "balance_sheets":
            self.db.write_balance_sheets(frame)
        elif table == "compliance_flags":
            self.db.write_compliance(frame)
        else:
            raise ValueError(f"Unknown table: {table}")
        return len(frame)

    def _maybe_mirror(self, table: str, frame: pd.DataFrame) -> None:
        if not self.mirror_parquet or frame.empty:
            return
        self.parquet_dir.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(self.parquet_dir / f"{table}.parquet", index=False)

    def close(self) -> None:
        self.db.close()
