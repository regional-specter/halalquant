"""Tests for dividend purification math."""

from halalquant.purification._purifier import Purifier


def test_impure_income_ratio():
    purifier = Purifier()
    ratio = purifier.impure_income_ratio(non_compliant_income=5.0, total_revenue=100.0)
    assert ratio == 0.05


def test_purification_amount():
    purifier = Purifier()
    amount = purifier.purification_amount(
        dividend=2.0,
        non_compliant_income=5.0,
        total_revenue=100.0,
    )
    assert amount == 0.1
