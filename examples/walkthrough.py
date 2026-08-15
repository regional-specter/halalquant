#!/usr/bin/env python3
"""
Print live examples of every public strategy function.

Run from the repo root after `pip install -e .`:

    python examples/walkthrough.py

Output is meant to be copied into USAGE.md. Ratios and prices move;
re-run this script when you want a fresh snapshot.
"""

from __future__ import annotations

import pandas as pd

import halalquant as hq
from halalquant.providers import FilingsProvider, SECEdgarProvider, YFinanceProvider
from halalquant.purification import Purifier
from halalquant.screening import SectorFilter
from halalquant.utils._pit_adjustments import as_of_filter

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 140)
pd.set_option("display.max_colwidth", 48)
pd.set_option("display.float_format", lambda x: f"{x:,.4f}")


def section(title: str) -> None:
    print("\n" + "=" * 88)
    print(title)
    print("=" * 88)


def show(frame: pd.DataFrame, columns: list[str] | None = None, n: int | None = None) -> None:
    view = frame.copy()
    if columns:
        view = view[columns]
    if n is not None:
        view = view.head(n)
    print(view.to_string(index=False))
    print(f"\n[{len(frame)} row(s), {len(frame.columns)} column(s)]")


def main() -> None:
    market = YFinanceProvider()
    filings = FilingsProvider(sec=SECEdgarProvider(), yahoo=market)

    section("1. download — OHLCV prices")
    prices = hq.download("AAPL", start="2024-01-02", end="2024-01-08", provider=market)
    show(prices, n=5)

    section("2. purify_dividends — donate impure share of each dividend")
    purified = hq.purify_dividends(
        "AAPL",
        start="2024-01-01",
        end="2024-12-31",
        provider=market,
        filings=filings,
    )
    show(
        purified,
        ["symbol", "ex_date", "dividend", "report_date", "impure_ratio", "purification_amount"],
    )

    section("3. get_halal_universe — AAOIFI survivors (JPM dropped by sector)")
    universe = hq.get_halal_universe(
        ["AAPL", "MSFT", "JPM"],
        provider=market,
        filings=filings,
        apply_sector_filter=True,
    )
    show(
        universe,
        ["symbol", "as_of", "is_compliant", "debt_ratio", "cash_ratio", "receivables_ratio", "standard", "reason"],
    )

    section("4. get_halal_universe — DJIM, no sector filter (JPM still fails ratios)")
    unfiltered = hq.get_halal_universe(
        ["AAPL", "JPM"],
        standard="djim",
        provider=market,
        filings=filings,
        apply_sector_filter=False,
    )
    show(
        unfiltered,
        ["symbol", "is_compliant", "debt_ratio", "cash_ratio", "receivables_ratio", "standard", "reason"],
    )

    section("5. compare_standards — AAOIFI vs DJIM, including a non-US name")
    compared = hq.compare_standards(
        ["AAPL", "MSFT", "NESN.SW"],
        provider=market,
        filings=filings,
        apply_sector_filter=True,
    )
    show(
        compared,
        [
            "symbol",
            "as_of",
            "debt_ratio",
            "cash_ratio",
            "receivables_ratio",
            "aaoifi_compliant",
            "djim_compliant",
            "agreement",
        ],
    )

    section("6. get_financial_metrics — one row per annual filing")
    metrics = hq.get_financial_metrics(
        "AAPL",
        start="2021-01-01",
        end="2024-12-31",
        provider=market,
        filings=filings,
    )
    show(
        metrics,
        [
            "symbol",
            "as_of",
            "report_date",
            "filed_date",
            "debt_ratio",
            "cash_ratio",
            "receivables_ratio",
            "impure_ratio",
        ],
    )

    section("7. get_financial_metrics — monthly point-in-time snapshots")
    monthly = hq.get_financial_metrics(
        "AAPL",
        start="2023-10-01",
        end="2023-12-31",
        provider=market,
        filings=filings,
        freq="ME",
    )
    show(
        monthly,
        ["symbol", "as_of", "report_date", "filed_date", "debt_ratio", "cash_ratio"],
    )

    section("8. Purifier — the formula with small numbers (no network)")
    purifier = Purifier()
    print("impure_income_ratio(5, 100) =", purifier.impure_income_ratio(5.0, 100.0))
    print("purification_amount(2.00, 5, 100) =", purifier.purification_amount(2.0, 5.0, 100.0))

    section("9. SectorFilter — business-activity exclusion")
    sector_filter = SectorFilter()
    kept = sector_filter.filter_symbols(
        ["AAPL", "JPM", "MET"],
        sector_map={
            "AAPL": "technology",
            "JPM": "conventional banking",
            "MET": "conventional insurance",
        },
    )
    print("kept =", kept)
    print("audit_log =", sector_filter.audit_log)

    section("10. FilingsProvider — SEC filed_date vs Yahoo 90-day lag")
    aapl_bs = filings.get_balance_sheet(["AAPL"])
    nesn_bs = filings.get_balance_sheet(["NESN.SW"])
    aapl_fy = aapl_bs[pd.to_datetime(aapl_bs["report_date"]) == pd.Timestamp("2023-09-30")]
    print("AAPL FY2023 (SEC):")
    show(aapl_fy, ["symbol", "report_date", "filed_date", "total_debt", "cash_and_equiv"])
    print("NESN.SW latest annual (Yahoo, filed_date = report_date + 90 days):")
    show(
        nesn_bs.sort_values("report_date").tail(1),
        ["symbol", "report_date", "filed_date", "total_debt", "cash_and_equiv"],
    )

    section("11. as_of_filter — only the latest filing already public")
    known = as_of_filter(aapl_bs, as_of="2023-11-10")
    print("Latest AAPL balance sheet known on 2023-11-10:")
    show(known, ["symbol", "report_date", "filed_date"])


if __name__ == "__main__":
    main()
