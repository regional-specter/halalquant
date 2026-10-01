"""Local caching and point-in-time storage."""

from halalquant.database._cache import (
    CacheBackedProvider,
    LocalCache,
    default_cache_path,
    default_data_dir,
    default_facts_dir,
    default_parquet_dir,
    resolve_cache,
)
from halalquant.database._coverage import coverage_report, coverage_summary
from halalquant.database._dataset import backfill_universe, prepare_dataset, refresh_dataset
from halalquant.database._duckdb_driver import DuckDBDriver
from halalquant.database._events import filing_events
from halalquant.database._models import SCHEMA_SQL
from halalquant.database._quality import dividend_quality, impure_ratio_flags
from halalquant.database._universe import (
    TICKER_RENAMES,
    list_universe,
    members_as_of,
    sp500_constituents,
    sp500_history,
    sp500_stints,
    stints_from_snapshots,
    yahoo_symbol,
)

__all__ = [
    "CacheBackedProvider",
    "DuckDBDriver",
    "LocalCache",
    "SCHEMA_SQL",
    "TICKER_RENAMES",
    "backfill_universe",
    "dividend_quality",
    "impure_ratio_flags",
    "members_as_of",
    "sp500_history",
    "sp500_stints",
    "stints_from_snapshots",
    "default_cache_path",
    "default_data_dir",
    "default_facts_dir",
    "default_parquet_dir",
    "coverage_report",
    "coverage_summary",
    "filing_events",
    "list_universe",
    "prepare_dataset",
    "refresh_dataset",
    "resolve_cache",
    "sp500_constituents",
    "yahoo_symbol",
]
