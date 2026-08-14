"""Look-ahead bias prevention tests."""

import pandas as pd

from halalquant.utils._pit_adjustments import as_of_filter, known_filings, prevent_lookahead_prices

def test_known_filings_keeps_history():
    frame = pd.DataFrame(
        {
            "symbol": ["AAA", "AAA", "AAA"],
            "report_date": ["2023-12-31", "2024-06-30", "2024-12-31"],
            "filed_date": ["2024-02-15", "2024-08-01", "2025-02-20"],
            "total_debt": [1.0, 2.0, 3.0],
        }
    )
    out = known_filings(frame, as_of="2024-09-01")
    assert len(out) == 2
    assert list(out["report_date"]) == ["2023-12-31", "2024-06-30"]


def test_as_of_filter_keeps_only_known_filings():
    frame = pd.DataFrame(
        {
            "symbol": ["AAA", "AAA", "AAA"],
            "report_date": ["2023-12-31", "2024-06-30", "2024-12-31"],
            "filed_date": ["2024-02-15", "2024-08-01", "2025-02-20"],
            "total_debt": [1.0, 2.0, 3.0],
        }
    )
    out = as_of_filter(frame, as_of="2024-09-01")
    assert len(out) == 1
    assert out.iloc[0]["report_date"] == "2024-06-30"


def test_prevent_lookahead_prices():
    prices = pd.DataFrame(
        {
            "symbol": ["AAA", "AAA"],
            "date": ["2024-01-01", "2024-01-10"],
            "close": [10.0, 11.0],
        }
    )
    out = prevent_lookahead_prices(prices, as_of="2024-01-05")
    assert len(out) == 1
    assert out.iloc[0]["date"] == "2024-01-01"
