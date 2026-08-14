"""Financial Modeling Prep (FMP) live data provider."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional, Sequence, Union

import pandas as pd
import requests

from halalquant.base import BALANCE_SHEET_COLUMNS, PRICE_COLUMNS, DateLike
from halalquant.config import resolve_api_key
from halalquant.providers._base_provider import AbstractFetcher

DateLikeInput = Union[str, date, datetime]

INCOME_COLUMNS = (
    "symbol",
    "report_date",
    "filed_date",
    "total_revenue",
    "interest_income",
    "non_compliant_income",
)

DIVIDEND_COLUMNS = (
    "symbol",
    "ex_date",
    "dividend",
    "adj_dividend",
    "record_date",
    "payment_date",
)


class FMPProvider(AbstractFetcher):
    """
    Live FMP adaptor using the stable REST surface.

    Set a key in user code (``hq.api_key = "..."``), pass ``api_key=``,
    or export ``FMP_API_KEY``.
    """

    BASE_URL = "https://financialmodelingprep.com/stable"

    def __init__(self, api_key: Optional[str] = None, **kwargs) -> None:
        super().__init__(api_key=resolve_api_key(api_key), **kwargs)

    def _require_api_key(self) -> str:
        if not self.api_key:
            raise ValueError(
                "FMP API key required. Set hq.api_key = '...', pass api_key=, "
                "or export FMP_API_KEY."
            )
        return self.api_key

    def _params(self, **extra: Any) -> dict[str, Any]:
        params: dict[str, Any] = {"apikey": self._require_api_key()}
        for key, value in extra.items():
            if value is not None:
                params[key] = value
        return params

    def _get(self, path: str, **params: Any) -> Any:
        url = f"{self.BASE_URL}/{path.lstrip('/')}"
        try:
            payload = self._get_json(url, params=self._params(**params))
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            raise ValueError(
                f"FMP request failed ({status}) for '{path}'. "
                "That endpoint may not be included on your FMP plan."
            ) from None
        if isinstance(payload, dict):
            message = payload.get("Error Message") or payload.get("Error") or payload.get("error")
            if message:
                raise ValueError(f"FMP error: {message}")
        return payload

    @staticmethod
    def _as_records(payload: Any) -> list[dict[str, Any]]:
        if payload is None:
            return []
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        if isinstance(payload, dict):
            for key in ("historical", "data", "results"):
                nested = payload.get(key)
                if isinstance(nested, list):
                    return [row for row in nested if isinstance(row, dict)]
            # Single-object error / empty payloads
            if "Error Message" in payload or "error" in payload:
                return []
            if any(k in payload for k in ("date", "symbol", "close", "open")):
                return [payload]
        return []

    @staticmethod
    def _to_date_str(value: DateLikeInput) -> str:
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return str(value)[:10]

    def get_prices(
        self,
        symbols: Sequence[str],
        start: DateLike,
        end: DateLike,
    ) -> pd.DataFrame:
        start_s = self._to_date_str(start)
        end_s = self._to_date_str(end)
        frames: list[pd.DataFrame] = []

        for symbol in symbols:
            rows = self._as_records(
                self._get(
                    "historical-price-eod/full",
                    symbol=symbol,
                    **{"from": start_s, "to": end_s},
                )
            )
            if not rows:
                continue
            frame = pd.DataFrame(rows)
            frame["symbol"] = symbol
            rename = {
                "adjClose": "adj_close",
                "adjusted_close": "adj_close",
            }
            frame = frame.rename(columns=rename)
            if "adj_close" not in frame.columns:
                frame["adj_close"] = frame.get("close")
            keep = [c for c in PRICE_COLUMNS if c in frame.columns]
            frame = frame[keep]
            frames.append(frame)

        if not frames:
            return pd.DataFrame(columns=list(PRICE_COLUMNS))

        out = pd.concat(frames, ignore_index=True)
        out["date"] = pd.to_datetime(out["date"]).dt.date
        for col in ("open", "high", "low", "close", "adj_close"):
            if col in out.columns:
                out[col] = pd.to_numeric(out[col], errors="coerce")
        if "volume" in out.columns:
            out["volume"] = pd.to_numeric(out["volume"], errors="coerce").astype("Int64")
        return out[list(PRICE_COLUMNS)].sort_values(["symbol", "date"]).reset_index(drop=True)

    def get_balance_sheet(
        self,
        symbols: Sequence[str],
        as_of: Optional[DateLike] = None,
        period: str = "annual",
        limit: int = 40,
        attach_market_cap: bool = True,
    ) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        as_of_s = self._to_date_str(as_of) if as_of is not None else None

        for symbol in symbols:
            rows = self._as_records(
                self._get(
                    "balance-sheet-statement",
                    symbol=symbol,
                    period=period,
                    limit=limit,
                )
            )
            if not rows:
                continue
            mapped = [self._map_balance_sheet_row(symbol, row) for row in rows]
            frame = pd.DataFrame(mapped)
            if as_of_s is not None and not frame.empty:
                filed = pd.to_datetime(frame["filed_date"], errors="coerce")
                frame = frame[filed <= pd.Timestamp(as_of_s)]
            if frame.empty:
                continue
            if attach_market_cap:
                frame = self._attach_market_caps(symbol, frame, as_of=as_of_s)
            frames.append(frame)

        if not frames:
            return pd.DataFrame(columns=list(BALANCE_SHEET_COLUMNS))

        out = pd.concat(frames, ignore_index=True)
        for col in ("report_date", "filed_date"):
            out[col] = pd.to_datetime(out[col], errors="coerce").dt.date
        return out[list(BALANCE_SHEET_COLUMNS)].sort_values(
            ["symbol", "report_date"]
        ).reset_index(drop=True)

    def get_income_statement(
        self,
        symbols: Sequence[str],
        as_of: Optional[DateLike] = None,
        period: str = "annual",
        limit: int = 40,
    ) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        as_of_s = self._to_date_str(as_of) if as_of is not None else None

        for symbol in symbols:
            rows = self._as_records(
                self._get(
                    "income-statement",
                    symbol=symbol,
                    period=period,
                    limit=limit,
                )
            )
            if not rows:
                continue
            mapped = [self._map_income_row(symbol, row) for row in rows]
            frame = pd.DataFrame(mapped)
            if as_of_s is not None and not frame.empty:
                filed = pd.to_datetime(frame["filed_date"], errors="coerce")
                frame = frame[filed <= pd.Timestamp(as_of_s)]
            if not frame.empty:
                frames.append(frame)

        if not frames:
            return pd.DataFrame(columns=list(INCOME_COLUMNS))

        out = pd.concat(frames, ignore_index=True)
        for col in ("report_date", "filed_date"):
            out[col] = pd.to_datetime(out[col], errors="coerce").dt.date
        return out[list(INCOME_COLUMNS)].sort_values(
            ["symbol", "report_date"]
        ).reset_index(drop=True)

    def get_dividends(
        self,
        symbols: Sequence[str],
        start: Optional[DateLike] = None,
        end: Optional[DateLike] = None,
    ) -> pd.DataFrame:
        start_s = self._to_date_str(start) if start is not None else None
        end_s = self._to_date_str(end) if end is not None else None
        frames: list[pd.DataFrame] = []

        for symbol in symbols:
            rows = self._as_records(self._get("dividends", symbol=symbol))
            if not rows:
                continue
            mapped = [self._map_dividend_row(symbol, row) for row in rows]
            frame = pd.DataFrame(mapped)
            if frame.empty:
                continue
            ex_dates = pd.to_datetime(frame["ex_date"], errors="coerce")
            if start_s is not None:
                frame = frame[ex_dates >= pd.Timestamp(start_s)]
                ex_dates = pd.to_datetime(frame["ex_date"], errors="coerce")
            if end_s is not None:
                frame = frame[ex_dates <= pd.Timestamp(end_s)]
            if not frame.empty:
                frames.append(frame)

        if not frames:
            return pd.DataFrame(columns=list(DIVIDEND_COLUMNS))

        out = pd.concat(frames, ignore_index=True)
        out["ex_date"] = pd.to_datetime(out["ex_date"], errors="coerce").dt.date
        for col in ("record_date", "payment_date"):
            out[col] = pd.to_datetime(out[col], errors="coerce").dt.date
        return out[list(DIVIDEND_COLUMNS)].sort_values(
            ["symbol", "ex_date"]
        ).reset_index(drop=True)

    def get_profile(self, symbols: Sequence[str]) -> pd.DataFrame:
        """Return company profile rows (sector, industry, marketCap, …)."""
        rows: list[dict[str, Any]] = []
        for symbol in symbols:
            payload = self._as_records(self._get("profile", symbol=symbol))
            for row in payload:
                rows.append(
                    {
                        "symbol": symbol,
                        "sector": row.get("sector"),
                        "industry": row.get("industry"),
                        "company_name": row.get("companyName") or row.get("company_name"),
                        "market_cap": row.get("marketCap") or row.get("mktCap"),
                    }
                )
        if not rows:
            return pd.DataFrame(
                columns=["symbol", "sector", "industry", "company_name", "market_cap"]
            )
        return pd.DataFrame(rows)

    def get_sector_map(self, symbols: Sequence[str]) -> dict[str, str]:
        profile = self.get_profile(symbols)
        if profile.empty:
            return {}
        out: dict[str, str] = {}
        for _, row in profile.iterrows():
            sector = row.get("sector") or row.get("industry") or ""
            if sector:
                out[str(row["symbol"])] = str(sector)
        return out

    def get_historical_market_cap(
        self,
        symbol: str,
        start: Optional[DateLike] = None,
        end: Optional[DateLike] = None,
        limit: int = 750,
    ) -> pd.DataFrame:
        params: dict[str, Any] = {"symbol": symbol, "limit": limit}
        if start is not None:
            params["from"] = self._to_date_str(start)
        if end is not None:
            params["to"] = self._to_date_str(end)
        rows = self._as_records(self._get("historical-market-capitalization", **params))
        if not rows:
            return pd.DataFrame(columns=["symbol", "date", "market_cap"])
        frame = pd.DataFrame(rows)
        frame["symbol"] = symbol
        if "marketCap" in frame.columns:
            frame = frame.rename(columns={"marketCap": "market_cap"})
        elif "market_cap" not in frame.columns:
            frame["market_cap"] = pd.NA
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
        frame["market_cap"] = pd.to_numeric(frame["market_cap"], errors="coerce")
        return frame[["symbol", "date", "market_cap"]].dropna(subset=["date"]).sort_values(
            "date"
        )

    def trailing_market_cap_average(
        self,
        symbol: str,
        as_of: Optional[DateLike] = None,
        months: int = 24,
    ) -> Optional[float]:
        """Average market capitalization over the trailing ``months`` window."""
        end = self._to_date_str(as_of) if as_of is not None else date.today().isoformat()
        end_d = datetime.strptime(end, "%Y-%m-%d").date()
        start_d = end_d - timedelta(days=int(months * 30.44) + 14)
        hist = self.get_historical_market_cap(symbol, start=start_d, end=end_d)
        if hist.empty:
            # Fall back to latest spot market cap
            spot = self._as_records(self._get("market-capitalization", symbol=symbol))
            if not spot:
                return None
            value = spot[0].get("marketCap") or spot[0].get("market_cap")
            return float(value) if value is not None else None
        window = hist[
            (pd.to_datetime(hist["date"]) >= pd.Timestamp(start_d))
            & (pd.to_datetime(hist["date"]) <= pd.Timestamp(end_d))
        ]
        if window.empty or window["market_cap"].isna().all():
            return None
        return float(window["market_cap"].mean())

    def _attach_market_caps(
        self,
        symbol: str,
        frame: pd.DataFrame,
        as_of: Optional[str] = None,
    ) -> pd.DataFrame:
        out = frame.copy()
        hist = self.get_historical_market_cap(symbol, limit=750)
        if hist.empty:
            spot_rows = self._as_records(self._get("market-capitalization", symbol=symbol))
            spot = None
            if spot_rows:
                spot = _num(spot_rows[0].get("marketCap") or spot_rows[0].get("market_cap"))
            out["market_cap"] = spot
            out["market_cap_24m"] = spot
            return out

        hist = hist.sort_values("date").reset_index(drop=True)
        hist_dates = pd.to_datetime(hist["date"])
        hist_values = hist["market_cap"].astype(float)
        window = pd.Timedelta(days=int(24 * 30.44))

        market_caps: list[Optional[float]] = []
        market_caps_24m: list[Optional[float]] = []
        fallback_end = pd.Timestamp(as_of) if as_of else hist_dates.max()

        for _, row in out.iterrows():
            report = row.get("report_date")
            end_ts = pd.Timestamp(report) if report is not None else fallback_end
            known = hist_values[hist_dates <= end_ts]
            spot = float(known.iloc[-1]) if not known.empty else None
            start_ts = end_ts - window
            trail = hist_values[(hist_dates >= start_ts) & (hist_dates <= end_ts)]
            avg = float(trail.mean()) if not trail.empty else spot
            market_caps.append(spot)
            market_caps_24m.append(avg)

        out["market_cap"] = market_caps
        out["market_cap_24m"] = market_caps_24m
        return out
    @staticmethod
    def _map_balance_sheet_row(symbol: str, row: dict[str, Any]) -> dict[str, Any]:
        cash = _num(row.get("cashAndCashEquivalents") or row.get("cashAndShortTermInvestments"))
        short_investments = _num(row.get("shortTermInvestments"))
        cash_and_equiv = cash if cash is not None else 0.0
        ibs = short_investments if short_investments is not None else 0.0
        receivables = _num(row.get("netReceivables") or row.get("accountsReceivables"))
        short_debt = _num(row.get("shortTermDebt"))
        long_debt = _num(row.get("longTermDebt"))
        total_debt = _num(row.get("totalDebt"))
        if total_debt is None:
            total_debt = (short_debt or 0.0) + (long_debt or 0.0)

        report_date = row.get("date")
        filed = (
            row.get("filingDate")
            or row.get("acceptedDate")
            or row.get("fillingDate")
            or report_date
        )
        return {
            "symbol": symbol,
            "report_date": report_date,
            "filed_date": filed,
            "total_debt": total_debt,
            "short_term_debt": short_debt,
            "long_term_debt": long_debt,
            "cash_and_equiv": cash_and_equiv,
            "interest_bearing_securities": ibs,
            "receivables": receivables if receivables is not None else 0.0,
            "liquid_assets": (cash_and_equiv or 0.0) + (ibs or 0.0),
            "market_cap": None,
            "market_cap_24m": None,
        }

    @staticmethod
    def _map_income_row(symbol: str, row: dict[str, Any]) -> dict[str, Any]:
        revenue = _num(row.get("revenue") or row.get("totalRevenue"))
        interest_income = _num(
            row.get("interestIncome")
            or row.get("interestAndInvestmentIncome")
            or row.get("netInterestIncome")
        )
        # Conservative proxy: treat interest income as non-compliant income when
        # a finer breakdown is unavailable from the vendor.
        non_compliant = interest_income
        report_date = row.get("date")
        filed = (
            row.get("filingDate")
            or row.get("acceptedDate")
            or row.get("fillingDate")
            or report_date
        )
        return {
            "symbol": symbol,
            "report_date": report_date,
            "filed_date": filed,
            "total_revenue": revenue,
            "interest_income": interest_income,
            "non_compliant_income": non_compliant,
        }

    @staticmethod
    def _map_dividend_row(symbol: str, row: dict[str, Any]) -> dict[str, Any]:
        amount = _num(row.get("dividend") or row.get("adjDividend") or row.get("adj_dividend"))
        adj = _num(row.get("adjDividend") or row.get("adj_dividend") or row.get("dividend"))
        return {
            "symbol": symbol,
            "ex_date": row.get("date") or row.get("exDate") or row.get("ex_date"),
            "dividend": amount,
            "adj_dividend": adj,
            "record_date": row.get("recordDate") or row.get("record_date"),
            "payment_date": row.get("paymentDate") or row.get("payment_date"),
        }


def _num(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
