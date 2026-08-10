"""Dow Jones Islamic Market (DJIM) compliance rules."""

from __future__ import annotations

import pandas as pd

from halalquant.base import BaseScreener


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

    def evaluate_compliance(self, fundamentals: pd.DataFrame) -> pd.DataFrame:
        # Reuse AAOIFI vectorized path with DJIM thresholds
        from halalquant.screening._aaoifi import AAOIFIScreener

        screener = AAOIFIScreener(
            debt_threshold=self.debt_threshold,
            cash_threshold=self.cash_threshold,
            receivables_threshold=self.receivables_threshold,
        )
        result = screener.evaluate_compliance(fundamentals)
        if not result.empty:
            result["standard"] = "djim"
            result["reason"] = result["reason"].str.replace("AAOIFI", "DJIM", regex=False)
        return result
