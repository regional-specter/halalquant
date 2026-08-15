"""Shared live providers so SEC companyfacts are fetched once per session."""

from __future__ import annotations

import pytest

from halalquant.providers import SECEdgarProvider, YFinanceProvider


@pytest.fixture(scope="session")
def market() -> YFinanceProvider:
    return YFinanceProvider()


@pytest.fixture(scope="session")
def filings() -> SECEdgarProvider:
    return SECEdgarProvider()
