"""Unit tests for DJIM thresholds and AAOIFI vs DJIM comparison."""

import numpy as np
import pandas as pd
import pytest

from halalquant.screening._aaoifi import AAOIFIScreener
from halalquant.screening._compare import compare_screeners
from halalquant.screening._djim import DJIMScreener


def _fundamentals(**overrides) -> pd.DataFrame:
    row = {
        "symbol": "AAA",
        "report_date": "2024-12-31",
        "total_debt": 20.0,
        "cash_and_equiv": 10.0,
        "interest_bearing_securities": 0.0,
        "receivables": 10.0,
        "liquid_assets": 0.0,
        "market_cap_24m": 100.0,
    }
    row.update(overrides)
    return pd.DataFrame([row])


def test_djim_evaluate_arrays_pass_and_fail():
    screener = DJIMScreener()
    total_debt = np.array([10.0, 40.0])
    cash = np.array([5.0, 5.0])
    mc = np.array([100.0, 100.0])

    result = screener.evaluate_arrays(total_debt, cash, mc)
    assert bool(result[0]) is True
    assert bool(result[1]) is False


def test_djim_receivables_threshold_is_33_percent():
    screener = DJIMScreener()
    # 32/100 < 0.33 passes; 33/100 does not (strict <)
    recv = np.array([32.0, 33.0])
    result = screener.evaluate_arrays(
        np.array([10.0, 10.0]),
        np.array([10.0, 10.0]),
        np.array([100.0, 100.0]),
        receivables_and_liquid=recv,
    )
    assert bool(result[0]) is True
    assert bool(result[1]) is False


def test_djim_evaluate_compliance_labels_standard():
    out = DJIMScreener().evaluate_compliance(_fundamentals())
    assert list(out["is_compliant"]) == [True]
    assert out.loc[0, "standard"] == "djim"
    assert "DJIM" in out.loc[0, "reason"]


def test_djim_empty_fundamentals():
    out = DJIMScreener().evaluate_compliance(pd.DataFrame())
    assert out.empty
    assert "is_compliant" in out.columns


def test_debt_between_aaoifi_and_djim_thresholds():
    """32% debt fails AAOIFI (30%) but passes DJIM (33%)."""
    frame = _fundamentals(total_debt=32.0)
    aaoifi = AAOIFIScreener().evaluate_compliance(frame)
    djim = DJIMScreener().evaluate_compliance(frame)
    assert bool(aaoifi.loc[0, "is_compliant"]) is False
    assert bool(djim.loc[0, "is_compliant"]) is True


def test_receivables_between_djim_and_aaoifi_thresholds():
    """50% receivables passes AAOIFI (70%) but fails DJIM (33%)."""
    frame = _fundamentals(receivables=50.0)
    aaoifi = AAOIFIScreener().evaluate_compliance(frame)
    djim = DJIMScreener().evaluate_compliance(frame)
    assert bool(aaoifi.loc[0, "is_compliant"]) is True
    assert bool(djim.loc[0, "is_compliant"]) is False


def test_compare_screeners_agreement_and_disagreement():
    frame = pd.DataFrame(
        [
            {
                "symbol": "PASS_BOTH",
                "report_date": "2024-12-31",
                "total_debt": 20.0,
                "cash_and_equiv": 10.0,
                "interest_bearing_securities": 0.0,
                "receivables": 10.0,
                "liquid_assets": 0.0,
                "market_cap_24m": 100.0,
            },
            {
                "symbol": "DJIM_ONLY",
                "report_date": "2024-12-31",
                "total_debt": 32.0,
                "cash_and_equiv": 10.0,
                "interest_bearing_securities": 0.0,
                "receivables": 10.0,
                "liquid_assets": 0.0,
                "market_cap_24m": 100.0,
            },
            {
                "symbol": "AAOIFI_ONLY",
                "report_date": "2024-12-31",
                "total_debt": 20.0,
                "cash_and_equiv": 10.0,
                "interest_bearing_securities": 0.0,
                "receivables": 50.0,
                "liquid_assets": 0.0,
                "market_cap_24m": 100.0,
            },
        ]
    )
    out = compare_screeners(frame)
    by_symbol = out.set_index("symbol")

    assert bool(by_symbol.loc["PASS_BOTH", "agreement"]) is True
    assert bool(by_symbol.loc["PASS_BOTH", "aaoifi_compliant"]) is True
    assert bool(by_symbol.loc["PASS_BOTH", "djim_compliant"]) is True

    assert bool(by_symbol.loc["DJIM_ONLY", "agreement"]) is False
    assert bool(by_symbol.loc["DJIM_ONLY", "aaoifi_compliant"]) is False
    assert bool(by_symbol.loc["DJIM_ONLY", "djim_compliant"]) is True

    assert bool(by_symbol.loc["AAOIFI_ONLY", "agreement"]) is False
    assert bool(by_symbol.loc["AAOIFI_ONLY", "aaoifi_compliant"]) is True
    assert bool(by_symbol.loc["AAOIFI_ONLY", "djim_compliant"]) is False

    assert by_symbol.loc["DJIM_ONLY", "debt_ratio"] == pytest.approx(0.32)


def test_compare_screeners_empty():
    out = compare_screeners(pd.DataFrame())
    assert out.empty
    assert "agreement" in out.columns
