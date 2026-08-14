"""Unified strategy-facing API (yfinance-like surface)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional, Sequence, Union

import pandas as pd

from halalquant.database._cache import LocalCache, default_cache_path
from halalquant.providers._fmp import FMPProvider
from halalquant.screening._aaoifi import AAOIFIScreener
from halalquant.screening._sector_filter import SectorFilter
from halalquant.utils.validation import validate_date_range, validate_symbols

DateLikeInput = Union[str, date]


def _build_cache(
    provider: FMPProvider,
    cache: Optional[LocalCache] = None,
    cache_path: Optional[Union[str, Path]] = None,
    use_cache: bool = True,
) -> Optional[LocalCache]:
    if not use_cache:
        return None
    if cache is not None:
        return cache
    return LocalCache(provider, path=cache_path or default_cache_path())


def download(
    tickers: Union[str, Sequence[str]],
    start: Optional[DateLikeInput] = None,
    end: Optional[DateLikeInput] = None,
    provider: Optional[FMPProvider] = None,
    api_key: Optional[str] = None,
    use_cache: bool = True,
    cache: Optional[LocalCache] = None,
    cache_path: Optional[Union[str, Path]] = None,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """
    Download OHLCV history for one or more tickers.

    Mirrors a simple yfinance.download() shape and returns a long DataFrame
    with normalized column names. By default results are served from the local
    DuckDB cache (``~/.halalquant/cache.duckdb``), fetching only missing symbols.

    Set the vendor key once with ``hq.api_key = "..."``, pass ``api_key=`` here,
    or export ``FMP_API_KEY``.
    """
    symbols = validate_symbols(tickers)
    start_date, end_date = validate_date_range(start, end)
    client = provider or FMPProvider(api_key=api_key)
    store = _build_cache(client, cache=cache, cache_path=cache_path, use_cache=use_cache)
    if store is None:
        return client.get_prices(symbols, start=start_date, end=end_date)
    return store.get_prices(
        symbols, start=start_date, end=end_date, force_refresh=force_refresh
    )


def get_halal_universe(
    tickers: Union[str, Sequence[str]],
    as_of: Optional[DateLikeInput] = None,
    standard: str = "aaoifi",
    provider: Optional[FMPProvider] = None,
    api_key: Optional[str] = None,
    apply_sector_filter: bool = True,
    use_cache: bool = True,
    cache: Optional[LocalCache] = None,
    cache_path: Optional[Union[str, Path]] = None,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """
    Return compliant tickers and screening metrics for a candidate universe.

    Steps:
    1. Optional sector / business-activity exclusion (from FMP profile when available)
    2. Financial ratio screening (AAOIFI by default)
    3. Persist compliance flags into the local cache when caching is enabled
    """
    symbols = validate_symbols(tickers)
    client = provider or FMPProvider(api_key=api_key)
    store = _build_cache(client, cache=cache, cache_path=cache_path, use_cache=use_cache)

    if apply_sector_filter:
        sector_map: dict[str, str] = {}
        if hasattr(client, "get_sector_map"):
            try:
                sector_map = client.get_sector_map(symbols)
            except (ValueError, OSError):
                # Missing API key or local I/O issues — continue without sector data.
                sector_map = {}
        sector_filter = SectorFilter()
        symbols = sector_filter.filter_symbols(symbols, sector_map=sector_map or None)

    if not symbols:
        return pd.DataFrame(
            columns=[
                "symbol",
                "as_of",
                "is_compliant",
                "debt_ratio",
                "cash_ratio",
                "receivables_ratio",
                "standard",
                "reason",
            ]
        )

    if store is not None:
        fundamentals = store.get_balance_sheet(
            symbols, as_of=as_of, force_refresh=force_refresh
        )
    else:
        fundamentals = client.get_balance_sheet(symbols, as_of=as_of)

    if standard.lower() == "aaoifi":
        screener = AAOIFIScreener()
    else:
        from halalquant.screening._djim import DJIMScreener

        screener = DJIMScreener()

    result = screener.evaluate_compliance(fundamentals)
    if store is not None and not result.empty:
        store.write_compliance(result)
    return result
