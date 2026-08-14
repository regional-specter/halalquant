"""Dow Jones Islamic Market (DJIM) compliance rules."""

from __future__ import annotations

import numpy as np
import pandas as pd

from halalquant.base import BaseScreener
from halalquant.screening._aaoifi import AAOIFIScreener


class DJIMScreener(BaseScreener):
    """
    DJIM-style financial screens.

    Typical published thresholds (approximate; confirm against current guide):
    - total debt / trailing 24m market cap < 33%
    - cash + interest-bearing securities / trailing 24m market cap < 33%
    - accounts receivable / trailing 24m market cap < 33%
    """

    def __init__(
        self,
        debt_threshold: float = 0.33,
        cash_threshold: float = 0.33,
        receivables_threshold: float = 0.33,
    ) -> None:
        self.debt_threshold = debt_threshold
        self.cash_threshold = cash_threshold
        self.receivables_threshold = receivables_threshold

    def _delegate(self) -> AAOIFIScreener:
        return AAOIFIScreener(
            debt_threshold=self.debt_threshold,
            cash_threshold=self.cash_threshold,
            receivables_threshold=self.receivables_threshold,
        )

    def evaluate_compliance(self, fundamentals: pd.DataFrame) -> pd.DataFrame:
        result = self._delegate().evaluate_compliance(fundamentals)
        if result.empty:
            return result
        out = result.copy()
        out["standard"] = "djim"
        out["reason"] = out["reason"].str.replace("AAOIFI", "DJIM", regex=False)
        return out

    def evaluate_arrays(
        self,
        total_debt: np.ndarray,
        cash_and_equiv: np.ndarray,
        market_cap_24m: np.ndarray,
        receivables_and_liquid: np.ndarray | None = None,
    ) -> np.ndarray:
        """Vectorized boolean mask using DJIM thresholds (default 33%)."""
        return self._delegate().evaluate_arrays(
            total_debt,
            cash_and_equiv,
            market_cap_24m,
            receivables_and_liquid,
        )
