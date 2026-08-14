"""Unified strategy-facing API (yfinance-like surface)."""

from __future__ import annotations

from datetime import date
from typing import Optional, Sequence, Union

import pandas as pd

from halalquant.providers._fmp import FMPProvider
from halalquant.screening._aaoifi import AAOIFIScreener
from halalquant.screening._sector_filter import SectorFilter
from halalquant.utils._pit_adjustments import as_of_filter
from halalquant.utils.validation import validate_date_range, validate_symbols

DateLikeInput = Union[str, date]


def download(
    tickers: Union[str, Sequence[str]],
    start: Optional[DateLikeInput] = None,
    end: Optional[DateLikeInput] = None,
    provider: Optional[FMPProvider] = None,
    api_key: Optional[str] = None,
) -> pd.DataFrame:
    """
    Fetch OHLCV history for one or more tickers.

    Mirrors a simple yfinance.download() shape: call the vendor, return a long
    DataFrame with normalized column names. Nothing is written to disk.

    Set the vendor key once with ``hq.api_key = "..."``, pass ``api_key=`` here,
    or export ``FMP_API_KEY``.
    """
    symbols = validate_symbols(tickers)
    start_date, end_date = validate_date_range(start, end)
    client = provider or FMPProvider(api_key=api_key)
    return client.get_prices(symbols, start=start_date, end=end_date)


def get_halal_universe(
    tickers: Union[str, Sequence[str]],
    as_of: Optional[DateLikeInput] = None,
    standard: str = "aaoifi",
    provider: Optional[FMPProvider] = None,
    api_key: Optional[str] = None,
    apply_sector_filter: bool = True,
) -> pd.DataFrame:
    """
    Fetch fundamentals and return compliant tickers plus screening metrics.

    Steps:
    1. Optional sector / business-activity exclusion (from FMP profile when available)
    2. Fetch balance sheets from the vendor
    3. Financial ratio screening (AAOIFI by default)
    """
    symbols = validate_symbols(tickers)
    client = provider or FMPProvider(api_key=api_key)

    if apply_sector_filter:
        sector_map: dict[str, str] = {}
        if hasattr(client, "get_sector_map"):
            try:
                sector_map = client.get_sector_map(symbols)
            except (ValueError, OSError):
                sector_map = {}
        sector_filter = SectorFilter()
        symbols = sector_filter.filter_symbols(symbols, sector_map=sector_map or None)

    empty = pd.DataFrame(
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
    if not symbols:
        return empty

    fundamentals = client.get_balance_sheet(symbols, as_of=as_of)
    if as_of is not None and not fundamentals.empty:
        fundamentals = as_of_filter(fundamentals, as_of=str(as_of)[:10])

    if standard.lower() == "aaoifi":
        screener = AAOIFIScreener()
    else:
        from halalquant.screening._djim import DJIMScreener

        screener = DJIMScreener()

    return screener.evaluate_compliance(fundamentals)
