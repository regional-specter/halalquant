"""Point-in-time membership, the combined halal screen, and data-quality flags. Offline."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

import halalquant as hq
from halalquant.database import LocalCache, members_as_of, stints_from_snapshots


class _NoNetwork:
    def __getattr__(self, name):
        raise AssertionError(f"network call {name} during an offline test")


@pytest.fixture
def store(tmp_path) -> LocalCache:
    offline = _NoNetwork()
    return LocalCache(
        provider=offline,
        filings=offline,
        path=":memory:",
        parquet_dir=tmp_path / "parquet",
        mirror_parquet=False,
    )


def _history() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2019-12-23", "2021-06-01", "2022-06-09", "2024-04-02"]),
            "tickers": [
                "AAPL,FB,OLDCO",
                "AAPL,FB",
                "AAPL,META",
                "AAPL,META,PLTR",
            ],
        }
    )


def _current() -> pd.DataFrame:
    return pd.DataFrame({"symbol": ["AAPL", "META", "PLTR"], "name": None, "sector": None, "industry": None})


def test_stints_round_trip_and_renames() -> None:
    stints = stints_from_snapshots(_history(), _current(), today=date(2026, 9, 30))
    meta = stints.set_index("symbol")
    assert "FB" not in meta.index
    assert meta.loc["META", "start_date"] == date(2019, 12, 23)
    assert pd.isna(meta.loc["META", "end_date"])
    assert meta.loc["META", "source_symbols"] == "FB,META"
    assert meta.loc["OLDCO", "end_date"] == date(2021, 6, 1)
    assert meta.loc["PLTR", "start_date"] == date(2024, 4, 2)

    rebuilt = set(members_as_of(stints, "2026-09-30")["symbol"])
    assert rebuilt == set(_current()["symbol"])


def test_2020_snapshot_excludes_later_additions() -> None:
    stints = stints_from_snapshots(_history(), _current(), today=date(2026, 9, 30))
    early = set(members_as_of(stints, "2020-01-02")["symbol"])
    assert early == {"AAPL", "META", "OLDCO"}
    assert "PLTR" not in early
    assert "OLDCO" not in set(members_as_of(stints, "2021-06-01")["symbol"])


def test_current_names_missing_from_last_list_join_after_it() -> None:
    current = pd.concat([_current(), pd.DataFrame({"symbol": ["NEWCO"]})], ignore_index=True)
    stints = stints_from_snapshots(_history(), current, today=date(2026, 9, 30))
    assert members_as_of(stints, "2024-04-02").query("symbol == 'NEWCO'").empty
    assert not members_as_of(stints, "2026-09-30").query("symbol == 'NEWCO'").empty


def _seed(store: LocalCache) -> None:
    stints = stints_from_snapshots(
        pd.DataFrame(
            {
                "date": pd.to_datetime(["2019-12-23"]),
                "tickers": ["GOOD,BANK,NOSECTOR,LEVERED"],
            }
        ),
        pd.DataFrame({"symbol": ["GOOD", "BANK", "NOSECTOR", "LEVERED"]}),
        today=date(2026, 9, 30),
    )
    store.replace_stints(stints, "sp500")
    store.db.write_sector_map(
        {"GOOD": "information technology", "BANK": "conventional banking", "LEVERED": "industrials"}
    )
    rows = []
    for symbol, debt in (("GOOD", 1.0), ("BANK", 1.0), ("NOSECTOR", 1.0), ("LEVERED", 50.0)):
        rows.append(
            {
                "symbol": symbol,
                "as_of": pd.Timestamp("2020-01-31"),
                "report_date": pd.Timestamp("2019-12-31"),
                "filed_date": pd.Timestamp("2020-01-20"),
                "total_debt": debt,
                "cash_and_equiv": 1.0,
                "interest_bearing_securities": 0.0,
                "receivables": 1.0,
                "liquid_assets": 0.0,
                "market_cap": 100.0,
                "market_cap_24m": 100.0,
            }
        )
    store.write_metrics(pd.DataFrame(rows), freq="ME")


def test_sector_banned_name_is_never_compliant(store: LocalCache) -> None:
    _seed(store)
    out = hq.halal_universe("2020-02-15", cache=store).set_index("symbol")
    assert bool(out.loc["GOOD", "is_compliant"])
    assert bool(out.loc["BANK", "financial_pass"])
    assert not bool(out.loc["BANK", "is_compliant"])
    assert out.loc["BANK", "reason"] == "excluded activity: conventional banking"
    assert not bool(out.loc["NOSECTOR", "is_compliant"])
    assert out.loc["NOSECTOR", "reason"] == "missing sector label"
    assert not bool(out.loc["LEVERED", "is_compliant"])
    assert out.loc["LEVERED", "reason"] == "debt ratio exceeds threshold"
    assert bool(out["pit"].all())


def test_halal_universe_before_first_snapshot_has_no_metrics(store: LocalCache) -> None:
    _seed(store)
    out = hq.halal_universe("2020-01-15", cache=store)
    assert not out["is_compliant"].any()
    assert set(out.loc[out["sector_allowed"], "reason"]) == {"no AAOIFI metrics"}


def test_dividend_quality_flags() -> None:
    frame = pd.DataFrame(
        {
            "symbol": ["A", "A", "B", "C"],
            "ex_date": ["2024-01-02", "2024-01-02", "2024-02-01", "2024-03-01"],
            "dividend": [0.5, 0.5, 0.0, 1.0],
            "adj_dividend": [0.5, 0.5, 0.0, 0.25],
        }
    )
    issues = hq.dividend_quality(frame)
    assert set(issues["issue"]) == {"duplicate ex_date", "non-positive dividend", "adj_dividend mismatch"}


def test_impure_ratio_flags() -> None:
    metrics = pd.DataFrame(
        {"symbol": ["EXR", "AAPL"], "as_of": ["2024-01-31", "2024-01-31"], "impure_ratio": [0.83, 0.01]}
    )
    flags = hq.impure_ratio_flags(metrics)
    assert list(flags["symbol"]) == ["EXR"]
