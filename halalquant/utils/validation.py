"""Symbol and date-range validators."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional, Sequence, Union

DateLike = Union[str, date, datetime]


def validate_symbols(tickers: Union[str, Sequence[str]]) -> list[str]:
    if isinstance(tickers, str):
        raw = [t.strip() for t in tickers.split(",")]
    else:
        raw = [str(t).strip() for t in tickers]

    symbols = [t.upper() for t in raw if t]
    if not symbols:
        raise ValueError("Provide at least one ticker symbol.")

    bad = [s for s in symbols if not s.replace(".", "").replace("-", "").isalnum()]
    if bad:
        raise ValueError(f"Invalid ticker symbol(s): {bad}")
    return symbols


def validate_date_range(
    start: Optional[DateLike],
    end: Optional[DateLike],
) -> tuple[date, date]:
    today = date.today()
    end_date = _to_date(end) if end is not None else today
    start_date = _to_date(start) if start is not None else end_date - timedelta(days=365)

    if start_date > end_date:
        raise ValueError(f"start ({start_date}) must be on or before end ({end_date}).")
    return start_date, end_date


def _to_date(value: DateLike) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
