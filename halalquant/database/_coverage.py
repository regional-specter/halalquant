"""Data-quality report for a Shariah reviewer. Not an alpha diagnostic."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional, Sequence, Union

import pandas as pd

from halalquant.database._cache import CacheLike, LocalCache, resolve_cache
from halalquant.database._universe import members_between
from halalquant.utils.validation import validate_symbols

DateLike = Union[str, date]


def coverage_report(
    tickers: Optional[Union[str, Sequence[str]]] = None,
    universe: str = "sp500",
    start: Optional[DateLike] = None,
    end: Optional[DateLike] = None,
    as_of: Optional[DateLike] = None,
    cache: CacheLike = True,
    adv_window: int = 20,
    provider=None,
    filings=None,
) -> pd.DataFrame:
    """
    One row per name: missing FCF, impure ratio, restated 10-Ks, CIK and
    sector gaps, 20-day median dollar ADV, and whether prices cover the
    requested span.

    Leavers in the point-in-time universe are kept even when Yahoo has no
    bars. Those rows have ``price_missing`` true. They are not dropped.
    """
    store = resolve_cache(cache, provider=provider, filings=filings)
    if store is None:
        store = LocalCache(provider=provider, filings=filings)
    as_of_d = pd.Timestamp(as_of).date() if as_of is not None else date.today()
    start_d = pd.Timestamp(start).date() if start is not None else None
    end_d = pd.Timestamp(end).date() if end is not None else as_of_d
    symbols = _coverage_symbols(store, tickers, universe, start_d, end_d)
    if not symbols:
        return _empty_coverage()

    metrics = store.db.read_metrics(symbols)
    balance = store.db.read_balance_sheets(symbols)
    income = store.db.read_income_statements(symbols)
    prices = store.db.read_prices(symbols, end=end_d.isoformat())
    sectors = store.db.read_sector_map(symbols)
    members = store.read_universe(universe if tickers is None else None)
    sector_from_members = {}
    if not members.empty and "sector" in members.columns:
        sector_from_members = {
            str(row["symbol"]): row["sector"]
            for _, row in members.iterrows()
            if pd.notna(row.get("sector"))
        }

    latest = _latest_metrics(metrics)
    rows: list[dict] = []
    for symbol in symbols:
        metric = latest.get(symbol)
        price = prices[prices["symbol"] == symbol] if not prices.empty else prices
        n_prices = 0 if price is None or price.empty else int(len(price))
        price_min = pd.to_datetime(price["date"]).min().date() if n_prices else None
        price_max = pd.to_datetime(price["date"]).max().date() if n_prices else None
        span_ok = False
        if n_prices and start_d is not None:
            span_ok = price_min <= start_d and price_max >= end_d - timedelta(days=7)
        elif n_prices and start_d is None:
            span_ok = bool(price_max and price_max >= end_d - timedelta(days=7))
        filed = _filed_dates(balance, income, symbol)
        sector = sectors.get(symbol) or sector_from_members.get(symbol)
        sector_text = "" if sector is None or pd.isna(sector) else str(sector)
        rows.append(
            {
                "symbol": symbol,
                "has_fcf": _has(metric, "free_cash_flow"),
                "has_ocf": _has(metric, "operating_cash_flow"),
                "has_capex": _has(metric, "capital_expenditure"),
                "has_impure_ratio": _has(metric, "impure_ratio"),
                "fcf_basis": None if metric is None else metric.get("fcf_basis"),
                "n_restatements": _restatement_count(balance, symbol),
                "has_cik": _has_cik(store, symbol),
                "sector": sector_text or None,
                "sector_gap": sector_text == "",
                "adv_20d": _adv(price, adv_window),
                "n_prices": n_prices,
                "price_min": price_min,
                "price_max": price_max,
                "filing_min": min(filed) if filed else None,
                "filing_max": max(filed) if filed else None,
                "span_ok": bool(span_ok),
                "price_missing": n_prices == 0,
            }
        )
    return pd.DataFrame(rows)


def coverage_summary(detail: pd.DataFrame) -> pd.DataFrame:
    """Reviewer one-pager: counts and percentages from :func:`coverage_report`."""
    frame = detail if detail is not None else _empty_coverage()
    n = len(frame)

    def pct(column: str) -> Optional[float]:
        if n == 0 or column not in frame.columns:
            return None
        return float(frame[column].fillna(False).astype(bool).mean())

    checks = [
        ("n_symbols", n, "names in this report, including leavers with no prices"),
        ("pct_with_fcf", pct("has_fcf"), "share of names with free cash flow"),
        ("pct_with_ocf", pct("has_ocf"), "share of names with operating cash flow"),
        ("pct_with_capex", pct("has_capex"), "share of names with capital expenditure"),
        ("pct_with_impure_ratio", pct("has_impure_ratio"), "share of names with interest/revenue"),
        ("pct_with_ttm_fcf", _ttm_share(frame), "share of names whose latest snapshot is TTM FCF"),
        ("n_restatements", int(frame["n_restatements"].fillna(0).sum()) if n and "n_restatements" in frame else 0, "10-K period ends with more than one filed date"),
        ("n_cik_misses", _cik_misses(frame), "US tickers with no SEC CIK"),
        ("n_sector_gaps", int(frame["sector_gap"].fillna(False).astype(bool).sum()) if n and "sector_gap" in frame else 0, "names with no sector label"),
        ("n_price_missing", int(frame["price_missing"].fillna(False).astype(bool).sum()) if n and "price_missing" in frame else 0, "names with no Yahoo bars; kept, not dropped"),
        ("median_adv_20d", _median(frame, "adv_20d"), "median 20-day dollar ADV"),
    ]
    return pd.DataFrame(checks, columns=["check", "value", "notes"])


def _coverage_symbols(store, tickers, universe, start, end) -> list[str]:
    if tickers is not None:
        return validate_symbols(tickers)
    symbols: list[str] = []
    members = store.read_universe(universe)
    if not members.empty:
        symbols.extend(str(symbol) for symbol in members["symbol"].tolist())
    if start is not None or end is not None:
        stints = store.read_stints(universe if universe else None)
        if not stints.empty:
            window = members_between(stints, start or date(1990, 1, 1), end or date.today())
            symbols.extend(str(symbol) for symbol in window["symbol"].tolist())
    if not symbols:
        prices = store.db.read_prices()
        if not prices.empty:
            symbols.extend(str(symbol) for symbol in prices["symbol"].unique())
    deduped = list(dict.fromkeys(symbols))
    return validate_symbols(deduped) if deduped else []


def _latest_metrics(metrics: pd.DataFrame) -> dict[str, pd.Series]:
    if metrics is None or metrics.empty:
        return {}
    frame = metrics.copy()
    frame["_as_of"] = pd.to_datetime(frame["as_of"], errors="coerce")
    prefer = frame["freq"].astype(str).ne("annual") if "freq" in frame.columns else False
    frame["_prefer"] = prefer.astype(int) if hasattr(prefer, "astype") else 0
    frame = frame.sort_values(["_prefer", "_as_of"])
    latest = frame.groupby(frame["symbol"].astype(str), as_index=False).tail(1)
    return {str(row["symbol"]): row for _, row in latest.iterrows()}


def _has(metric, column: str) -> bool:
    if metric is None or column not in metric.index:
        return False
    value = pd.to_numeric(metric[column], errors="coerce")
    return bool(pd.notna(value))


def _filed_dates(balance, income, symbol: str) -> list:
    dates = []
    for frame in (balance, income):
        if frame is None or frame.empty:
            continue
        rows = frame.loc[frame["symbol"].astype(str) == symbol, "filed_date"]
        parsed = pd.to_datetime(rows, errors="coerce").dropna()
        dates.extend(item.date() for item in parsed)
    return dates


def _restatement_count(balance: pd.DataFrame, symbol: str) -> int:
    if balance is None or balance.empty:
        return 0
    rows = balance.loc[balance["symbol"].astype(str) == symbol].copy()
    if rows.empty:
        return 0
    form = rows["form"].fillna("").astype(str).str.upper() if "form" in rows.columns else pd.Series("", index=rows.index)
    period = rows["fiscal_period"].fillna("").astype(str).str.upper() if "fiscal_period" in rows.columns else pd.Series("", index=rows.index)
    annual = form.isin(["", "10-K", "10-K/A"]) & ~period.isin(["Q1", "Q2", "Q3", "Q4"])
    rows = rows.loc[annual]
    if rows.empty:
        return 0
    counts = rows.groupby(pd.to_datetime(rows["report_date"], errors="coerce"))["filed_date"].nunique()
    return int((counts > 1).sum())


def _has_cik(store: LocalCache, symbol: str) -> Optional[bool]:
    provider = store.filings
    sec = provider if hasattr(provider, "has_cik") else getattr(provider, "sec", None)
    if sec is None or not hasattr(sec, "has_cik"):
        return None
    try:
        return bool(sec.has_cik(symbol))
    except (ValueError, OSError):
        return None


def _adv(prices: pd.DataFrame, window: int) -> Optional[float]:
    if prices is None or prices.empty or "close" not in prices.columns or "volume" not in prices.columns:
        return None
    frame = prices.sort_values("date").tail(window)
    dollar = pd.to_numeric(frame["close"], errors="coerce") * pd.to_numeric(frame["volume"], errors="coerce")
    dollar = dollar.dropna()
    if dollar.empty:
        return None
    return float(dollar.median())


def _ttm_share(frame: pd.DataFrame) -> Optional[float]:
    if frame.empty or "fcf_basis" not in frame.columns:
        return None
    basis = frame["fcf_basis"].fillna("").astype(str).str.lower()
    return float((basis == "ttm").mean())


def _cik_misses(frame: pd.DataFrame) -> int:
    if frame.empty or "has_cik" not in frame.columns:
        return 0
    known = frame["has_cik"].map(lambda value: value is False)
    return int(known.sum())


def _median(frame: pd.DataFrame, column: str):
    if frame.empty or column not in frame.columns:
        return None
    series = pd.to_numeric(frame[column], errors="coerce").dropna()
    if series.empty:
        return None
    return float(series.median())


def _empty_coverage() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "symbol",
            "has_fcf",
            "has_ocf",
            "has_capex",
            "has_impure_ratio",
            "fcf_basis",
            "n_restatements",
            "has_cik",
            "sector",
            "sector_gap",
            "adv_20d",
            "n_prices",
            "price_min",
            "price_max",
            "filing_min",
            "filing_max",
            "span_ok",
            "price_missing",
        ]
    )
