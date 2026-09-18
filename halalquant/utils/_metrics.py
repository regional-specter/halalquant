"""Build screening-ratio panels from prices, filings, and income."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional, Union

import pandas as pd

from halalquant.base import METRIC_COLUMNS
from halalquant.purification._purifier import Purifier
from halalquant.screening._aaoifi import compute_ratios
from halalquant.utils._pit_adjustments import as_of_filter, known_filings

DateLike = Union[str, date]

_WINDOW_DAYS = int(24 * 30.44)
_LOOKBACK = timedelta(days=_WINDOW_DAYS + 14)


def normalize_freq(freq: str) -> str:
    aliases = {"M": "ME", "Q": "QE", "Y": "YE", "A": "YE"}
    return aliases.get(str(freq).upper(), freq)


def lookback() -> timedelta:
    return _LOOKBACK


def price_window_for_fundamentals(
    fundamentals: pd.DataFrame,
    as_of: DateLike,
) -> tuple[date, date]:
    end = pd.Timestamp(str(as_of)[:10]).date()
    start = end - _LOOKBACK
    if "report_date" in fundamentals.columns:
        report_dates = pd.to_datetime(fundamentals["report_date"], errors="coerce")
        if report_dates.notna().any():
            start = min(start, report_dates.min().date() - _LOOKBACK)
            end = max(end, report_dates.max().date())
    return start, end


def fill_market_caps_from_prices(
    fundamentals: pd.DataFrame,
    prices: pd.DataFrame,
    as_of: DateLike,
    price_as_of: Optional[DateLike] = None,
) -> pd.DataFrame:
    """Fill missing market cap using shares outstanding × trailing prices."""
    out = fundamentals.copy()
    if out.empty:
        return out

    needs_cap = (
        out["market_cap"].isna() if "market_cap" in out.columns else pd.Series(True, index=out.index)
    )
    needs_24 = (
        out["market_cap_24m"].isna()
        if "market_cap_24m" in out.columns
        else pd.Series(True, index=out.index)
    )
    if not (needs_cap | needs_24).any():
        return out
    if "shares_outstanding" not in out.columns or out["shares_outstanding"].isna().all():
        return out
    if prices is None or prices.empty:
        return out

    prices = prices.copy()
    prices["date"] = pd.to_datetime(prices["date"], errors="coerce").astype("datetime64[ns]")
    prices["close"] = pd.to_numeric(prices["close"], errors="coerce")
    prices = prices.dropna(subset=["date", "close", "symbol"]).sort_values(["symbol", "date"])
    if prices.empty:
        return out

    if price_as_of is not None:
        out["_end"] = pd.Timestamp(str(price_as_of)[:10])
    else:
        out["_end"] = pd.to_datetime(out.get("report_date"), errors="coerce")
        out["_end"] = out["_end"].fillna(pd.Timestamp(str(as_of)[:10]))
    out["_end"] = pd.to_datetime(out["_end"], errors="coerce").astype("datetime64[ns]")

    left = out.reset_index().rename(columns={"index": "_idx"})
    left["_end"] = pd.to_datetime(left["_end"], errors="coerce").astype("datetime64[ns]")
    keyed = left.dropna(subset=["_end", "symbol"]).sort_values("_end")
    if keyed.empty:
        out = out.drop(columns=["_end"], errors="ignore")
        return out

    right_prices = prices[["symbol", "date", "close"]].sort_values("date")
    spot = pd.merge_asof(
        keyed[["_idx", "symbol", "_end"]],
        right_prices,
        left_on="_end",
        right_on="date",
        by="symbol",
        direction="backward",
    )

    indexed = prices.set_index("date").sort_index()
    rolled = (
        indexed.groupby("symbol", sort=False)["close"]
        .rolling(f"{_WINDOW_DAYS}D", min_periods=1)
        .mean()
        .rename("close_24m")
        .reset_index()
    )
    rolled["date"] = pd.to_datetime(rolled["date"], errors="coerce").astype("datetime64[ns]")
    avg = pd.merge_asof(
        keyed[["_idx", "symbol", "_end"]].sort_values("_end"),
        rolled.sort_values("date"),
        left_on="_end",
        right_on="date",
        by="symbol",
        direction="backward",
    )

    shares = pd.to_numeric(out["shares_outstanding"], errors="coerce")
    spot_close = pd.to_numeric(spot.set_index("_idx")["close"], errors="coerce").reindex(out.index)
    avg_close = pd.to_numeric(avg.set_index("_idx")["close_24m"], errors="coerce").reindex(out.index)

    spot_mc = shares * spot_close
    avg_mc = shares * avg_close
    avg_mc = avg_mc.where(avg_mc.notna(), spot_mc)

    if "market_cap" in out.columns:
        out["market_cap"] = out["market_cap"].where(out["market_cap"].notna(), spot_mc)
    else:
        out["market_cap"] = spot_mc
    if "market_cap_24m" in out.columns:
        out["market_cap_24m"] = out["market_cap_24m"].where(out["market_cap_24m"].notna(), avg_mc)
    else:
        out["market_cap_24m"] = avg_mc

    return out.drop(columns=["_end"], errors="ignore")


def join_income_metrics(panel: pd.DataFrame, income: pd.DataFrame) -> pd.DataFrame:
    extra = [
        "total_revenue",
        "non_compliant_income",
        "impure_ratio",
        "ebitda",
        "operating_cash_flow",
        "capital_expenditure",
        "free_cash_flow",
    ]
    out = panel.copy()
    for col in extra:
        if col not in out.columns:
            out[col] = pd.NA
    if income.empty or "report_date" not in out.columns:
        return out

    income = income.copy()
    income["_report"] = pd.to_datetime(income["report_date"], errors="coerce")
    income["_filed"] = pd.to_datetime(income["filed_date"], errors="coerce")
    grouped = {symbol: frame for symbol, frame in income.groupby("symbol")}
    purifier = Purifier()

    revenues: list = []
    impure_income: list = []
    ebitda: list = []
    ocf: list = []
    capex: list = []
    fcf: list = []
    for _, row in out.iterrows():
        as_of_ts = pd.Timestamp(row.get("as_of") or row.get("filed_date"))
        report_ts = pd.to_datetime(row.get("report_date"), errors="coerce")
        known = grouped.get(row["symbol"])
        if known is None:
            revenues.append(pd.NA)
            impure_income.append(pd.NA)
            ebitda.append(pd.NA)
            ocf.append(pd.NA)
            capex.append(pd.NA)
            fcf.append(pd.NA)
            continue
        known = known[known["_filed"] <= as_of_ts]
        if known.empty:
            revenues.append(pd.NA)
            impure_income.append(pd.NA)
            ebitda.append(pd.NA)
            ocf.append(pd.NA)
            capex.append(pd.NA)
            fcf.append(pd.NA)
            continue
        if pd.notna(report_ts):
            same_period = known[known["_report"] == report_ts]
            pick = same_period if not same_period.empty else known
        else:
            pick = known
        latest = pick.sort_values(["_report", "_filed"]).iloc[-1]
        revenues.append(latest.get("total_revenue"))
        impure_income.append(latest.get("non_compliant_income"))
        ebitda.append(latest.get("ebitda") if "ebitda" in latest.index else pd.NA)
        ocf.append(latest.get("operating_cash_flow") if "operating_cash_flow" in latest.index else pd.NA)
        capex.append(latest.get("capital_expenditure") if "capital_expenditure" in latest.index else pd.NA)
        fcf.append(latest.get("free_cash_flow") if "free_cash_flow" in latest.index else pd.NA)

    out["total_revenue"] = revenues
    out["non_compliant_income"] = impure_income
    out["ebitda"] = ebitda
    out["operating_cash_flow"] = ocf
    out["capital_expenditure"] = capex
    out["free_cash_flow"] = fcf
    out["impure_ratio"] = purifier.impure_income_ratio(
        pd.to_numeric(out["non_compliant_income"], errors="coerce"),
        pd.to_numeric(out["total_revenue"], errors="coerce"),
    )
    return out


def build_financial_metrics(
    fundamentals: pd.DataFrame,
    income: pd.DataFrame,
    prices: pd.DataFrame,
    start: DateLike,
    end: DateLike,
    freq: Optional[str] = None,
) -> pd.DataFrame:
    """
    Assemble AAOIFI ratios (and income / cash-flow extras) over a date range.

    Default: one row per annual filing whose report date falls in
    ``[start, end]`` and was already public by ``end``. Pass ``freq``
    (``"ME"``, ``"QE"``) for calendar point-in-time snapshots.
    """
    empty = pd.DataFrame(columns=list(METRIC_COLUMNS))
    if fundamentals is None or fundamentals.empty:
        return empty

    if freq:
        pandas_freq = normalize_freq(freq)
        stamps = pd.date_range(start=start, end=end, freq=pandas_freq)
        snapshots: list[pd.DataFrame] = []
        for stamp in stamps:
            snap = as_of_filter(fundamentals, as_of=stamp)
            if snap.empty:
                continue
            snap = fill_market_caps_from_prices(
                snap,
                prices,
                as_of=stamp.date(),
                price_as_of=stamp.date(),
            )
            snap["as_of"] = stamp.date()
            snapshots.append(snap)
        if not snapshots:
            return empty
        panel = pd.concat(snapshots, ignore_index=True)
    else:
        panel = known_filings(fundamentals, as_of=str(end)[:10])
        if panel.empty:
            return empty
        if not income.empty:
            income = known_filings(income, as_of=str(end)[:10])
        report_ts = pd.to_datetime(panel["report_date"], errors="coerce")
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        panel = panel[(report_ts >= start_ts) & (report_ts <= end_ts)].copy()
        if panel.empty:
            return empty
        panel = fill_market_caps_from_prices(panel, prices, as_of=end)
        panel["as_of"] = pd.to_datetime(panel["filed_date"], errors="coerce").dt.date

    ratios = compute_ratios(panel)
    panel["debt_ratio"] = ratios["debt_ratio"].values
    panel["cash_ratio"] = ratios["cash_ratio"].values
    panel["receivables_ratio"] = ratios["receivables_ratio"].values
    panel = join_income_metrics(panel, income)

    for col in METRIC_COLUMNS:
        if col not in panel.columns:
            panel[col] = pd.NA
    return panel[list(METRIC_COLUMNS)].reset_index(drop=True)
