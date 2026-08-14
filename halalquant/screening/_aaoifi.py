"""AAOIFI financial-ratio compliance engine."""

from __future__ import annotations

import numpy as np
import pandas as pd

from halalquant.base import BaseScreener


def compute_ratios(fundamentals: pd.DataFrame) -> pd.DataFrame:
    """
    Compute debt, cash, and receivables ratios against 24-month market cap.

    Numerators and the denominator are the same for AAOIFI and DJIM; only
    the pass/fail thresholds differ.
    """
    if fundamentals.empty:
        return pd.DataFrame(
            columns=["debt_ratio", "cash_ratio", "receivables_ratio", "market_cap_24m"],
            index=fundamentals.index,
        )

    frame = fundamentals
    market_cap = frame.get("market_cap_24m", frame.get("market_cap")).astype(float)
    market_cap = market_cap.replace(0, np.nan)

    total_debt = frame.get("total_debt")
    if total_debt is None:
        short_term = frame.get("short_term_debt", 0).fillna(0)
        long_term = frame.get("long_term_debt", 0).fillna(0)
        total_debt = short_term + long_term
    total_debt = total_debt.astype(float).fillna(0)

    cash = frame.get("cash_and_equiv", 0).fillna(0).astype(float)
    ibs = frame.get("interest_bearing_securities", 0).fillna(0).astype(float)
    cash_and_ibs = cash + ibs

    receivables = frame.get("receivables", 0).fillna(0).astype(float)
    liquid = frame.get("liquid_assets", 0).fillna(0).astype(float)
    receivables_and_liquid = receivables + liquid

    return pd.DataFrame(
        {
            "debt_ratio": total_debt / market_cap,
            "cash_ratio": cash_and_ibs / market_cap,
            "receivables_ratio": receivables_and_liquid / market_cap,
            "market_cap_24m": market_cap,
        },
        index=frame.index,
    )


class AAOIFIScreener(BaseScreener):
    """
    AAOIFI-style screening against 24-month average market capitalization.

    Default thresholds:
    - debt / MC_24m < 0.30
    - cash + interest-bearing securities / MC_24m < 0.30
    - receivables + liquid assets / MC_24m < 0.70
    """

    def __init__(
        self,
        debt_threshold: float = 0.30,
        cash_threshold: float = 0.30,
        receivables_threshold: float = 0.70,
    ) -> None:
        self.debt_threshold = debt_threshold
        self.cash_threshold = cash_threshold
        self.receivables_threshold = receivables_threshold

    def evaluate_compliance(self, fundamentals: pd.DataFrame) -> pd.DataFrame:
        if fundamentals.empty:
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

        frame = fundamentals.copy()
        ratios = compute_ratios(frame)
        debt_ratio = ratios["debt_ratio"]
        cash_ratio = ratios["cash_ratio"]
        receivables_ratio = ratios["receivables_ratio"]
        market_cap = ratios["market_cap_24m"]

        debt_ok = debt_ratio < self.debt_threshold
        cash_ok = cash_ratio < self.cash_threshold
        recv_ok = receivables_ratio < self.receivables_threshold
        is_compliant = debt_ok & cash_ok & recv_ok & market_cap.notna()

        reasons = []
        for i in range(len(frame)):
            if pd.isna(market_cap.iloc[i]):
                reasons.append("missing market cap")
            elif not bool(debt_ok.iloc[i]):
                reasons.append("debt ratio exceeds threshold")
            elif not bool(cash_ok.iloc[i]):
                reasons.append("cash ratio exceeds threshold")
            elif not bool(recv_ok.iloc[i]):
                reasons.append("receivables ratio exceeds threshold")
            else:
                reasons.append("passes AAOIFI financial screens")

        as_of = frame["report_date"] if "report_date" in frame.columns else pd.NaT

        return pd.DataFrame(
            {
                "symbol": frame["symbol"].values,
                "as_of": as_of.values,
                "is_compliant": is_compliant.fillna(False).astype(bool).values,
                "debt_ratio": debt_ratio.values,
                "cash_ratio": cash_ratio.values,
                "receivables_ratio": receivables_ratio.values,
                "standard": "aaoifi",
                "reason": reasons,
            }
        )

    def evaluate_arrays(
        self,
        total_debt: np.ndarray,
        cash_and_equiv: np.ndarray,
        market_cap_24m: np.ndarray,
        receivables_and_liquid: np.ndarray | None = None,
    ) -> np.ndarray:
        """Vectorized boolean mask for array inputs."""
        with np.errstate(divide="ignore", invalid="ignore"):
            debt_ratio = total_debt / market_cap_24m
            cash_ratio = cash_and_equiv / market_cap_24m
            compliant = (debt_ratio < self.debt_threshold) & (
                cash_ratio < self.cash_threshold
            )
            if receivables_and_liquid is not None:
                recv_ratio = receivables_and_liquid / market_cap_24m
                compliant = compliant & (recv_ratio < self.receivables_threshold)
            compliant = compliant & np.isfinite(market_cap_24m) & (market_cap_24m > 0)
        return compliant
