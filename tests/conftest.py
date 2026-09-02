"""Shared live providers so SEC companyfacts are fetched once per session."""

from __future__ import annotations

import os

import pytest

from halalquant.providers import FilingsProvider, SECEdgarProvider, YFinanceProvider

os.environ.setdefault(
    "HALALQUANT_SEC_UA",
    "HalalQuant pytest halalquant-test@example.com",
)


@pytest.fixture(scope="session")
def market() -> YFinanceProvider:
    return YFinanceProvider()


@pytest.fixture(scope="session")
def filings() -> SECEdgarProvider:
    return SECEdgarProvider()


@pytest.fixture(scope="session")
def composite_filings(market: YFinanceProvider, filings: SECEdgarProvider) -> FilingsProvider:
    return FilingsProvider(sec=filings, yahoo=market)
