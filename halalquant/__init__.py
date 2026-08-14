"""
halalquant — Shariah-compliant quant data engine.

Public entry points mirror a simple yfinance-style workflow:
fetch prices, screen the universe, and return strategy-ready frames.
"""

import sys
from types import ModuleType

from halalquant.base import BaseDataProvider, BaseScreener
from halalquant.api import download, get_halal_universe
from halalquant.database import LocalCache, default_cache_path
from halalquant.providers import FMPProvider

__all__ = [
    "BaseDataProvider",
    "BaseScreener",
    "FMPProvider",
    "LocalCache",
    "api_key",
    "default_cache_path",
    "download",
    "get_halal_universe",
]

__version__ = "0.1.0"


class _HalalQuantModule(ModuleType):
    """Allow ``import halalquant as hq; hq.api_key = '...'``."""

    @property
    def api_key(self):
        from halalquant.config import api_key as stored

        return stored

    @api_key.setter
    def api_key(self, value):
        import halalquant.config as config

        config.api_key = value


sys.modules[__name__].__class__ = _HalalQuantModule
