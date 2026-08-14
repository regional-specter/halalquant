"""SEC companyfacts mapping tests (no network)."""

from halalquant.providers._sec_edgar import SECEdgarProvider

FACTS = {
    "facts": {
        "us-gaap": {
            "DebtCurrent": {
                "units": {
                    "USD": [
                        {
                            "end": "2023-09-30",
                            "filed": "2023-11-03",
                            "val": 4.0,
                            "form": "10-K",
                            "fp": "FY",
                        }
                    ]
                }
            },
            "LongTermDebt": {
                "units": {
                    "USD": [
                        {
                            "end": "2023-09-30",
                            "filed": "2023-11-03",
                            "val": 6.0,
                            "form": "10-K",
                            "fp": "FY",
                        }
                    ]
                }
            },
            "CashAndCashEquivalentsAtCarryingValue": {
                "units": {
                    "USD": [
                        {
                            "end": "2023-09-30",
                            "filed": "2023-11-03",
                            "val": 50.0,
                            "form": "10-K",
                            "fp": "FY",
                        }
                    ]
                }
            },
            "AccountsReceivableNetCurrent": {
                "units": {
                    "USD": [
                        {
                            "end": "2023-09-30",
                            "filed": "2023-11-03",
                            "val": 8.0,
                            "form": "10-K",
                            "fp": "FY",
                        }
                    ]
                }
            },
            "CommonStockSharesOutstanding": {
                "units": {
                    "shares": [
                        {
                            "end": "2023-09-30",
                            "filed": "2023-11-03",
                            "val": 100.0,
                            "form": "10-K",
                            "fp": "FY",
                        }
                    ]
                }
            },
            "Revenues": {
                "units": {
                    "USD": [
                        {
                            "end": "2023-09-30",
                            "filed": "2023-11-03",
                            "val": 100.0,
                            "form": "10-K",
                            "fp": "FY",
                        }
                    ]
                }
            },
            "InvestmentIncomeInterest": {
                "units": {
                    "USD": [
                        {
                            "end": "2023-09-30",
                            "filed": "2023-11-03",
                            "val": 5.0,
                            "form": "10-K",
                            "fp": "FY",
                        }
                    ]
                }
            },
        }
    }
}


def test_sec_maps_annual_balance_sheet():
    provider = SECEdgarProvider()
    frame = provider._facts_to_balance_sheet("AAPL", FACTS)
    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["symbol"] == "AAPL"
    assert float(row["total_debt"]) == 10.0
    assert float(row["cash_and_equiv"]) == 50.0
    assert float(row["shares_outstanding"]) == 100.0


def test_sec_maps_annual_income():
    provider = SECEdgarProvider()
    frame = provider._facts_to_income("AAPL", FACTS)
    assert len(frame) == 1
    row = frame.iloc[0]
    assert float(row["total_revenue"]) == 100.0
    assert float(row["non_compliant_income"]) == 5.0
