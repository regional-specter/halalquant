"""SEC EDGAR companyfacts adaptor for balance sheets and income."""

from __future__ import annotations

import gzip
import json
import os
from collections import OrderedDict, defaultdict
from pathlib import Path
from typing import Any, Optional, Sequence, Union

import pandas as pd
import requests

from halalquant.base import BALANCE_SHEET_COLUMNS, DateLike, INCOME_COLUMNS
from halalquant.providers._base_provider import AbstractFetcher

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

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
    "ebitda": ("EBITDA",),
    "operating_income": ("OperatingIncomeLoss",),
    "depreciation": (
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
    ),
    "operating_cash_flow": (
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ),
    "capital_expenditure": (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ),
}

ANNUAL_FORMS = {"10-K", "10-K/A"}
QUARTERLY_FORMS = {"10-Q", "10-Q/A"}
STATEMENT_FORMS = ANNUAL_FORMS | QUARTERLY_FORMS
QUARTER_PERIODS = {"Q1", "Q2", "Q3", "Q4"}
DEFAULT_SEC_USER_AGENT = (
    "HalalQuant/0.3.0 (halalquant-research@example.com; override with HALALQUANT_SEC_UA)"
)

# SEC company_tickers.json can point a listed ticker at a new holding-company
# CIK that has almost no XBRL. Prefer the operating-company 10-K instead.
CIK_OVERRIDES: dict[str, int] = {
    "XOM": 34088,  # Exxon Mobil Corporation
}


class SECEdgarProvider(AbstractFetcher):
    """
    SEC EDGAR adaptor using the public companyfacts JSON API.

    Prices are not served by EDGAR. Balance sheets and income come from XBRL
    tags. Market cap is left empty for the caller to fill from price history.
    """

    BASE_URL = "https://data.sec.gov"

    def __init__(
        self,
        user_agent: Optional[str] = None,
        facts_dir: Optional[Union[str, Path, bool]] = None,
        refresh_facts: bool = False,
        min_interval: float = 0.12,
        **kwargs,
    ) -> None:
        kwargs.setdefault("min_interval", min_interval)
        super().__init__(**kwargs)
        ua = user_agent or os.getenv("HALALQUANT_SEC_UA") or DEFAULT_SEC_USER_AGENT
        self.session.headers.update(
            {
                "User-Agent": ua,
                "Accept-Encoding": "gzip, deflate",
            }
        )
        self._tickers: Optional[dict[str, int]] = None
        self._facts_cache: OrderedDict[str, Optional[dict[str, Any]]] = OrderedDict()
        self._facts_cache_limit = 32
        self.refresh_facts = refresh_facts
        if facts_dir is False:
            self.facts_dir: Optional[Path] = None
        elif facts_dir is None:
            from halalquant.database._cache import default_facts_dir

            self.facts_dir = default_facts_dir()
        else:
            self.facts_dir = Path(facts_dir)

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
            mapping.update(CIK_OVERRIDES)
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
        if not self.refresh_facts and key in self._facts_cache:
            self._facts_cache.move_to_end(key)
            return self._facts_cache[key]
        if not self.refresh_facts:
            disk = self._read_facts_disk(key)
            if disk is not None:
                self._remember_facts(key, disk)
                return disk
        cik = self._ticker_map().get(key)
        if cik is None:
            self._remember_facts(key, None)
            return None
        url = COMPANYFACTS_URL.format(cik=f"{cik:010d}")
        try:
            payload = self._get_json(url)
        except requests.HTTPError:
            self._remember_facts(key, None)
            return None
        facts = payload if isinstance(payload, dict) else None
        if facts:
            self._write_facts_disk(key, facts)
        self._remember_facts(key, facts)
        return facts

    def invalidate_facts(self, symbol: str) -> None:
        """Drop one cached companyfacts blob so the next read hits EDGAR."""
        key = str(symbol).upper()
        self._facts_cache.pop(key, None)
        path = self._facts_path(key)
        if path is not None and path.exists():
            try:
                path.unlink()
            except OSError:
                return

    def recent_filings(self, symbol: str) -> pd.DataFrame:
        """10-Q / 10-K rows from the SEC submissions index."""
        frame, _accession, _etag, _unchanged = self.submission_index(symbol)
        return frame

    def submission_index(
        self,
        symbol: str,
        etag: Optional[str] = None,
    ) -> tuple[pd.DataFrame, Optional[str], Optional[str], bool]:
        """
        Submissions index for one CIK.

        Returns ``(filings, newest_statement_accession, etag, not_modified)``.
        Pass the previous ``ETag`` so an unchanged CIK comes back as HTTP 304
        and companyfacts is not downloaded again.
        """
        empty = pd.DataFrame(
            columns=[
                "symbol",
                "cik",
                "form",
                "report_date",
                "filed_date",
                "fiscal_period",
                "accession",
            ]
        )
        try:
            cik = self._ticker_map().get(str(symbol).upper())
        except ValueError:
            return empty, None, etag, False
        if cik is None:
            return empty, None, etag, False
        url = SUBMISSIONS_URL.format(cik=f"{cik:010d}")
        headers = {"If-None-Match": etag} if etag else None
        try:
            response = self._request(url, headers=headers)
        except requests.HTTPError:
            return empty, None, etag, False
        new_etag = response.headers.get("ETag") or etag
        if response.status_code == 304:
            return empty, None, new_etag, True
        try:
            payload = response.json()
        except ValueError:
            return empty, None, new_etag, False
        recent = {}
        if isinstance(payload, dict):
            recent = payload.get("filings", {}).get("recent", {}) or {}
        forms = list(recent.get("form") or [])
        filed = list(recent.get("filingDate") or [])
        reports = list(recent.get("reportDate") or [])
        accessions = list(recent.get("accessionNumber") or [])
        newest: Optional[str] = None
        rows: list[dict[str, Any]] = []
        for idx, form in enumerate(forms):
            form_s = str(form or "").upper()
            accession = str(accessions[idx]) if idx < len(accessions) else ""
            if newest is None and form_s in STATEMENT_FORMS and accession:
                newest = accession
            filed_date = filed[idx] if idx < len(filed) else None
            report_date = reports[idx] if idx < len(reports) else None
            if form_s not in STATEMENT_FORMS or not filed_date:
                continue
            rows.append(
                {
                    "symbol": str(symbol).upper(),
                    "cik": str(int(cik)),
                    "form": form_s,
                    "report_date": report_date or None,
                    "filed_date": filed_date,
                    "fiscal_period": "",
                    "accession": accession,
                }
            )
        if not rows:
            return empty, newest, new_etag, False
        frame = pd.DataFrame(rows)
        for col in ("report_date", "filed_date"):
            frame[col] = pd.to_datetime(frame[col], errors="coerce").dt.date
        frame = frame.dropna(subset=["filed_date"]).reset_index(drop=True)
        return frame, newest, new_etag, False

    def _remember_facts(self, key: str, facts: Optional[dict[str, Any]]) -> None:
        self._facts_cache[key] = facts
        self._facts_cache.move_to_end(key)
        while len(self._facts_cache) > self._facts_cache_limit:
            self._facts_cache.popitem(last=False)

    def _facts_path(self, key: str) -> Optional[Path]:
        if self.facts_dir is None:
            return None
        return self.facts_dir / f"{key}.json.gz"

    def _read_facts_disk(self, key: str) -> Optional[dict[str, Any]]:
        path = self._facts_path(key)
        if path is None or not path.exists():
            return None
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _write_facts_disk(self, key: str, facts: dict[str, Any]) -> None:
        path = self._facts_path(key)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                json.dump(facts, handle)
        except OSError:
            return

    def _facts_to_balance_sheet(
        self,
        symbol: str,
        facts: dict[str, Any],
        as_of: Optional[str] = None,
    ) -> pd.DataFrame:
        merged: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(dict)
        for field, tags in BALANCE_TAGS.items():
            for end, filed, val, form, period, days in _iter_statement_points(
                facts,
                tags,
                kind="annual",
                as_of=as_of,
                share_units=field == "shares_outstanding",
            ):
                slot = merged[(end, filed, period)]
                slot[field] = val
                slot["form"] = form
                slot["fiscal_period"] = period
                if days is not None:
                    slot["period_days"] = days
            for end, filed, val, form, period, days in _iter_statement_points(
                facts,
                tags,
                kind="quarter",
                as_of=as_of,
                share_units=field == "shares_outstanding",
            ):
                slot = merged[(end, filed, period)]
                slot[field] = val
                slot["form"] = form
                slot["fiscal_period"] = period

        if not merged:
            return pd.DataFrame()

        rows: list[dict[str, Any]] = []
        for (end, filed, period), vals in sorted(merged.items()):
            short_debt = vals.get("short_term_debt")
            long_debt = vals.get("long_term_debt")
            tagged_total = vals.get("total_debt")
            if short_debt is not None or long_debt is not None:
                total_debt = (short_debt or 0.0) + (long_debt or 0.0)
            else:
                total_debt = tagged_total if tagged_total is not None else 0.0
            deposits = vals.get("interest_bearing_liabilities") or 0.0
            total_debt = float(total_debt) + deposits
            cash = vals.get("cash_and_equiv") or 0.0
            ibs = vals.get("interest_bearing_securities") or 0.0
            receivables = vals.get("receivables") or 0.0
            if total_debt == 0.0 and cash == 0.0 and ibs == 0.0 and receivables == 0.0:
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "report_date": end,
                    "filed_date": filed,
                    "total_debt": total_debt,
                    "short_term_debt": short_debt,
                    "long_term_debt": long_debt,
                    "cash_and_equiv": cash,
                    "interest_bearing_securities": ibs,
                    "receivables": receivables,
                    "liquid_assets": cash + ibs,
                    "market_cap": None,
                    "market_cap_24m": None,
                    "shares_outstanding": vals.get("shares_outstanding"),
                    "form": vals.get("form") or "",
                    "fiscal_period": vals.get("fiscal_period") or period or "",
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
        merged: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(dict)
        for field, tags in INCOME_TAGS.items():
            for kind in ("annual", "quarter"):
                for end, filed, val, form, period, days in _iter_statement_points(
                    facts,
                    tags,
                    kind=kind,
                    as_of=as_of,
                ):
                    slot = merged[(end, filed, period)]
                    slot[field] = val
                    slot["form"] = form
                    slot["fiscal_period"] = period
                    if days is not None:
                        slot["period_days"] = days

        if not merged:
            return pd.DataFrame(columns=list(INCOME_COLUMNS) + ["form", "fiscal_period"])

        rows: list[dict[str, Any]] = []
        for (end, filed, period), vals in sorted(merged.items()):
            revenue = vals.get("total_revenue")
            interest = vals.get("interest_income")
            ocf = vals.get("operating_cash_flow")
            capex = vals.get("capital_expenditure")
            ebitda = vals.get("ebitda")
            operating_income = vals.get("operating_income")
            depreciation = vals.get("depreciation")
            if ebitda is None and operating_income is not None and depreciation is not None:
                ebitda = float(operating_income) + abs(float(depreciation))
            fcf = _free_cash_flow(ocf, capex)
            if (
                revenue is None
                and interest is None
                and ebitda is None
                and ocf is None
                and capex is None
            ):
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "report_date": end,
                    "filed_date": filed,
                    "total_revenue": revenue,
                    "interest_income": interest,
                    "non_compliant_income": interest,
                    "ebitda": ebitda,
                    "operating_cash_flow": ocf,
                    "capital_expenditure": capex,
                    "free_cash_flow": fcf,
                    "form": vals.get("form") or "",
                    "fiscal_period": vals.get("fiscal_period") or period or "",
                    "period_days": vals.get("period_days"),
                }
            )
        frame = pd.DataFrame(rows)
        for col in ("report_date", "filed_date"):
            frame[col] = pd.to_datetime(frame[col], errors="coerce").dt.date
        columns = list(INCOME_COLUMNS) + ["form", "fiscal_period", "period_days"]
        return frame[columns]


def _iter_annual_points(
    facts: dict[str, Any],
    tags: Sequence[str],
    as_of: Optional[str] = None,
    share_units: bool = False,
) -> list[tuple[str, str, float]]:
    """Annual (report_end, filed_date, value) rows. 10-Q facts are excluded."""
    return [
        (end, filed, val)
        for end, filed, val, _form, _period, _days in _iter_statement_points(
            facts,
            tags,
            kind="annual",
            as_of=as_of,
            share_units=share_units,
        )
    ]


def _iter_statement_points(
    facts: dict[str, Any],
    tags: Sequence[str],
    kind: str,
    as_of: Optional[str] = None,
    share_units: bool = False,
) -> list[tuple[str, str, float, str, str, Optional[int]]]:
    """
    Return ``(report_end, filed_date, value, form, fiscal_period)`` rows.

    ``kind="annual"`` keeps 10-K / FY facts (same selection as v0.1).
    ``kind="quarter"`` keeps 10-Q facts and ~90-day Q4 facts. Year-to-date
    10-Q durations are skipped so a nine-month total is not treated as a quarter.
    Each filing revision is kept so point-in-time screens use the values that
    were public on the snapshot date.
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    dei = facts.get("facts", {}).get("dei", {})
    as_of_ts = pd.Timestamp(as_of) if as_of else None
    unit_keys = ("shares",) if share_units else ("USD",)
    seen: set[tuple[str, str, str]] = set()
    rows: list[tuple[str, str, float, str, str, Optional[int]]] = []

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
        for point in points:
            if not isinstance(point, dict):
                continue
            end = point.get("end")
            filed = point.get("filed") or end
            val = point.get("val")
            if not end or filed is None or val is None:
                continue
            # A 10-K often repeats the same period end as a 90-day Q4 slice.
            # Keep that slice for TTM only. The annual row stays the full-year fact.
            if kind == "annual":
                duration = _duration_days(point)
                if duration is not None and 70 <= duration <= 120:
                    continue
            parsed = _classify_point(point, kind)
            if parsed is None:
                continue
            form, period = parsed
            if as_of_ts is not None and pd.Timestamp(filed) > as_of_ts:
                continue
            # Annual selection stays one value per (period end, filed date),
            # matching v0.1. Quarters are distinct fiscal periods.
            end_s, filed_s = str(end), str(filed)
            key = (end_s, filed_s, "" if kind == "annual" else period)
            if key in seen:
                continue
            seen.add(key)
            try:
                rows.append((end_s, filed_s, float(val), form, period, _duration_days(point)))
            except (TypeError, ValueError):
                continue
    return rows


def _duration_days(point: dict[str, Any]) -> Optional[int]:
    start = point.get("start")
    end = point.get("end")
    if not start or not end:
        return None
    try:
        return int((pd.Timestamp(end) - pd.Timestamp(start)).days)
    except (TypeError, ValueError):
        return None


def _classify_point(point: dict[str, Any], kind: str) -> Optional[tuple[str, str]]:
    form = str(point.get("form") or "").upper()
    fp = str(point.get("fp") or "").upper()
    if kind == "annual":
        if form in ANNUAL_FORMS or fp == "FY":
            period = "FY" if fp in {"", "FY"} else fp
            return form or "10-K", period
        return None
    if kind != "quarter":
        return None
    if not _is_quarter_fact(point, form, fp):
        return None
    period = fp if fp in QUARTER_PERIODS else "Q"
    return form or "10-Q", period


def _is_quarter_fact(point: dict[str, Any], form: str, fp: str) -> bool:
    start = point.get("start")
    end = point.get("end")
    days: Optional[int] = None
    if start and end:
        try:
            days = int((pd.Timestamp(end) - pd.Timestamp(start)).days)
        except (TypeError, ValueError):
            days = None
    instant = days is None or days <= 7
    quarterly_label = form in QUARTERLY_FORMS or fp in QUARTER_PERIODS
    q4_in_annual = form in ANNUAL_FORMS and fp == "Q4"
    if not quarterly_label and not q4_in_annual:
        return False
    if instant:
        return quarterly_label
    return days is not None and 70 <= days <= 120


def _free_cash_flow(ocf: Optional[float], capex: Optional[float]) -> Optional[float]:
    if ocf is None or capex is None:
        return None
    capex_f = float(capex)
    ocf_f = float(ocf)
    return ocf_f + capex_f if capex_f < 0 else ocf_f - abs(capex_f)
