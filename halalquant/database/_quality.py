"""Checks on cached facts before a book trades on them."""

from __future__ import annotations

import pandas as pd

QUALITY_COLUMNS = ["symbol", "date", "issue", "value"]


def dividend_quality(dividends: pd.DataFrame, adj_tolerance: float = 0.5) -> pd.DataFrame:
    """
    Flag dividend rows a book should not credit blindly.

    Issues: ``duplicate ex_date``, ``non-positive dividend``, and
    ``adj_dividend mismatch`` when the split-adjusted amount differs from the
    paid amount by more than ``adj_tolerance`` (relative). A split explains
    some mismatches; the flag asks for a look, it does not drop the row.
    """
    if dividends is None or dividends.empty:
        return pd.DataFrame(columns=QUALITY_COLUMNS)
    frame = dividends.copy()
    frame["ex_date"] = pd.to_datetime(frame["ex_date"], errors="coerce")
    frame["dividend"] = pd.to_numeric(frame["dividend"], errors="coerce")
    rows: list[dict] = []
    dupes = frame[frame.duplicated(["symbol", "ex_date"], keep=False)]
    for _, row in dupes.iterrows():
        rows.append({"symbol": row["symbol"], "date": row["ex_date"], "issue": "duplicate ex_date", "value": row["dividend"]})
    bad = frame[~(frame["dividend"] > 0)]
    for _, row in bad.iterrows():
        rows.append({"symbol": row["symbol"], "date": row["ex_date"], "issue": "non-positive dividend", "value": row["dividend"]})
    if "adj_dividend" in frame.columns:
        adj = pd.to_numeric(frame["adj_dividend"], errors="coerce")
        gap = (adj - frame["dividend"]).abs() / frame["dividend"].where(frame["dividend"] > 0)
        off = frame[gap > adj_tolerance]
        for idx, row in off.iterrows():
            rows.append({"symbol": row["symbol"], "date": row["ex_date"], "issue": "adj_dividend mismatch", "value": float(gap.loc[idx])})
    if not rows:
        return pd.DataFrame(columns=QUALITY_COLUMNS)
    return pd.DataFrame(rows, columns=QUALITY_COLUMNS).sort_values(["symbol", "date"]).reset_index(drop=True)


def impure_ratio_flags(metrics: pd.DataFrame, threshold: float = 0.5) -> pd.DataFrame:
    """Rows whose impure-income ratio is above ``threshold`` (likely a data error, e.g. a REIT)."""
    if metrics is None or metrics.empty or "impure_ratio" not in metrics.columns:
        return pd.DataFrame(columns=QUALITY_COLUMNS)
    frame = metrics.copy()
    frame["impure_ratio"] = pd.to_numeric(frame["impure_ratio"], errors="coerce")
    hit = frame[frame["impure_ratio"] > threshold]
    if hit.empty:
        return pd.DataFrame(columns=QUALITY_COLUMNS)
    date_col = "as_of" if "as_of" in hit.columns else "report_date"
    out = pd.DataFrame(
        {
            "symbol": hit["symbol"].values,
            "date": pd.to_datetime(hit[date_col]).values,
            "issue": f"impure ratio above {threshold:.0%}",
            "value": hit["impure_ratio"].values,
        }
    )
    return out.drop_duplicates(["symbol", "date"]).sort_values(["symbol", "date"]).reset_index(drop=True)
