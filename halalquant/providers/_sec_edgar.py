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

# First tag that covers a given fiscal year wins. Later tags fill years the
# preferred tag does not report (banks/insurers use different us-gaap names).
BALANCE_TAGS: dict[str, tuple[str, ...]] = {
    "short_term_debt": (
        "DebtCurrent",
        "ShortTermBorrowings",
        "CommercialPaper",
        "FederalFundsPurchasedAndSecuritiesSoldUnderAgreementsToRepurchase",
        "OtherShortTermBorrowings",
    ),
    "long_term_debt": (
        "LongTermDebtNoncurrent",
        "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
        "LongTermDebtAndCapitalLeaseObligations",
        "LongTermDebt",
    ),
    "total_debt": ("LongTermDebtAndCapitalLeaseObligations", "LongTermDebt"),
    "interest_bearing_liabilities": (
        "Deposits",
        "PolicyholderContractDeposits",
        "PolicyholderFunds",
    ),
    "cash_and_equiv": (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashAndDueFromBanks",
        "CashCashEquivalentsAndShortTermInvestments",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "Cash",
    ),
    "interest_bearing_securities": (
        "ShortTermInvestments",
        "MarketableSecuritiesCurrent",
        "AvailableForSaleSecuritiesCurrent",
        "InterestBearingDepositsInBanks",
        "DebtSecuritiesAvailableForSaleExcludingAccruedInterest",
        "AvailableForSaleSecuritiesDebtSecurities",
        "DebtSecuritiesHeldToMaturityExcludingAccruedInterestAfterAllowanceForCreditLoss",
        "AvailableForSaleSecurities",
    ),
    "receivables": (
        "AccountsReceivableNetCurrent",
        "AccountsReceivableNet",
        "ReceivablesNetCurrent",
        "FinancingReceivableExcludingAccruedInterestAfterAllowanceForCreditLoss",
        "LoansAndLeasesReceivableNetOfDeferredIncome",
        "ReinsuranceRecoverables",
        "PremiumsReceivable",
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
        "InterestAndFeeIncomeLoansAndLeases",
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

    def has_cik(self, symbol: str) -> bool:
        """True when the SEC ticker map has a CIK for this symbol."""
        try:
            return str(symbol).upper() in self._ticker_map()
        except ValueError:
            return False

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
            deposits = _val(series["interest_bearing_liabilities"], end) or 0.0
            total_debt = float(total_debt) + deposits
            cash = _val(series["cash_and_equiv"], end) or 0.0
            ibs = _val(series["interest_bearing_securities"], end) or 0.0
            receivables = _val(series["receivables"], end) or 0.0
            if total_debt == 0.0 and cash == 0.0 and ibs == 0.0 and receivables == 0.0:
                continue
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
            if revenue is None and interest is None:
                continue
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
    """
    Map report end date -> (first_filed_date, value).

    Tags are tried in order. A later tag only fills fiscal years the earlier
    tags do not cover, so a stale industrial tag does not hide a bank tag.

    ``first_filed_date`` is the earliest 10-K that reported that year (not a
    later restatement). ``value`` is the latest revision with filed <= as_of.
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    dei = facts.get("facts", {}).get("dei", {})
    as_of_ts = pd.Timestamp(as_of) if as_of else None
    unit_keys = ("shares",) if share_units else ("USD",)
    best: dict[str, tuple[str, float]] = {}

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
        by_end: dict[str, list[tuple[str, float]]] = {}
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
            try:
                by_end.setdefault(str(end), []).append((str(filed), float(val)))
            except (TypeError, ValueError):
                continue
        for end, pairs in by_end.items():
            if end in best:
                continue
            usable = [
                (filed, val)
                for filed, val in pairs
                if as_of_ts is None or pd.Timestamp(filed) <= as_of_ts
            ]
            if not usable:
                continue
            first_filed = min(filed for filed, _ in usable)
            _, latest_val = max(usable, key=lambda item: item[0])
            best[end] = (first_filed, latest_val)
    return best


def _val(series: dict[str, tuple[str, float]], end: str) -> Optional[float]:
    item = series.get(end)
    return item[1] if item else None


def _filed_for(series_map: dict[str, dict[str, tuple[str, float]]], end: str) -> Optional[str]:
    dates = [values[end][0] for values in series_map.values() if end in values]
    return min(dates) if dates else None
