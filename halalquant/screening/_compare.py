"""Side-by-side AAOIFI vs DJIM comparison."""

from __future__ import annotations

import pandas as pd

from halalquant.screening._aaoifi import AAOIFIScreener
from halalquant.screening._djim import DJIMScreener

COMPARE_COLUMNS: tuple[str, ...] = (
    "symbol",
    "as_of",
    "debt_ratio",
    "cash_ratio",
    "receivables_ratio",
    "aaoifi_compliant",
    "djim_compliant",
    "aaoifi_reason",
    "djim_reason",
    "agreement",
)


def compare_screeners(fundamentals: pd.DataFrame) -> pd.DataFrame:
    """
    Evaluate the same fundamentals under AAOIFI and DJIM.

    Ratios are identical; only pass/fail thresholds differ (30%/70% vs 33%/33%).
    `agreement` is True when both standards reach the same verdict.
    """
    aaoifi = AAOIFIScreener().evaluate_compliance(fundamentals)
    djim = DJIMScreener().evaluate_compliance(fundamentals)
    if aaoifi.empty:
        return pd.DataFrame(columns=list(COMPARE_COLUMNS))

    out = pd.DataFrame(
        {
            "symbol": aaoifi["symbol"].values,
            "as_of": aaoifi["as_of"].values,
            "debt_ratio": aaoifi["debt_ratio"].values,
            "cash_ratio": aaoifi["cash_ratio"].values,
            "receivables_ratio": aaoifi["receivables_ratio"].values,
            "aaoifi_compliant": aaoifi["is_compliant"].values,
            "djim_compliant": djim["is_compliant"].values,
            "aaoifi_reason": aaoifi["reason"].values,
            "djim_reason": djim["reason"].values,
        }
    )
    out["agreement"] = out["aaoifi_compliant"] == out["djim_compliant"]
    return out[list(COMPARE_COLUMNS)]
