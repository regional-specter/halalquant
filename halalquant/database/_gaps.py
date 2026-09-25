"""Missing price windows inside an already cached history."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional, Sequence, Union

import pandas as pd

DateLike = Union[str, date, pd.Timestamp]


def _as_date(value: DateLike) -> date:
    return pd.Timestamp(value).date()


def price_gap_windows(
    dates: Sequence[DateLike],
    start: DateLike,
    end: DateLike,
    interior_gap_days: int = 5,
) -> list[tuple[date, date]]:
    """
    Ranges that still need a price fetch.

    The open ends are exact: a cached history that stops yesterday is a gap
    through ``end``, not "close enough." Interior holes longer than a long
    weekend (default 5 calendar days) are separate windows. A single holiday
    between Friday and Tuesday is not refetched forever.
    """
    start_d = _as_date(start)
    end_d = _as_date(end)
    if start_d > end_d:
        return []
    cleaned: list[date] = []
    for value in dates:
        if value is None or pd.isna(value):
            continue
        day = _as_date(value)
        if start_d <= day <= end_d:
            cleaned.append(day)
    cleaned = sorted(set(cleaned))
    if not cleaned:
        return [(start_d, end_d)]

    windows: list[tuple[date, date]] = []
    if cleaned[0] > start_d:
        windows.append((start_d, cleaned[0] - timedelta(days=1)))
    for prev, nxt in zip(cleaned, cleaned[1:]):
        if (nxt - prev).days > interior_gap_days:
            windows.append((prev + timedelta(days=1), nxt - timedelta(days=1)))
    if cleaned[-1] < end_d:
        windows.append((cleaned[-1] + timedelta(days=1), end_d))
    return [(left, right) for left, right in windows if left <= right and _has_weekday(left, right)]


def _has_weekday(start: date, end: date) -> bool:
    day = start
    while day <= end:
        if day.weekday() < 5:
            return True
        day += timedelta(days=1)
    return False
