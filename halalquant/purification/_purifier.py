"""Impure income ratio and dividend purification calculators."""

from __future__ import annotations

import numpy as np
import pandas as pd


class Purifier:
    """
    Compute purification amounts for dividends.

    Purification amount ≈ dividend * (non_compliant_income / total_revenue)

    When total revenue is missing or zero, the impure ratio is treated as NaN
    and the purification amount is left unset.
    """

    def impure_income_ratio(
        self,
        non_compliant_income: float | np.ndarray | pd.Series,
        total_revenue: float | np.ndarray | pd.Series,
    ) -> float | np.ndarray | pd.Series:
        revenue = np.asarray(total_revenue, dtype=float)
        impure = np.asarray(non_compliant_income, dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = np.where(revenue > 0, impure / revenue, np.nan)
        if np.ndim(ratio) == 0:
            return float(ratio)
        if isinstance(non_compliant_income, pd.Series):
            return pd.Series(ratio, index=non_compliant_income.index)
        return ratio

    def purification_amount(
        self,
        dividend: float | np.ndarray | pd.Series,
        non_compliant_income: float | np.ndarray | pd.Series,
        total_revenue: float | np.ndarray | pd.Series,
    ) -> float | np.ndarray | pd.Series:
        ratio = self.impure_income_ratio(non_compliant_income, total_revenue)
        amount = np.asarray(dividend, dtype=float) * np.asarray(ratio, dtype=float)
        if np.ndim(amount) == 0:
            return float(amount)
        if isinstance(dividend, pd.Series):
            return pd.Series(amount, index=dividend.index)
        return amount

    def purify_frame(self, income: pd.DataFrame, dividends: pd.DataFrame) -> pd.DataFrame:
        """
        Join income metrics onto dividend rows and attach purification columns.

        Expected income columns: symbol, non_compliant_income, total_revenue
        Expected dividend columns: symbol, ex_date, dividend
        """
        merged = dividends.merge(
            income[["symbol", "non_compliant_income", "total_revenue"]],
            on="symbol",
            how="left",
        )
        merged["impure_ratio"] = self.impure_income_ratio(
            merged["non_compliant_income"],
            merged["total_revenue"],
        )
        merged["purification_amount"] = self.purification_amount(
            merged["dividend"],
            merged["non_compliant_income"],
            merged["total_revenue"],
        )
        return merged
