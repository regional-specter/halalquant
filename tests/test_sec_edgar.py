"""Live SEC EDGAR companyfacts mapping."""

from __future__ import annotations

import pandas as pd

from halalquant.providers import SECEdgarProvider


def test_sec_maps_aapl_balance_sheet_and_income(filings: SECEdgarProvider) -> None:
    balance = filings.get_balance_sheet(["AAPL"])
    income = filings.get_income_statement(["AAPL"])
    assert not balance.empty
    latest = balance.sort_values("report_date").iloc[-1]
    assert latest["symbol"] == "AAPL"
    assert float(latest["cash_and_equiv"]) > 0
    assert float(latest["total_debt"]) > 0
    assert float(latest["shares_outstanding"]) > 0
    assert not income.empty
    assert income["total_revenue"].notna().any()


def test_sec_maps_bank_and_insurer_tags(filings: SECEdgarProvider) -> None:
    jpm = filings.get_balance_sheet(["JPM"]).sort_values("report_date").iloc[-1]
    met = filings.get_balance_sheet(["MET"]).sort_values("report_date").iloc[-1]
    assert float(jpm["cash_and_equiv"]) > 0
    assert float(jpm["interest_bearing_securities"]) > 0
    assert float(jpm["receivables"]) > 1e11
    assert float(jpm["total_debt"]) > 1e12
    assert float(met["cash_and_equiv"]) > 0
    assert float(met["total_debt"]) > 1e11


def test_sec_keeps_original_10k_filed_date(filings: SECEdgarProvider) -> None:
    balance = filings.get_balance_sheet(["AAPL"])
    fy2023 = balance[pd.to_datetime(balance["report_date"]) == pd.Timestamp("2023-09-30")]
    assert not fy2023.empty
    filed = pd.Timestamp(fy2023.iloc[0]["filed_date"])
    assert filed.year == 2023
    assert filed <= pd.Timestamp("2023-11-30")
