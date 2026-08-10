"""SEC EDGAR parser stub for raw XBRL filings."""

from __future__ import annotations

from typing import Optional, Sequence

import pandas as pd

from halalquant.base import BALANCE_SHEET_COLUMNS, PRICE_COLUMNS, DateLike
from halalquant.providers._base_provider import AbstractFetcher


class SECEdgarProvider(AbstractFetcher):
    """
    SEC EDGAR adaptor.

    Prices are not served by EDGAR; balance-sheet extraction from XBRL is the
    primary goal of this provider.
    """

    BASE_URL = "https://data.sec.gov"

    def get_prices(
        self,
        symbols: Sequence[str],
        start: DateLike,
        end: DateLike,
    ) -> pd.DataFrame:
        _ = (symbols, start, end)
        raise NotImplementedError(
            "SEC EDGAR does not provide market prices. Use FMPProvider for OHLCV."
        )

    def get_balance_sheet(
        self,
        symbols: Sequence[str],
        as_of: Optional[DateLike] = None,
    ) -> pd.DataFrame:
        # TODO: resolve CIK, fetch companyfacts XBRL, map tags to schema
        _ = (symbols, as_of)
        return pd.DataFrame(columns=list(BALANCE_SHEET_COLUMNS))

    def get_income_statement(
        self,
        symbols: Sequence[str],
        as_of: Optional[DateLike] = None,
    ) -> pd.DataFrame:
        # TODO: map XBRL income tags used for purification
        _ = (symbols, as_of)
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
