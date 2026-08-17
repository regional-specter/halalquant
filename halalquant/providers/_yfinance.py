"""Yahoo Finance market-data adaptor via the yfinance library."""

from __future__ import annotations

from datetime import date, datetime, timedelta
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
    Fetch prices, dividends, sector labels, and (for non-US issuers) statements.

    US fundamentals still prefer SEC EDGAR via FilingsProvider. Yahoo statements
    are used when there is no CIK. Yahoo does not publish filing dates; this
    adaptor uses report_date + 90 days as a conservative public-as-of date.
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
            raw = getattr(self._ticker_factory(symbol), "dividends", pd.Series(dtype=float))
            frame = _dividends_to_frame(raw, symbol)
            if frame.empty:
                continue
            if start_ts is not None:
                frame = frame[pd.to_datetime(frame["ex_date"]) >= start_ts]
            if end_ts is not None:
                frame = frame[pd.to_datetime(frame["ex_date"]) <= end_ts]
            if frame.empty:
                continue
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
        as_of_ts = pd.Timestamp(str(as_of)[:10]) if as_of is not None else None
        frames: list[pd.DataFrame] = []
        for symbol in symbols:
            ticker = self._ticker_factory(symbol)
            raw = getattr(ticker, "balance_sheet", None)
            if raw is None or getattr(raw, "empty", True):
                raw = getattr(ticker, "annual_balance_sheet", None)
            frame = _yahoo_balance_sheet(raw, symbol, as_of=as_of_ts)
            if not frame.empty:
                frames.append(frame)
        if not frames:
            return pd.DataFrame(columns=list(BALANCE_SHEET_COLUMNS) + ["shares_outstanding"])
        return (
            pd.concat(frames, ignore_index=True)
            .sort_values(["symbol", "report_date"])
            .reset_index(drop=True)
        )

    def get_income_statement(
        self,
        symbols: Sequence[str],
        as_of: Optional[DateLike] = None,
    ) -> pd.DataFrame:
        as_of_ts = pd.Timestamp(str(as_of)[:10]) if as_of is not None else None
        frames: list[pd.DataFrame] = []
        for symbol in symbols:
            ticker = self._ticker_factory(symbol)
            raw = getattr(ticker, "income_stmt", None)
            if raw is None or getattr(raw, "empty", True):
                raw = getattr(ticker, "financials", None)
            frame = _yahoo_income_statement(raw, symbol, as_of=as_of_ts)
            if not frame.empty:
                frames.append(frame)
        if not frames:
            return pd.DataFrame(columns=list(INCOME_COLUMNS))
        return (
            pd.concat(frames, ignore_index=True)
            .sort_values(["symbol", "report_date"])
            .reset_index(drop=True)
        )


def _dividends_to_frame(raw: Any, symbol: str) -> pd.DataFrame:
    """Normalize yfinance dividend Series or DataFrame to DIVIDEND_COLUMNS."""
    if raw is None or len(raw) == 0:
        return pd.DataFrame(columns=list(DIVIDEND_COLUMNS))

    if isinstance(raw, pd.Series):
        frame = raw.rename("dividend").reset_index()
    elif isinstance(raw, pd.DataFrame):
        frame = raw.copy()
        if not any(str(col).lower() in {"date", "ex_date"} for col in frame.columns):
            frame = frame.reset_index()
        rename = {}
        for col in frame.columns:
            key = str(col).strip().lower().replace(" ", "_")
            if key in {"date", "ex_date", "index"}:
                rename[col] = "ex_date"
            elif key in {"dividends", "dividend", "amount"}:
                rename[col] = "dividend"
        frame = frame.rename(columns=rename)
        if "dividend" not in frame.columns:
            numeric = [c for c in frame.columns if c != "ex_date" and pd.api.types.is_numeric_dtype(frame[c])]
            if numeric:
                frame = frame.rename(columns={numeric[0]: "dividend"})
    else:
        return pd.DataFrame(columns=list(DIVIDEND_COLUMNS))

    if "ex_date" not in frame.columns:
        frame = frame.rename(columns={frame.columns[0]: "ex_date"})
    if "dividend" not in frame.columns:
        return pd.DataFrame(columns=list(DIVIDEND_COLUMNS))

    frame["symbol"] = symbol
    frame["ex_date"] = pd.to_datetime(frame["ex_date"], utc=True, errors="coerce").dt.tz_localize(None)
    frame = frame.dropna(subset=["ex_date"])
    frame["dividend"] = pd.to_numeric(frame["dividend"], errors="coerce")
    frame["adj_dividend"] = frame["dividend"]
    frame["record_date"] = pd.NaT
    frame["payment_date"] = pd.NaT
    frame["ex_date"] = frame["ex_date"].dt.date
    return frame[list(DIVIDEND_COLUMNS)]


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
    if any(token in blob for token in ("brewers", "wineries", "distiller")):
        return "alcohol"
    if "alcoholic" in blob and "non-alcoholic" not in blob:
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


YAHOO_PUBLICATION_LAG = timedelta(days=90)

_YAHOO_BALANCE_ALIASES: dict[str, tuple[str, ...]] = {
    "total_debt": ("Total Debt",),
    "short_term_debt": ("Current Debt", "Current Debt And Capital Lease Obligation"),
    "long_term_debt": ("Long Term Debt", "Long Term Debt And Capital Lease Obligation"),
    "cash_and_equiv": ("Cash And Cash Equivalents",),
    "interest_bearing_securities": ("Other Short Term Investments",),
    "receivables": ("Accounts Receivable", "Receivables", "Gross Accounts Receivable"),
    "shares_outstanding": ("Ordinary Shares Number", "Share Issued"),
}

_YAHOO_INCOME_ALIASES: dict[str, tuple[str, ...]] = {
    "total_revenue": ("Total Revenue", "Operating Revenue"),
    "interest_income": ("Interest Income",),
}


def _yahoo_balance_sheet(
    raw: Any,
    symbol: str,
    as_of: Optional[pd.Timestamp] = None,
) -> pd.DataFrame:
    if raw is None or not isinstance(raw, pd.DataFrame) or raw.empty:
        return pd.DataFrame(columns=list(BALANCE_SHEET_COLUMNS) + ["shares_outstanding"])
    rows: list[dict[str, Any]] = []
    for col in raw.columns:
        report = pd.Timestamp(col)
        filed = report + YAHOO_PUBLICATION_LAG
        if as_of is not None and filed > as_of:
            continue
        short_debt = _yahoo_value(raw, col, _YAHOO_BALANCE_ALIASES["short_term_debt"])
        long_debt = _yahoo_value(raw, col, _YAHOO_BALANCE_ALIASES["long_term_debt"])
        tagged_total = _yahoo_value(raw, col, _YAHOO_BALANCE_ALIASES["total_debt"])
        if short_debt is not None or long_debt is not None:
            total_debt = (short_debt or 0.0) + (long_debt or 0.0)
            if tagged_total is not None:
                total_debt = max(total_debt, tagged_total)
        else:
            total_debt = tagged_total if tagged_total is not None else 0.0
        cash = _yahoo_value(raw, col, _YAHOO_BALANCE_ALIASES["cash_and_equiv"]) or 0.0
        ibs = _yahoo_value(raw, col, _YAHOO_BALANCE_ALIASES["interest_bearing_securities"]) or 0.0
        receivables = _yahoo_value(raw, col, _YAHOO_BALANCE_ALIASES["receivables"]) or 0.0
        if total_debt == 0.0 and cash == 0.0 and ibs == 0.0 and receivables == 0.0:
            continue
        rows.append(
            {
                "symbol": symbol,
                "report_date": report.date(),
                "filed_date": filed.date(),
                "total_debt": total_debt,
                "short_term_debt": short_debt,
                "long_term_debt": long_debt,
                "cash_and_equiv": cash,
                "interest_bearing_securities": ibs,
                "receivables": receivables,
                "liquid_assets": cash + ibs,
                "market_cap": None,
                "market_cap_24m": None,
                "shares_outstanding": _yahoo_value(raw, col, _YAHOO_BALANCE_ALIASES["shares_outstanding"]),
            }
        )
    if not rows:
        return pd.DataFrame(columns=list(BALANCE_SHEET_COLUMNS) + ["shares_outstanding"])
    return pd.DataFrame(rows)


def _yahoo_income_statement(
    raw: Any,
    symbol: str,
    as_of: Optional[pd.Timestamp] = None,
) -> pd.DataFrame:
    if raw is None or not isinstance(raw, pd.DataFrame) or raw.empty:
        return pd.DataFrame(columns=list(INCOME_COLUMNS))
    rows: list[dict[str, Any]] = []
    for col in raw.columns:
        report = pd.Timestamp(col)
        filed = report + YAHOO_PUBLICATION_LAG
        if as_of is not None and filed > as_of:
            continue
        revenue = _yahoo_value(raw, col, _YAHOO_INCOME_ALIASES["total_revenue"])
        interest = _yahoo_value(raw, col, _YAHOO_INCOME_ALIASES["interest_income"])
        if revenue is None and interest is None:
            continue
        rows.append(
            {
                "symbol": symbol,
                "report_date": report.date(),
                "filed_date": filed.date(),
                "total_revenue": revenue,
                "interest_income": interest,
                "non_compliant_income": interest,
            }
        )
    if not rows:
        return pd.DataFrame(columns=list(INCOME_COLUMNS))
    return pd.DataFrame(rows)


def _yahoo_value(frame: pd.DataFrame, col: Any, aliases: Sequence[str]) -> Optional[float]:
    lookup = {str(idx).strip().lower(): idx for idx in frame.index}
    for alias in aliases:
        key = lookup.get(alias.strip().lower())
        if key is None:
            continue
        val = frame.loc[key, col]
        if isinstance(val, pd.Series):
            val = val.dropna()
            if val.empty:
                continue
            val = val.iloc[0]
        if pd.isna(val):
            continue
        try:
            return float(val)
        except (TypeError, ValueError):
            continue
    return None
