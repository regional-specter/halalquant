"""Core abstract interfaces for data providers and Shariah screeners."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Any, Optional, Sequence, Union

import pandas as pd

DateLike = Union[str, date]


class BaseDataProvider(ABC):
    """Strict interface every vendor adaptor must implement."""

    @abstractmethod
    def get_prices(
        self,
        symbols: Sequence[str],
        start: DateLike,
        end: DateLike,
    ) -> pd.DataFrame:
        """
        Return OHLCV prices with normalized columns.

        Required columns: symbol, date, open, high, low, close, volume, adj_close
        """

    @abstractmethod
    def get_balance_sheet(
        self,
        symbols: Sequence[str],
        as_of: Optional[DateLike] = None,
    ) -> pd.DataFrame:
        """
        Return point-in-time balance sheet fields.

        Required columns include: symbol, report_date, filed_date,
        total_debt, cash_and_equiv, receivables, liquid_assets, market_cap
        """

    @abstractmethod
    def get_income_statement(
        self,
        symbols: Sequence[str],
        as_of: Optional[DateLike] = None,
    ) -> pd.DataFrame:
        """Return income statement fields used for purification."""


class BaseScreener(ABC):
    """Strict interface every Shariah screening engine must implement."""

    @abstractmethod
    def evaluate_compliance(self, fundamentals: pd.DataFrame) -> pd.DataFrame:
        """
        Evaluate compliance for each row of fundamentals.

        Must return a DataFrame with at least:
        symbol, is_compliant, debt_ratio, cash_ratio, receivables_ratio, reason
        """

    def screen(self, fundamentals: pd.DataFrame) -> pd.DataFrame:
        """Alias for evaluate_compliance."""
        return self.evaluate_compliance(fundamentals)


# Canonical column names used across providers and the strategy API
PRICE_COLUMNS: tuple[str, ...] = (
    "symbol",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "adj_close",
)

BALANCE_SHEET_COLUMNS: tuple[str, ...] = (
    "symbol",
    "report_date",
    "filed_date",
    "total_debt",
    "short_term_debt",
    "long_term_debt",
    "cash_and_equiv",
    "interest_bearing_securities",
    "receivables",
    "liquid_assets",
    "market_cap",
    "market_cap_24m",
)

COMPLIANCE_COLUMNS: tuple[str, ...] = (
    "symbol",
    "as_of",
    "is_compliant",
    "debt_ratio",
    "cash_ratio",
    "receivables_ratio",
    "standard",
    "reason",
)
