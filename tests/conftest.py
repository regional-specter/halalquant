"""Shared live providers so SEC companyfacts are fetched once per session."""

from __future__ import annotations

import pytest

from halalquant.providers import FilingsProvider, SECEdgarProvider, YFinanceProvider


@pytest.fixture(scope="session")
def market() -> YFinanceProvider:
    return YFinanceProvider()


@pytest.fixture(scope="session")
def filings() -> SECEdgarProvider:
    return SECEdgarProvider()


@pytest.fixture(scope="session")
def composite_filings(market: YFinanceProvider, filings: SECEdgarProvider) -> FilingsProvider:
    return FilingsProvider(sec=filings, yahoo=market)
