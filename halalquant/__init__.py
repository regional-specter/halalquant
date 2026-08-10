"""
halalquant — Shariah-compliant quant data engine.

Public entry points mirror a simple yfinance-style workflow:
fetch prices, screen the universe, and return strategy-ready frames.
"""

from halalquant.base import BaseDataProvider, BaseScreener
from halalquant.api import download, get_halal_universe

__all__ = [
    "BaseDataProvider",
    "BaseScreener",
    "download",
    "get_halal_universe",
]

__version__ = "0.1.0"
