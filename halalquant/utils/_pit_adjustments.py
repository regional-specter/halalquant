"""Point-in-time restatement helpers (no look-ahead bias)."""

from __future__ import annotations

import pandas as pd


def known_filings(
    frame: pd.DataFrame,
    as_of: str | pd.Timestamp,
    filed_col: str = "filed_date",
) -> pd.DataFrame:
    """Keep every filing that was already public on `as_of` (no collapse)."""
    if frame.empty:
        return frame.copy()
    as_of_ts = pd.Timestamp(as_of)
    return frame[pd.to_datetime(frame[filed_col]) <= as_of_ts].copy()


def as_of_filter(
    frame: pd.DataFrame,
    as_of: str | pd.Timestamp,
    filed_col: str = "filed_date",
    report_col: str = "report_date",
) -> pd.DataFrame:
    """
    Keep only rows that were known on `as_of`.

    A filing is usable when filed_date <= as_of. When multiple filings exist
    for the same symbol, keep the latest report_date that satisfies the rule.
    """
    if frame.empty:
        return frame.copy()

    as_of_ts = pd.Timestamp(as_of)
    known = frame[pd.to_datetime(frame[filed_col]) <= as_of_ts].copy()
    if known.empty:
        return known

    known = known.sort_values([report_col, filed_col])
    return known.groupby("symbol", as_index=False).tail(1).reset_index(drop=True)


def prevent_lookahead_prices(
    prices: pd.DataFrame,
    as_of: str | pd.Timestamp,
    date_col: str = "date",
) -> pd.DataFrame:
    """Drop any price rows after the decision date."""
    if prices.empty:
        return prices.copy()
    as_of_ts = pd.Timestamp(as_of)
    return prices[pd.to_datetime(prices[date_col]) <= as_of_ts].copy()
