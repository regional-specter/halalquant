"""SEC EDGAR companyfacts adaptor for balance sheets and income."""

from __future__ import annotations

import os
from typing import Any, Optional, Sequence

import pandas as pd
import requests

from halalquant.base import BALANCE_SHEET_COLUMNS, DateLike, INCOME_COLUMNS
from halalquant.providers._base_provider import AbstractFetcher

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# First matching tag wins for each field.
BALANCE_TAGS: dict[str, tuple[str, ...]] = {
    "short_term_debt": ("DebtCurrent", "ShortTermBorrowings", "CommercialPaper"),
    "long_term_debt": (
        "LongTermDebtNoncurrent",
        "LongTermDebt",
        "LongTermDebtAndCapitalLeaseObligations",
    ),
    "total_debt": ("LongTermDebtAndCapitalLeaseObligations", "LongTermDebt"),
    "cash_and_equiv": (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsAndShortTermInvestments",
        "Cash",
    ),
    "interest_bearing_securities": (
        "ShortTermInvestments",
        "MarketableSecuritiesCurrent",
        "AvailableForSaleSecuritiesCurrent",
    ),
    "receivables": (
        "AccountsReceivableNetCurrent",
        "AccountsReceivableNet",
        "ReceivablesNetCurrent",
    ),
    "shares_outstanding": (
        "CommonStockSharesOutstanding",
        "EntityCommonStockSharesOutstanding",
        "WeightedAverageNumberOfDilutedSharesOutstanding",
    ),
}

INCOME_TAGS: dict[str, tuple[str, ...]] = {
    "total_revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ),
    "interest_income": (
        "InvestmentIncomeInterest",
        "InterestAndDividendIncomeOperating",
        "InterestIncomeOperating",
        "InterestAndOtherIncome",
        "InterestIncomeSecurities",
        "InvestmentIncomeInterestAndDividend",
        "InterestAndDividendIncome",
    ),
}

ANNUAL_FORMS = {"10-K", "10-K/A"}


class SECEdgarProvider(AbstractFetcher):
    """
    SEC EDGAR adaptor using the public companyfacts JSON API.

    Prices are not served by EDGAR. Balance sheets and income come from XBRL
    tags. Market cap is left empty for the caller to fill from price history.
    """

    BASE_URL = "https://data.sec.gov"

    def __init__(self, user_agent: Optional[str] = None, **kwargs) -> None:
        super().__init__(**kwargs)
        ua = (
            user_agent
            or os.getenv("HALALQUANT_SEC_UA")
            or "HalalQuant/0.1.0 support@halalquant.local"
        )
        self.session.headers.update(
            {
                "User-Agent": ua,
                "Accept-Encoding": "gzip, deflate",
            }
        )
        self._tickers: Optional[dict[str, int]] = None
        self._facts_cache: dict[str, Optional[dict[str, Any]]] = {}

    def get_prices(
        self,
        symbols: Sequence[str],
        start: DateLike,
        end: DateLike,
    ) -> pd.DataFrame:
        _ = (symbols, start, end)
        raise NotImplementedError(
            "SEC EDGAR does not provide market prices. Use YFinanceProvider for OHLCV."
        )

    def get_balance_sheet(
        self,
        symbols: Sequence[str],
        as_of: Optional[DateLike] = None,
    ) -> pd.DataFrame:
        as_of_s = str(as_of)[:10] if as_of is not None else None
        frames: list[pd.DataFrame] = []
        for symbol in symbols:
            facts = self._companyfacts(symbol)
            if not facts:
                continue
            frame = self._facts_to_balance_sheet(symbol, facts, as_of=as_of_s)
            if not frame.empty:
                frames.append(frame)
        if not frames:
            return pd.DataFrame(columns=list(BALANCE_SHEET_COLUMNS) + ["shares_outstanding"])
        out = pd.concat(frames, ignore_index=True)
        return out.sort_values(["symbol", "report_date"]).reset_index(drop=True)

    def get_income_statement(
        self,
        symbols: Sequence[str],
        as_of: Optional[DateLike] = None,
    ) -> pd.DataFrame:
        as_of_s = str(as_of)[:10] if as_of is not None else None
        frames: list[pd.DataFrame] = []
        for symbol in symbols:
            facts = self._companyfacts(symbol)
            if not facts:
                continue
            frame = self._facts_to_income(symbol, facts, as_of=as_of_s)
            if not frame.empty:
                frames.append(frame)
        if not frames:
            return pd.DataFrame(columns=list(INCOME_COLUMNS))
        out = pd.concat(frames, ignore_index=True)
        return out.sort_values(["symbol", "report_date"]).reset_index(drop=True)

    def _ticker_map(self) -> dict[str, int]:
        if self._tickers is None:
            try:
                payload = self._get_json(TICKER_MAP_URL)
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else "unknown"
                raise ValueError(
                    f"SEC ticker map request failed ({status}). "
                    "Set HALALQUANT_SEC_UA to a User-Agent that includes a contact email."
                ) from None
            mapping: dict[str, int] = {}
            if isinstance(payload, dict):
                for row in payload.values():
                    if not isinstance(row, dict):
                        continue
                    ticker = str(row.get("ticker", "")).upper()
                    cik = row.get("cik_str")
                    if ticker and cik is not None:
                        mapping[ticker] = int(cik)
            self._tickers = mapping
        return self._tickers

    def _companyfacts(self, symbol: str) -> Optional[dict[str, Any]]:
        key = symbol.upper()
        if key in self._facts_cache:
            return self._facts_cache[key]
        cik = self._ticker_map().get(key)
        if cik is None:
            self._facts_cache[key] = None
            return None
        url = COMPANYFACTS_URL.format(cik=f"{cik:010d}")
        try:
            payload = self._get_json(url)
        except requests.HTTPError:
            self._facts_cache[key] = None
            return None
        facts = payload if isinstance(payload, dict) else None
        self._facts_cache[key] = facts
        return facts

    def _facts_to_balance_sheet(
        self,
        symbol: str,
        facts: dict[str, Any],
        as_of: Optional[str] = None,
    ) -> pd.DataFrame:
        series = {
            field: _annual_series(facts, tags, as_of=as_of, share_units=field == "shares_outstanding")
            for field, tags in BALANCE_TAGS.items()
        }
        ends = set()
        for values in series.values():
            ends.update(values.keys())
        if not ends:
            return pd.DataFrame()

        rows: list[dict[str, Any]] = []
        for end in sorted(ends):
            short_debt = _val(series["short_term_debt"], end)
            long_debt = _val(series["long_term_debt"], end)
            tagged_total = _val(series["total_debt"], end)
            if short_debt is not None or long_debt is not None:
                total_debt = (short_debt or 0.0) + (long_debt or 0.0)
            else:
                total_debt = tagged_total if tagged_total is not None else 0.0
            cash = _val(series["cash_and_equiv"], end) or 0.0
            ibs = _val(series["interest_bearing_securities"], end) or 0.0
            receivables = _val(series["receivables"], end) or 0.0
            filed = _filed_for(series, end)
            rows.append(
                {
                    "symbol": symbol,
                    "report_date": end,
                    "filed_date": filed or end,
                    "total_debt": total_debt,
                    "short_term_debt": short_debt,
                    "long_term_debt": long_debt,
                    "cash_and_equiv": cash,
                    "interest_bearing_securities": ibs,
                    "receivables": receivables,
                    "liquid_assets": cash + ibs,
                    "market_cap": None,
                    "market_cap_24m": None,
                    "shares_outstanding": _val(series["shares_outstanding"], end),
                }
            )
        frame = pd.DataFrame(rows)
        for col in ("report_date", "filed_date"):
            frame[col] = pd.to_datetime(frame[col], errors="coerce").dt.date
        return frame

    def _facts_to_income(
        self,
        symbol: str,
        facts: dict[str, Any],
        as_of: Optional[str] = None,
    ) -> pd.DataFrame:
        series = {
            field: _annual_series(facts, tags, as_of=as_of)
            for field, tags in INCOME_TAGS.items()
        }
        ends = set()
        for values in series.values():
            ends.update(values.keys())
        if not ends:
            return pd.DataFrame(columns=list(INCOME_COLUMNS))

        rows: list[dict[str, Any]] = []
        for end in sorted(ends):
            revenue = _val(series["total_revenue"], end)
            interest = _val(series["interest_income"], end)
            filed = _filed_for(series, end)
            rows.append(
                {
                    "symbol": symbol,
                    "report_date": end,
                    "filed_date": filed or end,
                    "total_revenue": revenue,
                    "interest_income": interest,
                    "non_compliant_income": interest,
                }
            )
        frame = pd.DataFrame(rows)
        for col in ("report_date", "filed_date"):
            frame[col] = pd.to_datetime(frame[col], errors="coerce").dt.date
        return frame[list(INCOME_COLUMNS)]


def _annual_series(
    facts: dict[str, Any],
    tags: Sequence[str],
    as_of: Optional[str] = None,
    share_units: bool = False,
) -> dict[str, tuple[str, float]]:
    """Map report end date -> (filed_date, value) for the first tag with annual facts."""
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    dei = facts.get("facts", {}).get("dei", {})
    as_of_ts = pd.Timestamp(as_of) if as_of else None
    unit_keys = ("shares",) if share_units else ("USD",)

    for tag in tags:
        node = us_gaap.get(tag) or dei.get(tag)
        if not isinstance(node, dict):
            continue
        units = node.get("units", {})
        points: list[dict[str, Any]] = []
        for key in unit_keys:
            points.extend(units.get(key, []))
        if not points:
            for values in units.values():
                if isinstance(values, list):
                    points.extend(values)
        best: dict[str, tuple[str, float]] = {}
        for point in points:
            if not isinstance(point, dict):
                continue
            end = point.get("end")
            filed = point.get("filed") or end
            val = point.get("val")
            form = str(point.get("form") or "")
            fp = str(point.get("fp") or "")
            annual = form in ANNUAL_FORMS or fp == "FY"
            if not annual or not end or val is None:
                continue
            if as_of_ts is not None and filed and pd.Timestamp(filed) > as_of_ts:
                continue
            prev = best.get(end)
            if prev is None or str(filed) >= prev[0]:
                try:
                    best[end] = (str(filed), float(val))
                except (TypeError, ValueError):
                    continue
        if best:
            return best
    return {}


def _val(series: dict[str, tuple[str, float]], end: str) -> Optional[float]:
    item = series.get(end)
    return item[1] if item else None


def _filed_for(series_map: dict[str, dict[str, tuple[str, float]]], end: str) -> Optional[str]:
    for values in series_map.values():
        item = values.get(end)
        if item:
            return item[0]
    return None
