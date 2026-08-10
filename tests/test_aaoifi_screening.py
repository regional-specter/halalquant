"""Unit tests for AAOIFI financial ratio thresholds."""

import numpy as np
import pandas as pd

from halalquant.screening._aaoifi import AAOIFIScreener


def test_evaluate_arrays_pass_and_fail():
    screener = AAOIFIScreener(debt_threshold=0.30, cash_threshold=0.30)

    total_debt = np.array([10.0, 40.0])
    cash = np.array([5.0, 5.0])
    mc = np.array([100.0, 100.0])

    result = screener.evaluate_arrays(total_debt, cash, mc)
    assert bool(result[0]) is True
    assert bool(result[1]) is False


def test_evaluate_compliance_dataframe():
    screener = AAOIFIScreener()
    fundamentals = pd.DataFrame(
        {
            "symbol": ["AAA", "BBB"],
            "report_date": ["2024-12-31", "2024-12-31"],
            "total_debt": [20.0, 50.0],
            "cash_and_equiv": [10.0, 10.0],
            "interest_bearing_securities": [0.0, 0.0],
            "receivables": [10.0, 10.0],
            "liquid_assets": [0.0, 0.0],
            "market_cap_24m": [100.0, 100.0],
        }
    )
    out = screener.evaluate_compliance(fundamentals)
    assert list(out["is_compliant"]) == [True, False]
    assert out.loc[0, "standard"] == "aaoifi"
