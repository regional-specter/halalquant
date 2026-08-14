"""API, FMP provider, and local cache tests (HTTP mocked)."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pytest
import responses

from halalquant import download, get_halal_universe
from halalquant.database import DuckDBDriver, LocalCache
from halalquant.providers._fmp import FMPProvider
from halalquant.utils.validation import validate_date_range, validate_symbols

BASE = "https://financialmodelingprep.com/stable"


def test_validate_symbols():
    assert validate_symbols("aapl, msft") == ["AAPL", "MSFT"]


def test_validate_date_range_order():
    start, end = validate_date_range("2024-01-01", "2024-06-01")
    assert start.isoformat() == "2024-01-01"
    assert end.isoformat() == "2024-06-01"


def test_fmp_requires_api_key(monkeypatch):
    monkeypatch.setattr("halalquant.config.api_key", None)
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    provider = FMPProvider(api_key=None)
    provider.api_key = None
    with pytest.raises(ValueError, match="FMP API key"):
        provider.get_prices(["AAPL"], start="2024-01-01", end="2024-01-31")


def test_module_level_api_key(monkeypatch):
    import halalquant as hq

    monkeypatch.delenv("FMP_API_KEY", raising=False)
    hq.api_key = "from-user-code"
    try:
        provider = FMPProvider()
        assert provider.api_key == "from-user-code"
    finally:
        hq.api_key = None


def test_download_accepts_api_key_argument(monkeypatch):
    monkeypatch.setattr("halalquant.config.api_key", None)
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    provider = FMPProvider(api_key="per-call-key")
    assert provider.api_key == "per-call-key"


@responses.activate
def test_fmp_get_prices_normalizes_columns():
    responses.add(
        responses.GET,
        re.compile(rf"{BASE}/historical-price-eod/full"),
        json=[
            {
                "symbol": "AAPL",
                "date": "2024-01-02",
                "open": 185.0,
                "high": 186.0,
                "low": 184.0,
                "close": 185.5,
                "volume": 1000,
                "adjClose": 185.5,
            }
        ],
        status=200,
    )
    provider = FMPProvider(api_key="test-key")
    prices = provider.get_prices(["AAPL"], start="2024-01-01", end="2024-01-31")
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
    assert len(prices) == 1
    assert prices.iloc[0]["symbol"] == "AAPL"
    assert float(prices.iloc[0]["close"]) == 185.5


@responses.activate
def test_fmp_get_balance_sheet_maps_fields():
    responses.add(
        responses.GET,
        re.compile(rf"{BASE}/balance-sheet-statement"),
        json=[
            {
                "date": "2023-09-30",
                "filingDate": "2023-11-03",
                "shortTermDebt": 10.0,
                "longTermDebt": 20.0,
                "totalDebt": 30.0,
                "cashAndCashEquivalents": 50.0,
                "shortTermInvestments": 5.0,
                "netReceivables": 15.0,
            }
        ],
        status=200,
    )
    responses.add(
        responses.GET,
        re.compile(rf"{BASE}/historical-market-capitalization"),
        json=[
            {"date": "2023-09-29", "marketCap": 2_000_000_000_000},
            {"date": "2023-03-01", "marketCap": 1_800_000_000_000},
        ],
        status=200,
    )
    provider = FMPProvider(api_key="test-key")
    frame = provider.get_balance_sheet(["AAPL"], attach_market_cap=True)
    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["symbol"] == "AAPL"
    assert float(row["total_debt"]) == 30.0
    assert float(row["cash_and_equiv"]) == 50.0
    assert float(row["interest_bearing_securities"]) == 5.0
    assert float(row["market_cap"]) == 2_000_000_000_000
    assert row["market_cap_24m"] is not None


@responses.activate
def test_download_uses_cache(tmp_path: Path):
    responses.add(
        responses.GET,
        re.compile(rf"{BASE}/historical-price-eod/full"),
        json=[
            {
                "symbol": "AAPL",
                "date": "2024-01-02",
                "open": 1,
                "high": 2,
                "low": 0.5,
                "close": 1.5,
                "volume": 10,
                "adjClose": 1.5,
            }
        ],
        status=200,
    )
    provider = FMPProvider(api_key="test-key")
    cache_path = tmp_path / "cache.duckdb"
    prices = download(
        "AAPL",
        start="2024-01-01",
        end="2024-01-31",
        provider=provider,
        cache_path=cache_path,
        use_cache=True,
    )
    assert len(prices) == 1
    assert len(responses.calls) == 1

    # Second call should hit cache only
    prices2 = download(
        "AAPL",
        start="2024-01-01",
        end="2024-01-31",
        provider=provider,
        cache_path=cache_path,
        use_cache=True,
    )
    assert len(prices2) == 1
    assert len(responses.calls) == 1


@responses.activate
def test_get_halal_universe_live_path(tmp_path: Path):
    responses.add(
        responses.GET,
        re.compile(rf"{BASE}/profile"),
        json=[{"symbol": "AAPL", "sector": "Technology", "industry": "Consumer Electronics"}],
        status=200,
    )
    responses.add(
        responses.GET,
        re.compile(rf"{BASE}/balance-sheet-statement"),
        json=[
            {
                "date": "2023-09-30",
                "filingDate": "2023-11-03",
                "totalDebt": 10.0,
                "shortTermDebt": 4.0,
                "longTermDebt": 6.0,
                "cashAndCashEquivalents": 5.0,
                "shortTermInvestments": 0.0,
                "netReceivables": 8.0,
            }
        ],
        status=200,
    )
    responses.add(
        responses.GET,
        re.compile(rf"{BASE}/historical-market-capitalization"),
        json=[{"date": "2023-09-29", "marketCap": 100.0}],
        status=200,
    )
    provider = FMPProvider(api_key="test-key")
    universe = get_halal_universe(
        "AAPL",
        provider=provider,
        apply_sector_filter=True,
        cache_path=tmp_path / "u.duckdb",
        use_cache=True,
    )
    assert not universe.empty
    assert "is_compliant" in universe.columns
    assert bool(universe.iloc[0]["is_compliant"]) is True


def test_local_cache_parquet_roundtrip(tmp_path: Path):
    class _FakeProvider:
        def get_prices(self, symbols, start, end):
            return pd.DataFrame(
                [
                    {
                        "symbol": "MSFT",
                        "date": "2024-01-02",
                        "open": 1.0,
                        "high": 2.0,
                        "low": 0.5,
                        "close": 1.5,
                        "volume": 100,
                        "adj_close": 1.5,
                    }
                ]
            )

        def get_balance_sheet(self, symbols, as_of=None):
            return pd.DataFrame()

        def get_income_statement(self, symbols, as_of=None):
            return pd.DataFrame()

    cache = LocalCache(
        _FakeProvider(),
        path=tmp_path / "c.duckdb",
        parquet_dir=tmp_path / "parquet",
        mirror_parquet=True,
    )
    frame = cache.get_prices(["MSFT"], start="2024-01-01", end="2024-01-31")
    assert len(frame) == 1
    parquet_path = tmp_path / "parquet" / "prices.parquet"
    assert parquet_path.exists()

    cache2 = LocalCache(
        _FakeProvider(),
        path=tmp_path / "c2.duckdb",
        parquet_dir=tmp_path / "parquet",
        mirror_parquet=False,
    )
    n = cache2.import_parquet("prices")
    assert n == 1
    loaded = cache2.db.read_prices(["MSFT"])
    assert len(loaded) == 1


def test_duckdb_balance_sheet_roundtrip(tmp_path: Path):
    db = DuckDBDriver(tmp_path / "bs.duckdb")
    frame = pd.DataFrame(
        [
            {
                "symbol": "AAPL",
                "report_date": "2023-09-30",
                "filed_date": "2023-11-03",
                "total_debt": 1.0,
                "short_term_debt": 0.4,
                "long_term_debt": 0.6,
                "cash_and_equiv": 2.0,
                "interest_bearing_securities": 0.1,
                "receivables": 0.5,
                "liquid_assets": 2.1,
                "market_cap": 100.0,
                "market_cap_24m": 90.0,
            }
        ]
    )
    db.write_balance_sheets(frame)
    got = db.read_balance_sheets(["AAPL"])
    assert len(got) == 1
    assert float(got.iloc[0]["total_debt"]) == 1.0
