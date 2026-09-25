"""v0.2 refresh / filing feed and v0.3 TTM + point-in-time universe."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

pytest.importorskip("duckdb")
pytest.importorskip("pyarrow")

import halalquant as hq
from halalquant.base import PRICE_COLUMNS, BaseDataProvider
from halalquant.database import LocalCache
from halalquant.database._dataset import _filings_needing_facts
from halalquant.database._events import shares_known_on
from halalquant.database._gaps import price_gap_windows
from halalquant.database._universe import members_as_of, members_between, stints_from_changes
from halalquant.providers._sec_edgar import SECEdgarProvider
from halalquant.utils._metrics import apply_ttm_cash_flow, build_financial_metrics


class RecordingProvider(BaseDataProvider):
    def __init__(self) -> None:
        self.price_windows: list[tuple[tuple[str, ...], str, str]] = []
        self.balance_calls = 0
        self.include_10q = False

    def get_prices(self, symbols, start, end) -> pd.DataFrame:
        self.price_windows.append((tuple(symbols), str(start)[:10], str(end)[:10]))
        start_d = pd.Timestamp(str(start)[:10])
        end_d = pd.Timestamp(str(end)[:10])
        rows = []
        for symbol in symbols:
            day = start_d
            while day <= end_d:
                if day.weekday() < 5:
                    rows.append(
                        {
                            "symbol": symbol,
                            "date": day.date(),
                            "open": 10.0,
                            "high": 10.0,
                            "low": 10.0,
                            "close": 10.0,
                            "volume": 2_000,
                            "adj_close": 10.0,
                        }
                    )
                day += timedelta(days=1)
        return pd.DataFrame(rows, columns=list(PRICE_COLUMNS))

    def get_balance_sheet(self, symbols, as_of=None) -> pd.DataFrame:
        self.balance_calls += 1
        rows = []
        for symbol in symbols:
            rows.append(_statement_row(symbol, date(2023, 12, 31), date(2024, 2, 15), "10-K", "FY", debt=100.0))
            if self.include_10q:
                rows.append(_statement_row(symbol, date(2024, 3, 31), date(2024, 5, 2), "10-Q", "Q1", debt=999.0))
        return pd.DataFrame(rows)

    def get_income_statement(self, symbols, as_of=None) -> pd.DataFrame:
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
                    "form": "10-K",
                    "fiscal_period": "FY",
                }
            )
        return pd.DataFrame(rows)

    def get_dividends(self, symbols, start=None, end=None) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "symbol": symbol,
                    "ex_date": date(2024, 5, 10),
                    "dividend": 1.0,
                    "adj_dividend": 1.0,
                    "record_date": date(2024, 5, 12),
                    "payment_date": date(2024, 5, 16),
                }
                for symbol in symbols
            ]
        )

    def get_sector_map(self, symbols) -> dict[str, str]:
        return {symbol: "technology" for symbol in symbols}

    def recent_filings(self, symbols) -> pd.DataFrame:
        rows = []
        for symbol in symbols:
            rows.append(_filing_row(symbol, "10-K", date(2023, 12, 31), date(2024, 2, 15), "0001"))
            if self.include_10q:
                rows.append(_filing_row(symbol, "10-Q", date(2024, 3, 31), date(2024, 5, 2), "0000320193"))
        return pd.DataFrame(rows)

    def has_cik(self, symbol: str) -> bool:
        return symbol != "NOCIK"


def _statement_row(symbol, report, filed, form, period, debt: float) -> dict:
    return {
        "symbol": symbol,
        "report_date": report,
        "filed_date": filed,
        "form": form,
        "fiscal_period": period,
        "total_debt": debt,
        "short_term_debt": debt / 4,
        "long_term_debt": debt * 3 / 4,
        "cash_and_equiv": 20.0,
        "interest_bearing_securities": 5.0,
        "receivables": 10.0,
        "liquid_assets": 25.0,
        "market_cap": None,
        "market_cap_24m": None,
        "shares_outstanding": 10.0,
        "cik": "0000320193",
    }


def _filing_row(symbol, form, report, filed, cik) -> dict:
    return {
        "symbol": symbol,
        "cik": cik,
        "form": form,
        "report_date": report,
        "filed_date": filed,
        "fiscal_period": "",
        "accession": cik,
    }


@pytest.fixture
def store(tmp_path) -> LocalCache:
    fake = RecordingProvider()
    return LocalCache(
        provider=fake,
        filings=fake,
        path=":memory:",
        parquet_dir=tmp_path / "parquet",
        mirror_parquet=False,
    )


def test_public_metrics_hide_fcf_basis(store: LocalCache) -> None:
    fake = store.market
    frame = hq.get_financial_metrics(
        "AAA",
        start="2024-01-01",
        end="2024-06-30",
        freq="ME",
        provider=fake,
        filings=fake,
        cache=False,
    )
    from halalquant.base import METRIC_COLUMNS

    assert list(frame.columns) == list(METRIC_COLUMNS)


def test_price_gap_windows_keep_the_open_end_and_skip_a_long_weekend() -> None:
    dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 8)]
    windows = price_gap_windows(dates, date(2024, 1, 2), date(2024, 1, 10))
    assert (date(2024, 1, 9), date(2024, 1, 10)) in windows
    assert all(left != date(2024, 1, 4) for left, _ in windows)


def test_refresh_appends_only_the_price_gap_and_emits_the_new_10q(store: LocalCache) -> None:
    fake = store.market
    hq.prepare_dataset(
        tickers=["AAA"],
        start="2024-01-02",
        end="2024-06-28",
        freq="ME",
        cache=store,
        provider=fake,
        filings=fake,
        apply_sector_filter=False,
        progress=False,
    )
    fake.price_windows.clear()
    balance_calls = fake.balance_calls
    fake.include_10q = True

    summary = hq.refresh_dataset(
        as_of="2024-07-08",
        tickers=["AAA"],
        start="2024-01-02",
        freq="ME",
        cache=store,
        provider=fake,
        filings=fake,
        apply_sector_filter=False,
        progress=False,
    )
    aaa_windows = [window for window in fake.price_windows if "AAA" in window[0]]
    assert aaa_windows
    assert all(window[1] >= "2024-06-29" for window in aaa_windows)
    assert any("SPY" in window[0] and "SPUS" in window[0] for window in fake.price_windows)
    assert int(summary.set_index("symbol").loc["AAA", "n_new_bars"]) > 0
    assert fake.balance_calls > balance_calls

    events = hq.filing_events(
        as_of="2024-07-08",
        since="2024-05-01",
        tickers=["AAA"],
        cache=store,
    )
    match = events[events["form"].astype(str).str.startswith("10-Q")]
    assert not match.empty
    assert str(match.iloc[0]["cik"]) == "0000320193"
    assert pd.Timestamp(match.iloc[0]["filed_date"]).date() == date(2024, 5, 2)


def test_prepare_force_refresh_refetches_the_full_price_window(store: LocalCache) -> None:
    fake = store.market
    hq.prepare_dataset(
        tickers=["AAA"],
        start="2024-06-03",
        end="2024-06-07",
        freq=None,
        cache=store,
        provider=fake,
        filings=fake,
        apply_sector_filter=False,
        progress=False,
    )
    fake.price_windows.clear()
    hq.prepare_dataset(
        tickers=["AAA"],
        start="2024-06-03",
        end="2024-06-07",
        freq=None,
        cache=store,
        provider=fake,
        filings=fake,
        apply_sector_filter=False,
        force_refresh=True,
        progress=False,
    )
    assert fake.price_windows
    assert min(window[1] for window in fake.price_windows) < "2024-06-03"


def test_filing_after_month_end_does_not_leak_and_cap_uses_that_days_shares() -> None:
    balance = pd.DataFrame(
        [
            _statement_row("AAA", date(2023, 12, 31), date(2024, 2, 15), "10-K", "FY", debt=100.0),
            _statement_row("AAA", date(2024, 3, 31), date(2024, 5, 2), "10-Q", "Q1", debt=999.0),
        ]
    )
    balance.loc[balance["form"] == "10-Q", "shares_outstanding"] = pd.NA
    balance.loc[balance["form"] == "10-K", "shares_outstanding"] = 10.0
    later = _statement_row("AAA", date(2024, 6, 30), date(2024, 8, 1), "10-Q", "Q2", debt=1.0)
    later["shares_outstanding"] = 99.0
    balance = pd.concat([balance, pd.DataFrame([later])], ignore_index=True)
    assert shares_known_on(balance, "AAA", date(2024, 5, 2)) == pytest.approx(10.0)

    prices = _daily_prices("AAA", date(2023, 1, 3), date(2024, 8, 1), close=10.0)
    income = pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "report_date": date(2023, 12, 31),
                "filed_date": date(2024, 2, 15),
                "form": "10-K",
                "fiscal_period": "FY",
                "total_revenue": 1000.0,
                "non_compliant_income": 10.0,
                "free_cash_flow": 250.0,
                "operating_cash_flow": 300.0,
                "capital_expenditure": -50.0,
            }
        ]
    )
    panel = build_financial_metrics(
        balance,
        income,
        prices,
        start="2024-04-01",
        end="2024-05-31",
        freq="ME",
    )
    april = panel[pd.to_datetime(panel["as_of"]) == pd.Timestamp("2024-04-30")].iloc[0]
    may = panel[pd.to_datetime(panel["as_of"]) == pd.Timestamp("2024-05-31")].iloc[0]
    assert float(april["total_debt"]) == pytest.approx(100.0)
    assert float(may["total_debt"]) == pytest.approx(999.0)


def test_market_cap_on_filed_date_ignores_a_later_close(store: LocalCache) -> None:
    balance = pd.DataFrame(
        [_statement_row("AAA", date(2023, 12, 31), date(2024, 2, 15), "10-K", "FY", debt=100.0)]
    )
    prices = pd.concat(
        [
            _daily_prices("AAA", date(2023, 1, 3), date(2024, 2, 15), close=10.0),
            _daily_prices("AAA", date(2024, 2, 16), date(2024, 3, 1), close=50.0),
        ],
        ignore_index=True,
    )
    store.db.write_balance_sheets(balance)
    store.db.write_prices(prices)
    store.write_filings(
        pd.DataFrame([_filing_row("AAA", "10-K", date(2023, 12, 31), date(2024, 2, 15), "99")])
    )
    events = hq.filing_events(as_of="2024-03-01", since="2024-02-01", tickers=["AAA"], cache=store)
    assert float(events.iloc[0]["market_cap_24m"]) == pytest.approx(100.0)
    store.write_universe(
        pd.DataFrame(
            {
                "universe": "custom",
                "symbol": ["AAA"],
                "sector": ["technology"],
                "sector_allowed": [True],
            }
        )
    )
    implied = hq.filing_events(as_of="2024-03-01", since="2024-02-01", cache=store)
    assert list(implied["symbol"]) == ["AAA"]


def test_ttm_sums_four_quarters_and_ignores_ytd() -> None:
    income = pd.DataFrame(
        [
            _quarter("AAA", date(2023, 3, 31), date(2023, 5, 1), "Q1", 10.0, 90),
            _quarter("AAA", date(2023, 6, 30), date(2023, 8, 1), "Q2", 20.0, 91),
            _quarter("AAA", date(2023, 9, 30), date(2023, 11, 1), "Q3", 30.0, 92),
            _quarter("AAA", date(2023, 9, 30), date(2023, 11, 1), "Q3", 1000.0, 273),
            _quarter("AAA", date(2023, 12, 31), date(2024, 2, 15), "Q4", 40.0, 92),
            {
                "symbol": "AAA",
                "report_date": date(2023, 12, 31),
                "filed_date": date(2024, 2, 15),
                "form": "10-K",
                "fiscal_period": "FY",
                "period_days": 365,
                "free_cash_flow": 999.0,
                "total_revenue": 999.0,
                "non_compliant_income": 1.0,
            },
        ]
    )
    panel = pd.DataFrame(
        [{"symbol": "AAA", "as_of": date(2024, 2, 28), "free_cash_flow": 1.0, "total_revenue": 1.0, "impure_ratio": 0.5}]
    )
    out = apply_ttm_cash_flow(panel, income)
    assert float(out.iloc[0]["free_cash_flow"]) == pytest.approx(100.0)
    assert out.iloc[0]["fcf_basis"] == "ttm"

    early = panel.copy()
    early["as_of"] = date(2024, 1, 31)
    before_q4 = apply_ttm_cash_flow(early, income)
    assert before_q4.iloc[0]["fcf_basis"] == "annual"
    assert float(before_q4.iloc[0]["free_cash_flow"]) != pytest.approx(100.0)


def test_sec_parser_drops_ytd_cash_flow() -> None:
    facts = {
        "facts": {
            "us-gaap": {
                "NetCashProvidedByUsedInOperatingActivities": {
                    "units": {
                        "USD": [
                            _xbrl("2023-03-31", "2023-01-01", "2023-05-01", 10, "10-Q", "Q1"),
                            _xbrl("2023-06-30", "2023-04-01", "2023-08-01", 20, "10-Q", "Q2"),
                            _xbrl("2023-09-30", "2023-07-01", "2023-11-01", 30, "10-Q", "Q3"),
                            _xbrl("2023-09-30", "2023-01-01", "2023-11-01", 1000, "10-Q", "Q3"),
                            _xbrl("2023-12-31", "2023-10-01", "2024-02-15", 40, "10-K", "Q4"),
                            _xbrl("2023-12-31", "2023-01-01", "2024-02-15", 999, "10-K", "FY"),
                        ]
                    }
                },
                "PaymentsToAcquirePropertyPlantAndEquipment": {
                    "units": {
                        "USD": [
                            _xbrl("2023-03-31", "2023-01-01", "2023-05-01", 0, "10-Q", "Q1"),
                            _xbrl("2023-06-30", "2023-04-01", "2023-08-01", 0, "10-Q", "Q2"),
                            _xbrl("2023-09-30", "2023-07-01", "2023-11-01", 0, "10-Q", "Q3"),
                            _xbrl("2023-09-30", "2023-01-01", "2023-11-01", 0, "10-Q", "Q3"),
                            _xbrl("2023-12-31", "2023-10-01", "2024-02-15", 0, "10-K", "Q4"),
                            _xbrl("2023-12-31", "2023-01-01", "2024-02-15", 0, "10-K", "FY"),
                        ]
                    }
                },
            }
        }
    }
    provider = SECEdgarProvider(facts_dir=False)
    income = provider._facts_to_income("AAA", facts)
    values = set(pd.to_numeric(income["free_cash_flow"], errors="coerce").dropna())
    assert 1000.0 not in values
    quarters = income[income["fiscal_period"].isin(["Q1", "Q2", "Q3", "Q4"])]
    assert set(pd.to_numeric(quarters["free_cash_flow"], errors="coerce")) == {10.0, 20.0, 30.0, 40.0}


def test_wikipedia_stints_drop_a_name_after_it_leaves() -> None:
    current = pd.DataFrame(
        {
            "symbol": ["AAA"],
            "name": ["Alpha"],
            "sector": ["Information Technology"],
            "industry": ["Hardware"],
        }
    )
    changes = pd.DataFrame(
        {
            "date": [date(2020, 6, 1)],
            "added_symbol": [None],
            "removed_symbol": ["BBB"],
        }
    )
    stints = stints_from_changes(current, changes)
    assert "BBB" not in set(members_as_of(stints, "2021-01-01")["symbol"])
    assert "BBB" in set(members_between(stints, "2010-01-01", "2021-01-01")["symbol"])
    assert "AAA" in set(members_as_of(stints, "2021-01-01")["symbol"])


def test_coverage_keeps_a_leaver_with_no_prices_and_reports_adv(store: LocalCache) -> None:
    store.db.write_metrics(
        pd.DataFrame(
            [
                {
                    "symbol": "BBB",
                    "as_of": date(2024, 6, 30),
                    "freq": "ME",
                    "report_date": date(2023, 12, 31),
                    "filed_date": date(2024, 2, 15),
                    "free_cash_flow": 10.0,
                    "operating_cash_flow": 12.0,
                    "capital_expenditure": -1.0,
                    "impure_ratio": 0.01,
                    "fcf_basis": "ttm",
                }
            ]
        )
    )
    store.db.write_prices(_daily_prices("BBB", date(2024, 6, 3), date(2024, 6, 7), close=4.0, volume=100))
    store.db.write_balance_sheets(
        pd.DataFrame(
            [
                _statement_row("BBB", date(2022, 12, 31), date(2023, 2, 1), "10-K", "FY", 1.0),
                _statement_row("BBB", date(2022, 12, 31), date(2024, 2, 1), "10-K", "FY", 2.0),
            ]
        )
    )
    store.write_stints(
        pd.DataFrame(
            [
                {
                    "universe": "sp500",
                    "symbol": "CCC",
                    "start_date": date(2010, 1, 1),
                    "end_date": date(2020, 6, 1),
                    "name": "Gone",
                    "sector": None,
                    "industry": None,
                }
            ]
        )
    )
    detail = hq.coverage_report(
        tickers=["NOCIK", "BBB", "CCC"],
        start="2024-06-01",
        end="2024-06-07",
        cache=store,
    )
    by_symbol = detail.set_index("symbol")
    assert bool(by_symbol.loc["NOCIK", "has_fcf"]) is False
    assert bool(by_symbol.loc["BBB", "has_fcf"]) is True
    assert int(by_symbol.loc["BBB", "n_restatements"]) == 1
    assert bool(by_symbol.loc["BBB", "has_cik"]) is True
    assert bool(by_symbol.loc["NOCIK", "has_cik"]) is False
    assert pd.notna(by_symbol.loc["BBB", "adv_20d"])
    assert bool(by_symbol.loc["CCC", "price_missing"]) is True
    summary = hq.coverage_summary(detail)
    assert "n_price_missing" in set(summary["check"])


def test_unchanged_accession_does_not_redownload_facts(store: LocalCache) -> None:
    store.db.write_balance_sheets(
        pd.DataFrame([_statement_row("AAA", date(2023, 12, 31), date(2024, 2, 15), "10-K", "FY", 100.0)])
    )

    class _Submissions:
        def __init__(self) -> None:
            self.calls = 0

        def has_cik(self, symbol: str) -> bool:
            return True

        def submission_index(self, symbol: str, etag=None):
            self.calls += 1
            if etag == "etag-1":
                return pd.DataFrame(), None, "etag-1", True
            frame = pd.DataFrame([_filing_row(symbol, "10-Q", date(2024, 3, 31), date(2024, 5, 2), "55")])
            return frame, "ACC-1", "etag-1", False

    stub = _Submissions()
    store.filings = stub
    changed, _ = _filings_needing_facts(store, ["AAA"], lambda _msg: None)
    assert changed == ["AAA"]
    changed_again, _ = _filings_needing_facts(store, ["AAA"], lambda _msg: None)
    assert changed_again == []
    assert stub.calls == 2


def _quarter(symbol, report, filed, period, fcf, days) -> dict:
    return {
        "symbol": symbol,
        "report_date": report,
        "filed_date": filed,
        "form": "10-Q" if period != "Q4" else "10-K",
        "fiscal_period": period,
        "period_days": days,
        "free_cash_flow": fcf,
        "total_revenue": fcf * 10,
        "non_compliant_income": 1.0,
    }


def _xbrl(end, start, filed, val, form, fp) -> dict:
    return {"end": end, "start": start, "filed": filed, "val": val, "form": form, "fp": fp}


def _daily_prices(symbol, start, end, close: float, volume: int = 1_000) -> pd.DataFrame:
    rows = []
    day = pd.Timestamp(start)
    stop = pd.Timestamp(end)
    while day <= stop:
        if day.weekday() < 5:
            rows.append(
                {
                    "symbol": symbol,
                    "date": day.date(),
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": volume,
                    "adj_close": close,
                }
            )
        day += timedelta(days=1)
    return pd.DataFrame(rows, columns=list(PRICE_COLUMNS))
