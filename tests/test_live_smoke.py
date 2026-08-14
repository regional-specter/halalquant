"""Live Yahoo Finance + SEC EDGAR smoke tests.

Skipped unless HALALQUANT_LIVE=1 so the default suite stays offline:

    HALALQUANT_LIVE=1 pytest tests/test_live_smoke.py -s
"""

from __future__ import annotations

import os

import pandas as pd
import pytest

import halalquant as hq
from halalquant.base import COMPLIANCE_COLUMNS, METRIC_COLUMNS
from halalquant.providers import SECEdgarProvider, YFinanceProvider
from halalquant.screening._compare import COMPARE_COLUMNS

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.getenv("HALALQUANT_LIVE") != "1",
        reason="Set HALALQUANT_LIVE=1 to hit live yfinance and SEC EDGAR",
    ),
]

TICKERS = ["AAPL", "MSFT", "JPM"]


@pytest.fixture(scope="module")
def market() -> YFinanceProvider:
    return YFinanceProvider()


@pytest.fixture(scope="module")
def filings() -> SECEdgarProvider:
    return SECEdgarProvider()


def test_live_download_prices(market: YFinanceProvider) -> None:
    prices = hq.download(
        "AAPL",
        start="2024-01-02",
        end="2024-01-31",
        provider=market,
    )
    assert not prices.empty
    assert list(prices.columns)[:3] == ["symbol", "date", "open"]
    assert (prices["symbol"] == "AAPL").all()
    assert prices["close"].notna().any()


def test_live_compare_aaoifi_vs_djim(
    market: YFinanceProvider,
    filings: SECEdgarProvider,
) -> None:
    screened = hq.compare_standards(
        TICKERS,
        provider=market,
        filings=filings,
        apply_sector_filter=True,
    )
    unfiltered = hq.compare_standards(
        TICKERS,
        provider=market,
        filings=filings,
        apply_sector_filter=False,
    )
    aaoifi = hq.get_halal_universe(
        TICKERS,
        standard="aaoifi",
        provider=market,
        filings=filings,
        apply_sector_filter=True,
    )
    djim = hq.get_halal_universe(
        TICKERS,
        standard="djim",
        provider=market,
        filings=filings,
        apply_sector_filter=True,
    )

    print("\nAAOIFI vs DJIM (sector filter on)")
    print(screened.to_string(index=False))
    print("\nAAOIFI vs DJIM (no sector filter)")
    print(unfiltered.to_string(index=False))
    print("\nAAOIFI universe")
    print(aaoifi.to_string(index=False))
    print("\nDJIM universe")
    print(djim.to_string(index=False))

    assert list(screened.columns) == list(COMPARE_COLUMNS)
    assert not unfiltered.empty
    assert set(unfiltered["symbol"]) >= {"AAPL", "MSFT", "JPM"}
    for col in ("debt_ratio", "cash_ratio", "receivables_ratio"):
        assert unfiltered[col].notna().all()
    assert unfiltered["agreement"].isin([True, False]).all()

    # Banks should drop when Yahoo sector labels are available.
    if "JPM" not in set(screened["symbol"]):
        assert set(screened["symbol"]) <= {"AAPL", "MSFT"}

    assert list(aaoifi.columns) == list(COMPLIANCE_COLUMNS)
    assert (aaoifi["standard"] == "aaoifi").all()
    assert (djim["standard"] == "djim").all()
    if not screened.empty:
        merged = screened.merge(
            aaoifi[["symbol", "is_compliant"]],
            on="symbol",
            how="left",
        )
        assert (merged["aaoifi_compliant"] == merged["is_compliant"]).all()


def test_live_metrics_and_purification(
    market: YFinanceProvider,
    filings: SECEdgarProvider,
) -> None:
    metrics = hq.get_financial_metrics(
        "AAPL",
        start="2021-01-01",
        end="2024-12-31",
        provider=market,
        filings=filings,
    )
    purified = hq.purify_dividends(
        "AAPL",
        start="2024-01-01",
        end="2024-12-31",
        provider=market,
        filings=filings,
    )

    print("\nAAPL financial metrics (annual filings 2021-2024)")
    print(metrics.to_string(index=False))
    print("\nAAPL purified dividends (2024)")
    print(purified.to_string(index=False))

    assert list(metrics.columns) == list(METRIC_COLUMNS)
    assert len(metrics) >= 2
    assert (metrics["symbol"] == "AAPL").all()
    assert metrics["debt_ratio"].notna().any()
    assert metrics["impure_ratio"].notna().any()

    assert not purified.empty
    assert "purification_amount" in purified.columns
    assert float(pd.to_numeric(purified["dividend"], errors="coerce").sum()) > 0
