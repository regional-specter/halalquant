"""
halalquant — Shariah-compliant quant data engine.

Public entry points mirror a simple yfinance-style workflow:
fetch prices, screen the universe, and return strategy-ready frames.
"""

from halalquant.base import BaseDataProvider, BaseScreener
from halalquant.api import download, get_halal_universe, purify_dividends
from halalquant.providers import SECEdgarProvider, YFinanceProvider
from halalquant.purification import Purifier

__all__ = [
    "BaseDataProvider",
    "BaseScreener",
    "Purifier",
    "SECEdgarProvider",
    "YFinanceProvider",
    "download",
    "get_halal_universe",
    "purify_dividends",
]

__version__ = "0.1.0"
