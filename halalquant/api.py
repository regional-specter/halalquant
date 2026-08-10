"""Unified strategy-facing API (yfinance-like surface)."""

from __future__ import annotations

from datetime import date
from typing import Optional, Sequence, Union

import pandas as pd

from halalquant.base import DateLike
from halalquant.providers._fmp import FMPProvider
from halalquant.screening._aaoifi import AAOIFIScreener
from halalquant.screening._sector_filter import SectorFilter
from halalquant.utils.validation import validate_date_range, validate_symbols

DateLikeInput = Union[str, date]


def download(
    tickers: Union[str, Sequence[str]],
    start: Optional[DateLikeInput] = None,
    end: Optional[DateLikeInput] = None,
    provider: Optional[FMPProvider] = None,
) -> pd.DataFrame:
    """
    Download OHLCV history for one or more tickers.

    Mirrors a simple yfinance.download() shape and returns a long DataFrame
    with normalized column names.
    """
    symbols = validate_symbols(tickers)
    start_date, end_date = validate_date_range(start, end)
    client = provider or FMPProvider()
    return client.get_prices(symbols, start=start_date, end=end_date)


def get_halal_universe(
    tickers: Union[str, Sequence[str]],
    as_of: Optional[DateLikeInput] = None,
    standard: str = "aaoifi",
    provider: Optional[FMPProvider] = None,
    apply_sector_filter: bool = True,
) -> pd.DataFrame:
    """
    Return compliant tickers and screening metrics for a candidate universe.

    Steps:
    1. Optional sector / business-activity exclusion
    2. Financial ratio screening (AAOIFI by default)
    """
    symbols = validate_symbols(tickers)
    client = provider or FMPProvider()

    if apply_sector_filter:
        sector_filter = SectorFilter()
        symbols = sector_filter.filter_symbols(symbols)

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

    fundamentals = client.get_balance_sheet(symbols, as_of=as_of)

    if standard.lower() == "aaoifi":
        screener = AAOIFIScreener()
    else:
        from halalquant.screening._djim import DJIMScreener

        screener = DJIMScreener()

    return screener.evaluate_compliance(fundamentals)
