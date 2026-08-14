"""Tests for financial-metric helpers over a date range."""

from __future__ import annotations

import pandas as pd
import pytest

from halalquant import compare_standards, get_financial_metrics, get_halal_universe
from halalquant.base import METRIC_COLUMNS
from halalquant.screening._aaoifi import compute_ratios


def _balance_rows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "AAPL",
                "report_date": "2022-09-24",
                "filed_date": "2022-10-28",
                "total_debt": 20.0,
                "short_term_debt": 8.0,
                "long_term_debt": 12.0,
                "cash_and_equiv": 10.0,
                "interest_bearing_securities": 0.0,
                "receivables": 8.0,
                "liquid_assets": 10.0,
                "market_cap": None,
                "market_cap_24m": None,
                "shares_outstanding": 100.0,
            },
            {
                "symbol": "AAPL",
                "report_date": "2023-09-30",
                "filed_date": "2023-11-03",
                "total_debt": 30.0,
                "short_term_debt": 10.0,
                "long_term_debt": 20.0,
                "cash_and_equiv": 12.0,
                "interest_bearing_securities": 0.0,
                "receivables": 9.0,
                "liquid_assets": 12.0,
                "market_cap": None,
                "market_cap_24m": None,
                "shares_outstanding": 100.0,
            },
        ]
    )


def _income_rows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "AAPL",
                "report_date": "2022-09-24",
                "filed_date": "2022-10-28",
                "total_revenue": 100.0,
                "interest_income": 4.0,
                "non_compliant_income": 4.0,
            },
            {
                "symbol": "AAPL",
                "report_date": "2023-09-30",
                "filed_date": "2023-11-03",
                "total_revenue": 200.0,
                "interest_income": 10.0,
                "non_compliant_income": 10.0,
            },
        ]
    )


def _prices() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "AAPL",
                "date": "2022-09-23",
                "open": 2.0,
                "high": 2.0,
                "low": 2.0,
                "close": 2.0,
                "volume": 1,
                "adj_close": 2.0,
            },
            {
                "symbol": "AAPL",
                "date": "2023-09-29",
                "open": 2.0,
                "high": 2.0,
                "low": 2.0,
                "close": 2.0,
                "volume": 1,
                "adj_close": 2.0,
            },
        ]
    )


class _FakeMarket:
    def get_sector_map(self, symbols):
        return {symbol: "technology" for symbol in symbols}

    def get_prices(self, symbols, start, end):
        return _prices()


class _FakeFilings:
    def get_balance_sheet(self, symbols, as_of=None):
        return _balance_rows()

    def get_income_statement(self, symbols, as_of=None):
        return _income_rows()


def test_compute_ratios_from_fundamentals():
    frame = pd.DataFrame(
        {
            "total_debt": [20.0],
            "cash_and_equiv": [10.0],
            "interest_bearing_securities": [0.0],
            "receivables": [8.0],
            "liquid_assets": [0.0],
            "market_cap_24m": [100.0],
        }
    )
    ratios = compute_ratios(frame)
    assert float(ratios.loc[0, "debt_ratio"]) == pytest.approx(0.20)
    assert float(ratios.loc[0, "cash_ratio"]) == pytest.approx(0.10)
    assert float(ratios.loc[0, "receivables_ratio"]) == pytest.approx(0.08)


def test_get_financial_metrics_one_row_per_filing():
    frame = get_financial_metrics(
        "AAPL",
        start="2022-01-01",
        end="2024-12-31",
        provider=_FakeMarket(),
        filings=_FakeFilings(),
    )
    assert list(frame.columns) == list(METRIC_COLUMNS)
    assert len(frame) == 2
    frame["report_date"] = pd.to_datetime(frame["report_date"])
    by_report = frame.set_index("report_date")
    # market cap = 2.0 close × 100 shares
    assert float(by_report.loc["2023-09-30", "debt_ratio"]) == pytest.approx(0.15)
    assert float(by_report.loc["2023-09-30", "impure_ratio"]) == pytest.approx(0.05)
    assert float(by_report.loc["2022-09-24", "impure_ratio"]) == pytest.approx(0.04)


def test_get_financial_metrics_drops_unfiled_look_ahead():
    frame = get_financial_metrics(
        "AAPL",
        start="2022-01-01",
        end="2023-01-01",
        provider=_FakeMarket(),
        filings=_FakeFilings(),
    )
    assert len(frame) == 1
    assert pd.Timestamp(frame.iloc[0]["report_date"]).date().isoformat() == "2022-09-24"


def test_get_financial_metrics_monthly_snapshots():
    frame = get_financial_metrics(
        "AAPL",
        start="2023-10-01",
        end="2023-12-31",
        provider=_FakeMarket(),
        filings=_FakeFilings(),
        freq="ME",
    )
    assert len(frame) == 3
    assert pd.Timestamp(frame.iloc[0]["report_date"]).date().isoformat() == "2022-09-24"
    assert pd.Timestamp(frame.iloc[-1]["report_date"]).date().isoformat() == "2023-09-30"
    assert float(frame.iloc[-1]["impure_ratio"]) == pytest.approx(0.05)


def test_get_financial_metrics_empty_filings():
    class _EmptyFilings:
        def get_balance_sheet(self, symbols, as_of=None):
            return pd.DataFrame()

        def get_income_statement(self, symbols, as_of=None):
            return pd.DataFrame()

    frame = get_financial_metrics(
        "AAPL",
        start="2024-01-01",
        end="2024-12-31",
        provider=_FakeMarket(),
        filings=_EmptyFilings(),
    )
    assert frame.empty
    assert list(frame.columns) == list(METRIC_COLUMNS)


def test_compare_standards_public_api():
    frame = compare_standards(
        "AAPL",
        provider=_FakeMarket(),
        filings=_FakeFilings(),
        apply_sector_filter=True,
    )
    assert len(frame) == 1
    assert "aaoifi_compliant" in frame.columns
    assert "djim_compliant" in frame.columns
    assert "agreement" in frame.columns
    assert bool(frame.iloc[0]["aaoifi_compliant"]) is True
    assert bool(frame.iloc[0]["djim_compliant"]) is True


def test_unknown_screening_standard_raises():
    with pytest.raises(ValueError, match="Unknown screening standard"):
        get_halal_universe(
            "AAPL",
            standard="ftse",
            provider=_FakeMarket(),
            filings=_FakeFilings(),
            apply_sector_filter=False,
        )
