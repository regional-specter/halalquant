"""API and provider stub tests."""

import pandas as pd

from halalquant import download, get_halal_universe
from halalquant.providers._fmp import FMPProvider
from halalquant.utils.validation import validate_date_range, validate_symbols


def test_validate_symbols():
    assert validate_symbols("aapl, msft") == ["AAPL", "MSFT"]


def test_validate_date_range_order():
    start, end = validate_date_range("2024-01-01", "2024-06-01")
    assert start.isoformat() == "2024-01-01"
    assert end.isoformat() == "2024-06-01"


def test_fmp_provider_stub_returns_empty_frames():
    provider = FMPProvider(api_key="test")
    prices = provider.get_prices(["AAPL"], start="2024-01-01", end="2024-01-31")
    assert isinstance(prices, pd.DataFrame)
    assert list(prices.columns) == [
        "symbol",
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "adj_close",
    ]


def test_download_and_universe_stubs():
    provider = FMPProvider(api_key="test")
    prices = download("AAPL", start="2024-01-01", end="2024-01-31", provider=provider)
    universe = get_halal_universe("AAPL", provider=provider, apply_sector_filter=False)
    assert prices.empty
    assert universe.empty or "is_compliant" in universe.columns
