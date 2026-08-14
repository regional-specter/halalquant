"""Yahoo Finance market-data adaptor via the yfinance library."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable, Optional, Sequence, Union

import pandas as pd
import yfinance as yf

from halalquant.base import (
    BALANCE_SHEET_COLUMNS,
    DIVIDEND_COLUMNS,
    INCOME_COLUMNS,
    PRICE_COLUMNS,
    BaseDataProvider,
    DateLike,
)

DateLikeInput = Union[str, date, datetime]

_PRICE_RENAME = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Adj Close": "adj_close",
    "Volume": "volume",
}


class YFinanceProvider(BaseDataProvider):
    """
    Fetch prices, dividends, and sector labels through yfinance.

    Fundamentals (balance sheets, income) come from SEC EDGAR, not Yahoo.
    """

    def __init__(
        self,
        downloader: Optional[Callable[..., pd.DataFrame]] = None,
        ticker_factory: Optional[Callable[[str], Any]] = None,
    ) -> None:
        self._download = downloader or yf.download
        self._ticker_factory = ticker_factory or yf.Ticker

    def get_prices(
        self,
        symbols: Sequence[str],
        start: DateLike,
        end: DateLike,
    ) -> pd.DataFrame:
        raw = self._download(
            list(symbols) if len(symbols) > 1 else symbols[0],
            start=_to_date_str(start),
            end=_to_date_str(end),
            auto_adjust=False,
            progress=False,
            threads=True,
        )
        return _normalize_prices(raw, list(symbols))

    def get_dividends(
        self,
        symbols: Sequence[str],
        start: Optional[DateLike] = None,
        end: Optional[DateLike] = None,
    ) -> pd.DataFrame:
        start_ts = pd.Timestamp(_to_date_str(start)) if start is not None else None
        end_ts = pd.Timestamp(_to_date_str(end)) if end is not None else None
        frames: list[pd.DataFrame] = []
        for symbol in symbols:
            series = getattr(self._ticker_factory(symbol), "dividends", pd.Series(dtype=float))
            if series is None or len(series) == 0:
                continue
            frame = series.rename("dividend").reset_index()
            date_col = frame.columns[0]
            frame = frame.rename(columns={date_col: "ex_date"})
            frame["symbol"] = symbol
            frame["ex_date"] = pd.to_datetime(frame["ex_date"], utc=True, errors="coerce").dt.tz_localize(None)
            if start_ts is not None:
                frame = frame[frame["ex_date"] >= start_ts]
            if end_ts is not None:
                frame = frame[frame["ex_date"] <= end_ts]
            if frame.empty:
                continue
            frame["adj_dividend"] = frame["dividend"]
            frame["record_date"] = pd.NaT
            frame["payment_date"] = pd.NaT
            frame["ex_date"] = frame["ex_date"].dt.date
            frames.append(frame[list(DIVIDEND_COLUMNS)])

        if not frames:
            return pd.DataFrame(columns=list(DIVIDEND_COLUMNS))
        return (
            pd.concat(frames, ignore_index=True)
            .sort_values(["symbol", "ex_date"])
            .reset_index(drop=True)
        )

    def get_sector_map(self, symbols: Sequence[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for symbol in symbols:
            info = _ticker_info(self._ticker_factory(symbol))
            mapped = _map_yahoo_activity(
                str(info.get("sector") or ""),
                str(info.get("industry") or ""),
            )
            if mapped:
                out[symbol] = mapped
        return out

    def get_balance_sheet(
        self,
        symbols: Sequence[str],
        as_of: Optional[DateLike] = None,
    ) -> pd.DataFrame:
        _ = (symbols, as_of)
        return pd.DataFrame(columns=list(BALANCE_SHEET_COLUMNS))

    def get_income_statement(
        self,
        symbols: Sequence[str],
        as_of: Optional[DateLike] = None,
    ) -> pd.DataFrame:
        _ = (symbols, as_of)
        return pd.DataFrame(columns=list(INCOME_COLUMNS))


def _to_date_str(value: DateLikeInput) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def _ticker_info(ticker: Any) -> dict[str, Any]:
    info = getattr(ticker, "info", None)
    if callable(info):
        info = info()
    if isinstance(info, dict):
        return info
    getter = getattr(ticker, "get_info", None)
    if callable(getter):
        payload = getter()
        if isinstance(payload, dict):
            return payload
    return {}


def _map_yahoo_activity(sector: str, industry: str) -> str:
    """Map Yahoo sector/industry labels onto the library exclusion vocabulary."""
    blob = f"{sector} {industry}".lower()
    if any(token in blob for token in ("bank", "mortgage", "credit services")):
        return "conventional banking"
    if "insurance" in blob:
        return "conventional insurance"
    if any(token in blob for token in ("gambling", "casino")):
        return "gambling"
    if "tobacco" in blob:
        return "tobacco"
    if any(token in blob for token in ("defense", "weapons", "aerospace & defense")):
        return "defense"
    if any(token in blob for token in ("brewers", "wineries", "distiller", "alcoholic")):
        return "alcohol"
    if "adult" in blob:
        return "adult entertainment"
    return sector.strip().lower() or industry.strip().lower()


def _normalize_prices(raw: pd.DataFrame, symbols: list[str]) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame(columns=list(PRICE_COLUMNS))

    frame = raw.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        level0 = {str(name) for name in frame.columns.get_level_values(0).unique()}
        price_names = {"Open", "High", "Low", "Close", "Adj Close", "Volume"}
        pieces: list[pd.DataFrame] = []
        if level0 & price_names:
            tickers = [str(name) for name in frame.columns.get_level_values(1).unique()]
            for ticker in tickers:
                pieces.append(_flat_ohlcv(frame.xs(ticker, axis=1, level=1), ticker))
        else:
            tickers = [str(name) for name in frame.columns.get_level_values(0).unique()]
            for ticker in tickers:
                pieces.append(_flat_ohlcv(frame.xs(ticker, axis=1, level=0), ticker))
        if not pieces:
            return pd.DataFrame(columns=list(PRICE_COLUMNS))
        out = pd.concat(pieces, ignore_index=True)
    else:
        out = _flat_ohlcv(frame, symbols[0] if symbols else "UNKNOWN")

    return out[list(PRICE_COLUMNS)].sort_values(["symbol", "date"]).reset_index(drop=True)


def _flat_ohlcv(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    sub = frame.copy()
    sub.columns = [str(col) for col in sub.columns]
    sub = sub.rename(columns=_PRICE_RENAME)
    if "adj_close" not in sub.columns:
        sub["adj_close"] = sub["close"] if "close" in sub.columns else pd.NA
    sub = sub.reset_index()
    if "Date" in sub.columns:
        sub = sub.rename(columns={"Date": "date"})
    elif "index" in sub.columns:
        sub = sub.rename(columns={"index": "date"})
    sub["symbol"] = symbol
    sub["date"] = pd.to_datetime(sub["date"], errors="coerce").dt.date
    for col in ("open", "high", "low", "close", "adj_close"):
        if col in sub.columns:
            sub[col] = pd.to_numeric(sub[col], errors="coerce")
    if "volume" in sub.columns:
        sub["volume"] = pd.to_numeric(sub["volume"], errors="coerce").astype("Int64")
    keep = [col for col in PRICE_COLUMNS if col in sub.columns]
    return sub[keep]
