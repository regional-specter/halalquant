"""API and provider tests (yfinance mocked; no network)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from halalquant import download, get_halal_universe, purify_dividends
from halalquant.database import DuckDBDriver, LocalCache
from halalquant.providers._yfinance import YFinanceProvider, _map_yahoo_activity
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


def test_yfinance_normalizes_flat_prices():
    raw = pd.DataFrame(
        {
            "Open": [185.0],
            "High": [186.0],
            "Low": [184.0],
            "Close": [185.5],
            "Adj Close": [185.5],
            "Volume": [1000],
        },
        index=pd.to_datetime(["2024-01-02"]),
    )
    provider = YFinanceProvider(downloader=lambda *args, **kwargs: raw)
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


def test_download_uses_injected_provider():
    class _FakeMarket:
        def get_prices(self, symbols, start, end):
            return pd.DataFrame(
                [
                    {
                        "symbol": "AAPL",
                        "date": "2024-01-02",
                        "open": 1.0,
                        "high": 2.0,
                        "low": 0.5,
                        "close": 1.5,
                        "volume": 10,
                        "adj_close": 1.5,
                    }
                ]
            )

    prices = download(
        "AAPL",
        start="2024-01-01",
        end="2024-01-31",
        provider=_FakeMarket(),
    )
    assert len(prices) == 1
    assert float(prices.iloc[0]["close"]) == 1.5


def test_get_halal_universe_uses_sec_filings():
    class _FakeMarket:
        def get_sector_map(self, symbols):
            return {"AAPL": "technology"}

        def get_prices(self, symbols, start, end):
            return pd.DataFrame(
                [
                    {
                        "symbol": "AAPL",
                        "date": "2023-09-29",
                        "open": 1.0,
                        "high": 1.0,
                        "low": 1.0,
                        "close": 2.0,
                        "volume": 1,
                        "adj_close": 2.0,
                    }
                ]
            )

    class _FakeFilings:
        def get_balance_sheet(self, symbols, as_of=None):
            return pd.DataFrame(
                [
                    {
                        "symbol": "AAPL",
                        "report_date": "2023-09-30",
                        "filed_date": "2023-11-03",
                        "total_debt": 10.0,
                        "short_term_debt": 4.0,
                        "long_term_debt": 6.0,
                        "cash_and_equiv": 5.0,
                        "interest_bearing_securities": 0.0,
                        "receivables": 8.0,
                        "liquid_assets": 5.0,
                        "market_cap": None,
                        "market_cap_24m": None,
                        "shares_outstanding": 100.0,
                    }
                ]
            )

    universe = get_halal_universe(
        "AAPL",
        provider=_FakeMarket(),
        filings=_FakeFilings(),
        apply_sector_filter=True,
    )
    assert len(universe) == 1
    assert "is_compliant" in universe.columns
    assert bool(universe.iloc[0]["is_compliant"]) is True
    assert float(universe.iloc[0]["debt_ratio"]) == pytest.approx(0.05)


def test_purify_dividends_matches_filed_income():
    class _FakeMarket:
        def get_dividends(self, symbols, start=None, end=None):
            return pd.DataFrame(
                [
                    {
                        "symbol": "AAPL",
                        "ex_date": "2024-05-10",
                        "dividend": 0.25,
                        "adj_dividend": 0.25,
                        "record_date": None,
                        "payment_date": None,
                    }
                ]
            )

    class _FakeFilings:
        def get_income_statement(self, symbols, as_of=None):
            return pd.DataFrame(
                [
                    {
                        "symbol": "AAPL",
                        "report_date": "2023-09-30",
                        "filed_date": "2023-11-03",
                        "total_revenue": 100.0,
                        "interest_income": 5.0,
                        "non_compliant_income": 5.0,
                    }
                ]
            )

    frame = purify_dividends(
        "AAPL",
        start="2024-01-01",
        end="2024-12-31",
        provider=_FakeMarket(),
        filings=_FakeFilings(),
    )
    assert len(frame) == 1
    assert float(frame.iloc[0]["dividend"]) == 0.25
    assert float(frame.iloc[0]["impure_ratio"]) == 0.05
    assert float(frame.iloc[0]["purification_amount"]) == 0.0125


def test_yfinance_dividends_from_ticker_series():
    class _FakeTicker:
        dividends = pd.Series(
            [0.25],
            index=pd.to_datetime(["2024-05-10"]),
            name="Dividends",
        )

    provider = YFinanceProvider(ticker_factory=lambda symbol: _FakeTicker())
    frame = provider.get_dividends(["AAPL"], start="2024-01-01", end="2024-12-31")
    assert len(frame) == 1
    assert float(frame.iloc[0]["dividend"]) == 0.25


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
