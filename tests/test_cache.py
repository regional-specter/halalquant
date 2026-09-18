"""Local DuckDB cache, prepared AAOIFI metrics DB, and cache-backed API."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

pytest.importorskip("duckdb")
pytest.importorskip("pyarrow")

import halalquant as hq
from halalquant.base import INCOME_COLUMNS, METRIC_COLUMNS, PRICE_COLUMNS, BaseDataProvider
from halalquant.database import LocalCache, yahoo_symbol
from halalquant.database._universe import _normalize_sp500
from halalquant.utils._metrics import fill_market_caps_from_prices


class FakeProvider(BaseDataProvider):
    def __init__(self) -> None:
        self.price_calls = 0
        self.balance_calls = 0
        self.income_calls = 0
        self.dividend_calls = 0
        self.sector_calls = 0

    def get_prices(self, symbols, start, end) -> pd.DataFrame:
        self.price_calls += 1
        start_d = pd.Timestamp(str(start)[:10])
        end_d = pd.Timestamp(str(end)[:10])
        rows = []
        for symbol in symbols:
            day = start_d
            price = 100.0 if symbol == "AAA" else 50.0
            while day <= end_d:
                if day.weekday() < 5:
                    rows.append(
                        {
                            "symbol": symbol,
                            "date": day.date(),
                            "open": price,
                            "high": price,
                            "low": price,
                            "close": price,
                            "volume": 1_000,
                            "adj_close": price,
                        }
                    )
                day += timedelta(days=1)
        return pd.DataFrame(rows, columns=list(PRICE_COLUMNS))

    def get_balance_sheet(self, symbols, as_of=None) -> pd.DataFrame:
        self.balance_calls += 1
        rows = []
        for symbol in symbols:
            shares = 10.0 if symbol == "AAA" else 20.0
            rows.append(
                {
                    "symbol": symbol,
                    "report_date": date(2023, 12, 31),
                    "filed_date": date(2024, 2, 15),
                    "total_debt": 200.0,
                    "short_term_debt": 50.0,
                    "long_term_debt": 150.0,
                    "cash_and_equiv": 80.0,
                    "interest_bearing_securities": 20.0,
                    "receivables": 40.0,
                    "liquid_assets": 100.0,
                    "market_cap": None,
                    "market_cap_24m": None,
                    "shares_outstanding": shares,
                }
            )
        return pd.DataFrame(rows)

    def get_income_statement(self, symbols, as_of=None) -> pd.DataFrame:
        self.income_calls += 1
        rows = []
        for symbol in symbols:
            rows.append(
                {
                    "symbol": symbol,
                    "report_date": date(2023, 12, 31),
                    "filed_date": date(2024, 2, 15),
                    "total_revenue": 1000.0,
                    "interest_income": 10.0,
                    "non_compliant_income": 10.0,
                    "ebitda": 400.0,
                    "operating_cash_flow": 300.0,
                    "capital_expenditure": -50.0,
                    "free_cash_flow": 250.0,
                }
            )
        return pd.DataFrame(rows, columns=list(INCOME_COLUMNS))

    def get_dividends(self, symbols, start=None, end=None) -> pd.DataFrame:
        self.dividend_calls += 1
        rows = []
        for symbol in symbols:
            rows.append(
                {
                    "symbol": symbol,
                    "ex_date": date(2024, 5, 10),
                    "dividend": 1.0,
                    "adj_dividend": 1.0,
                    "record_date": date(2024, 5, 12),
                    "payment_date": date(2024, 5, 16),
                }
            )
        return pd.DataFrame(rows)

    def get_sector_map(self, symbols) -> dict[str, str]:
        self.sector_calls += 1
        out = {}
        for symbol in symbols:
            out[symbol] = "conventional banking" if symbol == "BANK" else "technology"
        return out


@pytest.fixture
def store(tmp_path) -> LocalCache:
    fake = FakeProvider()
    return LocalCache(
        provider=fake,
        filings=fake,
        path=":memory:",
        parquet_dir=tmp_path / "parquet",
        mirror_parquet=True,
    )


def test_yahoo_symbol_maps_share_classes() -> None:
    assert yahoo_symbol("BRK.B") == "BRK-B"
    assert yahoo_symbol("brk.b") == "BRK-B"


def test_normalize_sp500_table() -> None:
    raw = pd.DataFrame(
        {
            "Symbol": ["AAPL", "BRK.B"],
            "Security": ["Apple", "Berkshire"],
            "GICS Sector": ["Information Technology", "Financials"],
        }
    )
    out = _normalize_sp500(raw)
    assert list(out["symbol"]) == ["AAPL", "BRK-B"]
    assert out.loc[0, "sector"] == "Information Technology"


def test_gics_subindustry_maps_banks() -> None:
    from halalquant.database._universe import map_universe_activity

    assert map_universe_activity("Financials", "Diversified Banks") == "conventional banking"
    assert map_universe_activity("Financials", "Life & Health Insurance") == "conventional insurance"
    assert map_universe_activity("Information Technology", "Technology Hardware, Storage & Peripherals") == "information technology"


def test_xom_sec_cik_override() -> None:
    from halalquant.providers._sec_edgar import CIK_OVERRIDES

    assert CIK_OVERRIDES["XOM"] == 34088


def test_prepare_dataset_writes_aaoifi_metrics(store: LocalCache) -> None:
    fake = store.market
    summary = hq.prepare_dataset(
        tickers=["AAA", "BBB", "BANK"],
        start="2024-01-01",
        end="2024-12-31",
        freq="ME",
        cache=store,
        provider=fake,
        filings=fake,
        apply_sector_filter=True,
        progress=False,
    )
    assert set(summary["symbol"]) == {"AAA", "BBB", "BANK"}
    bank = summary.set_index("symbol").loc["BANK"]
    assert bool(bank["sector_allowed"]) is False
    assert int(bank["n_balance_sheets"]) == 0

    kept = summary[summary["sector_allowed"]]
    assert set(kept["symbol"]) == {"AAA", "BBB"}
    assert (kept["n_metrics"] > 0).all()

    annual = store.read_metrics(["AAA"], start="2023-01-01", end="2024-12-31", freq="annual")
    assert not annual.empty
    assert "debt_ratio" in annual.columns
    assert "ebitda" in annual.columns
    assert "free_cash_flow" in annual.columns
    assert float(annual.iloc[0]["impure_ratio"]) == pytest.approx(0.01)

    monthly = store.read_metrics(["AAA"], start="2024-03-01", end="2024-06-30", freq="ME")
    assert not monthly.empty
    universe = store.read_universe("custom")
    assert "BANK" in set(universe["symbol"])


def test_cached_download_and_metrics_skip_upstream(store: LocalCache) -> None:
    fake = store.market
    hq.prepare_dataset(
        tickers=["AAA"],
        start="2024-01-01",
        end="2024-06-30",
        freq=None,
        cache=store,
        provider=fake,
        filings=fake,
        apply_sector_filter=False,
        progress=False,
    )
    prices_calls = fake.price_calls
    balance_calls = fake.balance_calls
    income_calls = fake.income_calls

    prices = hq.download("AAA", start="2024-02-01", end="2024-02-28", provider=fake, cache=store)
    metrics = hq.get_financial_metrics(
        "AAA",
        start="2023-01-01",
        end="2024-12-31",
        provider=fake,
        filings=fake,
        cache=store,
    )
    purified = hq.purify_dividends(
        "AAA",
        start="2024-01-01",
        end="2024-12-31",
        provider=fake,
        filings=fake,
        cache=store,
    )
    universe = hq.get_halal_universe(
        "AAA",
        as_of="2024-06-30",
        provider=fake,
        filings=fake,
        cache=store,
        apply_sector_filter=False,
    )

    assert not prices.empty
    assert list(metrics.columns) == list(METRIC_COLUMNS)
    assert float(purified.iloc[0]["purification_amount"]) == pytest.approx(0.01)
    assert bool(universe.iloc[0]["is_compliant"]) is True
    assert fake.price_calls == prices_calls
    assert fake.balance_calls == balance_calls
    assert fake.income_calls == income_calls


def test_fill_market_caps_uses_shares_and_trailing_close() -> None:
    fundamentals = pd.DataFrame(
        {
            "symbol": ["AAA"],
            "report_date": [date(2024, 1, 31)],
            "shares_outstanding": [10.0],
            "market_cap": [pd.NA],
            "market_cap_24m": [pd.NA],
            "total_debt": [20.0],
        }
    )
    prices = pd.DataFrame(
        {
            "symbol": ["AAA", "AAA"],
            "date": [date(2024, 1, 15), date(2024, 1, 31)],
            "close": [8.0, 10.0],
        }
    )
    out = fill_market_caps_from_prices(fundamentals, prices, as_of="2024-01-31")
    assert float(out.iloc[0]["market_cap"]) == pytest.approx(100.0)
    assert float(out.iloc[0]["market_cap_24m"]) == pytest.approx(90.0)


def test_annual_metrics_keep_restated_comparatives(store: LocalCache) -> None:
    frame = pd.DataFrame(
        {
            "symbol": ["AAA", "AAA"],
            "as_of": [date(2023, 11, 3), date(2023, 11, 3)],
            "report_date": [date(2022, 9, 24), date(2023, 9, 30)],
            "filed_date": [date(2023, 11, 3), date(2023, 11, 3)],
            "debt_ratio": [0.04, 0.03],
        }
    )
    store.write_metrics(frame, freq="annual")
    out = store.read_metrics(["AAA"], freq="annual")
    assert len(out) == 2
    assert set(pd.to_datetime(out["report_date"]).dt.date) == {date(2022, 9, 24), date(2023, 9, 30)}
