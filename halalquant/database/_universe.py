"""Named equity universes used to warm the local dataset."""

from __future__ import annotations

from datetime import date
from io import StringIO
from typing import Optional, Union
from urllib.error import URLError
from urllib.request import urlopen

import pandas as pd

# Names with no recorded add-date are treated as members from this day.
# Wikipedia's changes table is a selected list, not a CRSP membership file.
OPEN_MEMBERSHIP_START = date(1990, 1, 1)
DateLike = Union[str, date]

_SP500_CSV_URLS = (
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv",
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv",
)
_SP500_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def list_universe(
    name: str = "sp500",
    as_of: Optional[DateLike] = None,
    start: Optional[DateLike] = None,
    end: Optional[DateLike] = None,
) -> pd.DataFrame:
    """
    Return members of a named universe.

    With no dates, this is the **current** S&P 500 (Yahoo-style symbols).
    ``as_of`` returns who was in the index on that day. ``start`` and ``end``
    return the union of names that were members at any point in the window
    (leavers included).

    Point-in-time membership is reconstructed from Wikipedia's current list
    plus its **selected** changes table. That is better than today's winners
    only. It is not CRSP. A name with no recorded add date is treated as a
    member since 1990-01-01.
    """
    key = str(name).strip().lower()
    if key not in {"sp500", "s&p500", "s&p 500"}:
        raise ValueError(f"Unknown universe {name!r}. Use 'sp500' or pass tickers= explicitly.")
    if as_of is None and start is None and end is None:
        return sp500_constituents()
    stints = sp500_stints()
    if as_of is not None and start is None and end is None:
        return members_as_of(stints, as_of)
    window_start = start or as_of
    window_end = end or as_of
    if window_start is None or window_end is None:
        raise ValueError("Pass as_of, or both start and end, for point-in-time membership.")
    return members_between(stints, window_start, window_end)


def sp500_constituents() -> pd.DataFrame:
    """Current S&P 500 members with Yahoo tickers and GICS sectors."""
    errors: list[str] = []
    for url in _SP500_CSV_URLS:
        try:
            frame = _read_csv_url(url)
            out = _normalize_sp500(frame)
            if not out.empty:
                return out
        except (OSError, URLError, ValueError) as exc:
            errors.append(f"{url}: {exc}")
    try:
        tables = pd.read_html(_SP500_WIKI_URL)
    except (OSError, URLError, ValueError, ImportError) as exc:
        errors.append(f"wikipedia: {exc}")
        tables = []
    for table in tables:
        try:
            out = _normalize_sp500(table)
        except ValueError:
            continue
        if not out.empty:
            return out
    raise ValueError(
        "Could not download the S&P 500 list. Pass tickers= explicitly. "
        + "; ".join(errors)
    )


def yahoo_symbol(symbol: str) -> str:
    """Map index-file tickers (BRK.B) onto yfinance symbols (BRK-B)."""
    return str(symbol).strip().upper().replace(".", "-")


def _read_csv_url(url: str, timeout: float = 30.0) -> pd.DataFrame:
    with urlopen(url, timeout=timeout) as response:  # noqa: S310 — fixed public CSV
        payload = response.read().decode("utf-8")
    return pd.read_csv(StringIO(payload))


def _normalize_sp500(frame: pd.DataFrame) -> pd.DataFrame:
    rename = {}
    for col in frame.columns:
        key = str(col).strip().lower()
        if key in {"symbol", "ticker"}:
            rename[col] = "symbol"
        elif key in {"security", "name"}:
            rename[col] = "name"
        elif ("gics" in key and "sub" in key and "industry" in key) or key in {
            "gics sub-industry",
            "sub-industry",
            "industry",
        }:
            rename[col] = "industry"
        elif ("gics" in key and "sector" in key) or key == "sector":
            rename[col] = "sector"
    out = frame.rename(columns=rename)
    if "symbol" not in out.columns:
        raise ValueError("universe table has no symbol column")
    out["symbol"] = out["symbol"].map(yahoo_symbol)
    if "sector" not in out.columns:
        out["sector"] = pd.NA
    if "name" not in out.columns:
        out["name"] = pd.NA
    if "industry" not in out.columns:
        out["industry"] = pd.NA
    out = out.dropna(subset=["symbol"])
    out = out[out["symbol"] != ""]
    out = out.drop_duplicates(subset=["symbol"], keep="first")
    return out[["symbol", "name", "sector", "industry"]].reset_index(drop=True)


def sp500_changes() -> pd.DataFrame:
    """
    Selected S&P 500 adds and removes from Wikipedia.

    Columns: ``date``, ``added_symbol``, ``removed_symbol``. This is the
    "selected changes" table, not a complete constituent history.
    """
    try:
        tables = pd.read_html(_SP500_WIKI_URL)
    except (OSError, URLError, ValueError, ImportError) as exc:
        raise ValueError(f"Could not download S&P 500 changes: {exc}") from exc
    for table in tables:
        parsed = _parse_changes_table(table)
        if parsed is not None and not parsed.empty:
            return parsed
    return pd.DataFrame(columns=["date", "added_symbol", "removed_symbol"])


def sp500_stints() -> pd.DataFrame:
    """One row per membership stint. ``end_date`` is null while the name is still in."""
    return stints_from_changes(sp500_constituents(), sp500_changes())


def stints_from_changes(
    current: pd.DataFrame,
    changes: pd.DataFrame,
    open_start: date = OPEN_MEMBERSHIP_START,
) -> pd.DataFrame:
    """
    Build ``[start_date, end_date)`` stints from a current list and a changes tape.

    ``end_date`` is the session the name left. A name still in ``current`` has
    a null ``end_date``. Wikipedia omissions stay in from ``open_start``.
    """
    current = current.copy()
    current["symbol"] = current["symbol"].map(yahoo_symbol)
    meta = current.drop_duplicates("symbol").set_index("symbol")
    current_syms = set(meta.index)

    events: dict[str, list[tuple[date, str]]] = {}
    if changes is not None and not changes.empty:
        tape = changes.copy()
        tape["date"] = pd.to_datetime(tape["date"], errors="coerce")
        tape = tape.dropna(subset=["date"]).sort_values("date")
        for _, row in tape.iterrows():
            day = row["date"].date()
            added = _clean_symbol(row.get("added_symbol"))
            removed = _clean_symbol(row.get("removed_symbol"))
            if added:
                events.setdefault(added, []).append((day, "add"))
            if removed:
                events.setdefault(removed, []).append((day, "remove"))

    rows: list[dict] = []
    for symbol in sorted(set(events) | current_syms):
        symbol_events = sorted(events.get(symbol, []), key=lambda item: (item[0], item[1] != "add"))
        active: Optional[date] = None
        closed: list[tuple[date, date]] = []
        for day, kind in symbol_events:
            if kind == "add":
                if active is None:
                    active = day
            elif active is not None:
                if day > active:
                    closed.append((active, day))
                active = None
            else:
                closed.append((open_start, day))
                active = None
        if symbol in current_syms:
            start = active or open_start
            closed.append((start, None))
        elif active is not None:
            closed.append((active, None))
        info = meta.loc[symbol] if symbol in meta.index else None
        for start, stop in _merge_stints(closed):
            rows.append(
                {
                    "universe": "sp500",
                    "symbol": symbol,
                    "start_date": start,
                    "end_date": stop,
                    "name": None if info is None else info.get("name"),
                    "sector": None if info is None else info.get("sector"),
                    "industry": None if info is None else info.get("industry"),
                }
            )
    if not rows:
        return pd.DataFrame(
            columns=["universe", "symbol", "start_date", "end_date", "name", "sector", "industry"]
        )
    out = pd.DataFrame(rows)
    return out.sort_values(["symbol", "start_date"]).reset_index(drop=True)


def members_as_of(stints: pd.DataFrame, as_of: DateLike) -> pd.DataFrame:
    """Names whose stint covers ``as_of`` (left the index on ``end_date``)."""
    if stints is None or stints.empty:
        return pd.DataFrame(columns=["symbol", "name", "sector", "industry"])
    day = pd.Timestamp(as_of).date()
    start = pd.to_datetime(stints["start_date"]).dt.date
    end = pd.to_datetime(stints["end_date"], errors="coerce")
    open_ended = stints["end_date"].isna()
    ended_after = (~open_ended) & (end.dt.date > day)
    covered = (start <= day) & (open_ended | ended_after)
    return (
        stints.loc[covered, ["symbol", "name", "sector", "industry"]]
        .drop_duplicates("symbol")
        .sort_values("symbol")
        .reset_index(drop=True)
    )


def members_between(stints: pd.DataFrame, start: DateLike, end: DateLike) -> pd.DataFrame:
    """Union of names that were members on any day in ``[start, end]``."""
    if stints is None or stints.empty:
        return pd.DataFrame(columns=["symbol", "name", "sector", "industry"])
    start_d = pd.Timestamp(start).date()
    end_d = pd.Timestamp(end).date()
    stint_start = pd.to_datetime(stints["start_date"]).dt.date
    open_ended = stints["end_date"].isna()
    stint_end = pd.to_datetime(stints["end_date"], errors="coerce")
    overlaps = (stint_start <= end_d) & (open_ended | (stint_end.dt.date >= start_d))
    return (
        stints.loc[overlaps, ["symbol", "name", "sector", "industry"]]
        .drop_duplicates("symbol")
        .sort_values("symbol")
        .reset_index(drop=True)
    )


def _clean_symbol(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "<na>"}:
        return ""
    return yahoo_symbol(text)


def _merge_stints(stints: list[tuple[date, Optional[date]]]) -> list[tuple[date, Optional[date]]]:
    if not stints:
        return []
    ordered = sorted(stints, key=lambda item: (item[0], item[1] or date.max))
    merged: list[tuple[date, Optional[date]]] = [ordered[0]]
    for start, stop in ordered[1:]:
        prev_start, prev_stop = merged[-1]
        if prev_stop is None:
            continue
        if start <= prev_stop:
            if stop is None or stop > prev_stop:
                merged[-1] = (prev_start, stop)
            continue
        merged.append((start, stop))
    return merged


def _parse_changes_table(frame: pd.DataFrame) -> Optional[pd.DataFrame]:
    flat = { _flatten_col(col): col for col in frame.columns }
    date_col = _find_col(flat, lambda key: "date" in key and "add" not in key and "remov" not in key)
    added_col = _find_col(
        flat,
        lambda key: ("ticker" in key or "symbol" in key) and "add" in key,
    )
    removed_col = _find_col(
        flat,
        lambda key: ("ticker" in key or "symbol" in key) and "remov" in key,
    )
    if date_col is None or (added_col is None and removed_col is None):
        return None
    out = pd.DataFrame(
        {
            "date": frame[date_col],
            "added_symbol": frame[added_col] if added_col is not None else pd.NA,
            "removed_symbol": frame[removed_col] if removed_col is not None else pd.NA,
        }
    )
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out = out.dropna(subset=["date"])
    if out.empty:
        return None
    return out.reset_index(drop=True)


def _flatten_col(col: object) -> str:
    if isinstance(col, tuple):
        parts = [str(part) for part in col if str(part) not in {"", "nan", "None"} and "unnamed" not in str(part).lower()]
        return " ".join(parts).lower()
    return str(col).lower()


def _find_col(flat: dict, predicate) -> object:
    for key, original in flat.items():
        if predicate(key):
            return original
    return None


def map_universe_activity(sector: object, industry: object = None) -> str:
    """Map GICS / Yahoo sector+industry labels onto the exclusion vocabulary."""
    from halalquant.providers._yfinance import _map_yahoo_activity

    return _map_yahoo_activity(str(sector or ""), str(industry or ""))
