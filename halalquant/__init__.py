"""
halalquant — Shariah-compliant quant data engine.

Public entry points mirror a simple yfinance-style workflow:
fetch prices, screen the universe, and return strategy-ready frames.
"""

from halalquant.base import BaseDataProvider, BaseScreener
from halalquant.api import (
    compare_standards,
    download,
    get_financial_metrics,
    get_halal_universe,
    prepare_dataset,
    purify_dividends,
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
    "default_cache_path",
    "default_parquet_dir",
    "download",
    "get_financial_metrics",
    "get_halal_universe",
    "prepare_dataset",
    "purify_dividends",
]

__version__ = "0.1.0"
