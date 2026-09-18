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
DEFAULT_SEC_USER_AGENT = (
    "HalalQuant/0.1.0 (halalquant-research@example.com; override with HALALQUANT_SEC_UA)"
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
        merged: dict[tuple[str, str], dict[str, Optional[float]]] = defaultdict(dict)
        for field, tags in BALANCE_TAGS.items():
            for end, filed, val in _iter_annual_points(
                facts,
                tags,
                as_of=as_of,
                share_units=field == "shares_outstanding",
            ):
                merged[(end, filed)][field] = val

        if not merged:
            return pd.DataFrame()

        rows: list[dict[str, Any]] = []
        for (end, filed), vals in sorted(merged.items()):
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
        merged: dict[tuple[str, str], dict[str, Optional[float]]] = defaultdict(dict)
        for field, tags in INCOME_TAGS.items():
            for end, filed, val in _iter_annual_points(facts, tags, as_of=as_of):
                merged[(end, filed)][field] = val

        if not merged:
            return pd.DataFrame(columns=list(INCOME_COLUMNS))

        rows: list[dict[str, Any]] = []
        for (end, filed), vals in sorted(merged.items()):
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
                }
            )
        frame = pd.DataFrame(rows)
        for col in ("report_date", "filed_date"):
            frame[col] = pd.to_datetime(frame[col], errors="coerce").dt.date
        return frame[list(INCOME_COLUMNS)]


def _iter_annual_points(
    facts: dict[str, Any],
    tags: Sequence[str],
    as_of: Optional[str] = None,
    share_units: bool = False,
) -> list[tuple[str, str, float]]:
    """
    Return one (report_end, filed_date, value) row per annual XBRL filing.

    Each SEC filing revision is kept so monthly point-in-time screens can use
    the values that were actually public on each snapshot date.
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    dei = facts.get("facts", {}).get("dei", {})
    as_of_ts = pd.Timestamp(as_of) if as_of else None
    unit_keys = ("shares",) if share_units else ("USD",)
    seen: set[tuple[str, str]] = set()
    rows: list[tuple[str, str, float]] = []

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
            form = str(point.get("form") or "")
            fp = str(point.get("fp") or "")
            annual = form in ANNUAL_FORMS or fp == "FY"
            if not annual or not end or filed is None or val is None:
                continue
            if as_of_ts is not None and pd.Timestamp(filed) > as_of_ts:
                continue
            end_s, filed_s = str(end), str(filed)
            if (end_s, filed_s) in seen:
                continue
            seen.add((end_s, filed_s))
            try:
                rows.append((end_s, filed_s, float(val)))
            except (TypeError, ValueError):
                continue
    return rows


def _free_cash_flow(ocf: Optional[float], capex: Optional[float]) -> Optional[float]:
    if ocf is None or capex is None:
        return None
    capex_f = float(capex)
    ocf_f = float(ocf)
    return ocf_f + capex_f if capex_f < 0 else ocf_f - abs(capex_f)
