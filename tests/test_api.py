"""Public API tests against live yfinance and SEC EDGAR."""

from __future__ import annotations

import pandas as pd
import pytest

import halalquant as hq
from halalquant.base import COMPLIANCE_COLUMNS, METRIC_COLUMNS
from halalquant.providers import SECEdgarProvider, YFinanceProvider
from halalquant.screening._compare import COMPARE_COLUMNS
from halalquant.providers._yfinance import _map_yahoo_activity
from halalquant.utils.validation import validate_date_range, validate_symbols


def test_validate_symbols():
    assert validate_symbols("aapl, msft") == ["AAPL", "MSFT"]


def test_validate_date_range_order():
    start, end = validate_date_range("2024-01-01", "2024-06-01")
    assert start.isoformat() == "2024-01-01"
    assert end.isoformat() == "2024-06-01"


def test_map_yahoo_banks_to_exclusion_label():
    assert _map_yahoo_activity("Financial Services", "Banks - Diversified") == "conventional banking"
    assert _map_yahoo_activity("Technology", "Consumer Electronics") == "technology"


def test_download_aapl_prices(market: YFinanceProvider) -> None:
    prices = hq.download("AAPL", start="2024-01-02", end="2024-01-31", provider=market)
    assert not prices.empty
    assert list(prices.columns)[:3] == ["symbol", "date", "open"]
    assert (prices["symbol"] == "AAPL").all()
    assert float(prices["close"].min()) > 0


def test_get_halal_universe_aapl(
    market: YFinanceProvider,
    filings: SECEdgarProvider,
) -> None:
    universe = hq.get_halal_universe(
        "AAPL",
        provider=market,
        filings=filings,
        apply_sector_filter=True,
    )
    assert len(universe) == 1
    assert list(universe.columns) == list(COMPLIANCE_COLUMNS)
    assert universe.iloc[0]["symbol"] == "AAPL"
    assert universe.iloc[0]["standard"] == "aaoifi"
    assert pd.notna(universe.iloc[0]["debt_ratio"])
    assert 0 <= float(universe.iloc[0]["debt_ratio"]) < 1


def test_compare_standards_excludes_banks_and_fails_them_financially(
    market: YFinanceProvider,
    filings: SECEdgarProvider,
) -> None:
    screened = hq.compare_standards(
        ["AAPL", "MSFT", "JPM"],
        provider=market,
        filings=filings,
        apply_sector_filter=True,
    )
    unfiltered = hq.compare_standards(
        ["AAPL", "MSFT", "JPM", "MET"],
        provider=market,
        filings=filings,
        apply_sector_filter=False,
    )
    assert list(screened.columns) == list(COMPARE_COLUMNS)
    assert "JPM" not in set(screened["symbol"])
    assert {"AAPL", "MSFT"} <= set(screened["symbol"])

    by_symbol = unfiltered.set_index("symbol")
    assert float(by_symbol.loc["JPM", "debt_ratio"]) > 1
    assert float(by_symbol.loc["JPM", "receivables_ratio"]) > 0
    assert bool(by_symbol.loc["JPM", "aaoifi_compliant"]) is False
    assert bool(by_symbol.loc["MET", "aaoifi_compliant"]) is False
    assert bool(by_symbol.loc["AAPL", "aaoifi_compliant"]) is True


def test_djim_universe_matches_compare(
    market: YFinanceProvider,
    filings: SECEdgarProvider,
) -> None:
    djim = hq.get_halal_universe(
        ["AAPL", "MSFT"],
        standard="djim",
        provider=market,
        filings=filings,
        apply_sector_filter=True,
    )
    assert (djim["standard"] == "djim").all()
    assert djim["is_compliant"].notna().all()


def test_unknown_screening_standard_raises(
    market: YFinanceProvider,
    filings: SECEdgarProvider,
) -> None:
    with pytest.raises(ValueError, match="Unknown screening standard"):
        hq.get_halal_universe(
            "AAPL",
            standard="ftse",
            provider=market,
            filings=filings,
            apply_sector_filter=False,
        )


def test_purify_dividends_aapl_2024(
    market: YFinanceProvider,
    filings: SECEdgarProvider,
) -> None:
    frame = hq.purify_dividends(
        "AAPL",
        start="2024-01-01",
        end="2024-12-31",
        provider=market,
        filings=filings,
    )
    assert not frame.empty
    assert float(pd.to_numeric(frame["dividend"], errors="coerce").sum()) > 0
    may = frame[pd.to_datetime(frame["ex_date"]) == pd.Timestamp("2024-05-10")]
    assert not may.empty
    assert pd.Timestamp(may.iloc[0]["report_date"]).date().isoformat() == "2023-09-30"
    assert pd.notna(may.iloc[0]["impure_ratio"])
    assert float(may.iloc[0]["purification_amount"]) > 0


def test_get_financial_metrics_aapl(
    market: YFinanceProvider,
    filings: SECEdgarProvider,
) -> None:
    frame = hq.get_financial_metrics(
        "AAPL",
        start="2021-01-01",
        end="2024-12-31",
        provider=market,
        filings=filings,
    )
    assert list(frame.columns) == list(METRIC_COLUMNS)
    assert len(frame) >= 3
    assert (frame["symbol"] == "AAPL").all()
    assert frame["debt_ratio"].notna().any()
    assert frame["impure_ratio"].notna().any()


def test_get_financial_metrics_no_lookahead(
    market: YFinanceProvider,
    filings: SECEdgarProvider,
) -> None:
    frame = hq.get_financial_metrics(
        "AAPL",
        start="2022-01-01",
        end="2023-01-01",
        provider=market,
        filings=filings,
    )
    assert not frame.empty
    latest = max(pd.Timestamp(d).date().isoformat() for d in frame["report_date"])
    assert latest <= "2022-09-24"


def test_get_financial_metrics_monthly_snapshots(
    market: YFinanceProvider,
    filings: SECEdgarProvider,
) -> None:
    frame = hq.get_financial_metrics(
        "AAPL",
        start="2023-10-01",
        end="2023-12-31",
        provider=market,
        filings=filings,
        freq="ME",
    )
    assert len(frame) == 3
    first = pd.Timestamp(frame.iloc[0]["report_date"]).date().isoformat()
    last = pd.Timestamp(frame.iloc[-1]["report_date"]).date().isoformat()
    assert first <= "2022-09-24"
    assert last == "2023-09-30"


def test_missing_sec_issuer_returns_empty_metrics(
    market: YFinanceProvider,
    filings: SECEdgarProvider,
) -> None:
    frame = hq.get_financial_metrics(
        "ZZZZ",
        start="2024-01-01",
        end="2024-12-31",
        provider=market,
        filings=filings,
    )
    assert frame.empty
    assert list(frame.columns) == list(METRIC_COLUMNS)
