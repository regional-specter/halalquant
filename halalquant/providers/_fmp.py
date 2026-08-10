"""Financial Modeling Prep (FMP) data provider stub."""

from __future__ import annotations

import os
from typing import Optional, Sequence

import pandas as pd

from halalquant.base import BALANCE_SHEET_COLUMNS, PRICE_COLUMNS, DateLike
from halalquant.providers._base_provider import AbstractFetcher


class FMPProvider(AbstractFetcher):
    """
    FMP wrapper.

    Network calls are stubbed for the scaffold. Wire real endpoints once an
    API key and rate-limit policy are in place.
    """

    BASE_URL = "https://financialmodelingprep.com/api/v3"

    def __init__(self, api_key: Optional[str] = None, **kwargs) -> None:
        super().__init__(api_key=api_key or os.getenv("FMP_API_KEY"), **kwargs)

    def get_prices(
        self,
        symbols: Sequence[str],
        start: DateLike,
        end: DateLike,
    ) -> pd.DataFrame:
        # TODO: call FMP historical-price-full and normalize columns
        _ = (start, end, self.api_key)
        return pd.DataFrame(columns=list(PRICE_COLUMNS))

    def get_balance_sheet(
        self,
        symbols: Sequence[str],
        as_of: Optional[DateLike] = None,
    ) -> pd.DataFrame:
        # TODO: call FMP balance-sheet-statement and attach market cap
        _ = (symbols, as_of, self.api_key)
        return pd.DataFrame(columns=list(BALANCE_SHEET_COLUMNS))

    def get_income_statement(
        self,
        symbols: Sequence[str],
        as_of: Optional[DateLike] = None,
    ) -> pd.DataFrame:
        # TODO: call FMP income-statement for purification inputs
        _ = (symbols, as_of, self.api_key)
        return pd.DataFrame(
            columns=[
                "symbol",
                "report_date",
                "filed_date",
                "total_revenue",
                "interest_income",
                "non_compliant_income",
            ]
        )
