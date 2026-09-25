"""
halalquant — Shariah-compliant quant data engine.

Public entry points mirror a simple yfinance-style workflow:
fetch prices, screen the universe, and return strategy-ready frames.
"""

from halalquant.base import BaseDataProvider, BaseScreener
from halalquant.api import (
    compare_standards,
    coverage_report,
    coverage_summary,
    download,
    filing_events,
    get_financial_metrics,
    get_halal_universe,
    prepare_dataset,
    purify_dividends,
    refresh_dataset,
)
from halalquant.database import LocalCache, default_cache_path, default_parquet_dir
from halalquant.providers import FilingsProvider, SECEdgarProvider, YFinanceProvider
from halalquant.purification import Purifier

__all__ = [
    "BaseDataProvider",
    "BaseScreener",
    "Purifier",
    "FilingsProvider",
    "LocalCache",
    "SECEdgarProvider",
    "YFinanceProvider",
    "compare_standards",
    "coverage_report",
    "coverage_summary",
    "default_cache_path",
    "default_parquet_dir",
    "download",
    "filing_events",
    "get_financial_metrics",
    "get_halal_universe",
    "prepare_dataset",
    "purify_dividends",
    "refresh_dataset",
]

__version__ = "0.3.0"
