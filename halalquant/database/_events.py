"""Filing index and the AAOIFI breach feed."""

from __future__ import annotations

from datetime import date
from typing import Optional, Sequence, Union

import pandas as pd

from halalquant.database._cache import CacheLike, LocalCache, resolve_cache
from halalquant.screening._aaoifi import AAOIFIScreener, compute_ratios
from halalquant.utils._metrics import fill_market_caps_from_prices
from halalquant.utils.validation import validate_symbols

FILING_EVENT_COLUMNS = (
    "symbol",
    "cik",
    "form",
    "report_date",
    "filed_date",
    "fiscal_period",
    "total_debt",
    "cash_and_equiv",
    "receivables",
    "shares_outstanding",
    "market_cap_24m",
    "debt_ratio",
    "cash_ratio",
    "receivables_ratio",
    "is_compliant",
    "reason",
)


def statements_to_filings(*frames: pd.DataFrame) -> pd.DataFrame:
    """One filing-index row per statement already in the cache."""
    rows: list[pd.DataFrame] = []
    for frame in frames:
        if frame is None or frame.empty:
            continue
        if "symbol" not in frame.columns or "filed_date" not in frame.columns:
            continue
        out = pd.DataFrame(
            {
                "symbol": frame["symbol"].astype(str),
                "cik": frame["cik"] if "cik" in frame.columns else "",
                "form": frame["form"] if "form" in frame.columns else "",
                "report_date": frame["report_date"] if "report_date" in frame.columns else pd.NaT,
                "filed_date": frame["filed_date"],
                "fiscal_period": frame["fiscal_period"] if "fiscal_period" in frame.columns else "",
            }
        )
        rows.append(out)
    if not rows:
        return pd.DataFrame(columns=["symbol", "cik", "form", "report_date", "filed_date", "fiscal_period"])
    combined = pd.concat(rows, ignore_index=True)
    combined["form"] = combined["form"].fillna("").astype(str).str.upper()
    combined["fiscal_period"] = combined["fiscal_period"].fillna("").astype(str).str.upper()
    combined["cik"] = combined["cik"].fillna("").astype(str)
    combined.loc[combined["form"].isin(["", "NAN", "NONE", "<NA>"]), "form"] = ""
    quarter = combined["fiscal_period"].isin(["Q1", "Q2", "Q3", "Q4"])
    combined.loc[combined["form"].eq("") & quarter, "form"] = "10-Q"
    combined.loc[combined["form"].eq("") & ~quarter, "form"] = "10-K"
    combined = combined.dropna(subset=["symbol", "filed_date"])
    combined = combined.drop_duplicates(
        ["symbol", "form", "report_date", "filed_date", "fiscal_period"]
    )
    return combined.reset_index(drop=True)


def index_cached_filings(store: LocalCache, symbols: Optional[Sequence[str]] = None) -> int:
    """Write the filing index from cached statements. Returns rows written."""
    symbol_list = list(symbols) if symbols is not None else None
    balance = store.db.read_balance_sheets(symbol_list)
    income = store.db.read_income_statements(symbol_list)
    indexed = statements_to_filings(balance, income)
    if indexed.empty:
        return 0
    store.write_filings(indexed)
    return len(indexed)


def shares_known_on(balance: pd.DataFrame, symbol: str, as_of) -> Optional[float]:
    """
    Shares outstanding that were already public on ``as_of``.

    A later 10-Q's share count is not used. That would be look-ahead.
    """
    if balance is None or balance.empty or "shares_outstanding" not in balance.columns:
        return None
    rows = balance.loc[balance["symbol"].astype(str) == str(symbol)].copy()
    if rows.empty:
        return None
    filed = pd.to_datetime(rows["filed_date"], errors="coerce")
    rows = rows.loc[filed.notna() & (filed <= pd.Timestamp(as_of))].copy()
    rows["_shares"] = pd.to_numeric(rows["shares_outstanding"], errors="coerce")
    rows = rows.loc[rows["_shares"].notna() & (rows["_shares"] > 0)]
    if rows.empty:
        return None
    rows["_filed"] = pd.to_datetime(rows["filed_date"], errors="coerce")
    latest = rows.sort_values(["_filed", "report_date"]).iloc[-1]
    return float(latest["_shares"])


def filing_events(
    as_of: Optional[Union[str, date]] = None,
    since: Optional[Union[str, date]] = None,
    tickers: Optional[Union[str, Sequence[str]]] = None,
    cache: CacheLike = True,
    provider=None,
    filings=None,
) -> pd.DataFrame:
    """
    New 10-Q / 10-K rows and AAOIFI ratios versus the 24-month market cap
    **as of ``filed_date``**.

    ``tickers=None`` uses the cached universe (sector-kept names). Pass
    holdings when the shadow book should not scan the whole index.

    Market cap uses the shares and the close that were known on ``filed_date``.
    """
    store = resolve_cache(cache, provider=provider, filings=filings)
    if store is None:
        store = LocalCache(provider=provider, filings=filings)
    end = pd.Timestamp(as_of).date() if as_of is not None else date.today()
    since_s = str(since)[:10] if since is not None else None
    symbols = _event_symbols(store, tickers)
    if not symbols:
        return pd.DataFrame(columns=list(FILING_EVENT_COLUMNS))

    index_cached_filings(store, symbols)
    events = store.read_filings(symbols, since=since_s, as_of=end.isoformat())
    if events.empty:
        return pd.DataFrame(columns=list(FILING_EVENT_COLUMNS))
    events = events.copy()
    events["cik"] = events["cik"].fillna("").astype(str)
    grouped_cik = (
        events.replace({"": pd.NA})
        .groupby(["symbol", "form", "report_date", "filed_date"], dropna=False)["cik"]
        .transform(lambda values: next((item for item in values if pd.notna(item) and str(item).strip()), ""))
    )
    events["cik"] = grouped_cik.fillna("").astype(str)
    events["_blank_period"] = events["fiscal_period"].fillna("").astype(str).str.strip().eq("").astype(int)
    events = (
        events.sort_values("_blank_period")
        .drop_duplicates(["symbol", "form", "report_date", "filed_date"], keep="first")
        .drop(columns="_blank_period")
    )

    balance = store.db.read_balance_sheets(symbols)
    prices = store.db.read_prices(symbols, end=end.isoformat())
    screened = _screen_filings(events, balance, prices)
    return screened[list(FILING_EVENT_COLUMNS)].reset_index(drop=True)


def _event_symbols(store: LocalCache, tickers: Optional[Union[str, Sequence[str]]]) -> list[str]:
    if tickers is not None:
        return validate_symbols(tickers)
    members = store.read_universe()
    if not members.empty:
        allowed = members
        if "sector_allowed" in members.columns:
            allowed = members.loc[members["sector_allowed"].astype(bool)]
        symbols = [str(symbol) for symbol in allowed["symbol"].tolist()]
        if symbols:
            return validate_symbols(symbols)
    filings = store.read_filings()
    if filings.empty:
        return []
    return validate_symbols(filings["symbol"].astype(str).unique().tolist())


def _screen_filings(
    events: pd.DataFrame,
    balance: pd.DataFrame,
    prices: pd.DataFrame,
) -> pd.DataFrame:
    fundamentals = _fundamentals_for_events(events, balance)
    if fundamentals.empty:
        out = events.copy()
        for col in FILING_EVENT_COLUMNS:
            if col not in out.columns:
                out[col] = pd.NA
        out["is_compliant"] = False
        out["reason"] = "missing balance sheet"
        return out

    # Price the 24-month cap on filed_date, not on the fiscal period end and
    # not on today's close. fill_market_caps reads report_date as the price date.
    filed_caps = fundamentals.copy()
    filed_caps["report_date"] = pd.to_datetime(filed_caps["filed_date"], errors="coerce").dt.date
    filed_caps["market_cap"] = pd.NA
    filed_caps["market_cap_24m"] = pd.NA
    filed_caps = fill_market_caps_from_prices(
        filed_caps,
        prices,
        as_of=fundamentals["filed_date"].max(),
    )
    fundamentals = fundamentals.copy()
    fundamentals["market_cap"] = filed_caps["market_cap"].to_numpy()
    fundamentals["market_cap_24m"] = filed_caps["market_cap_24m"].to_numpy()
    fundamentals["shares_outstanding"] = filed_caps["shares_outstanding"].to_numpy()

    ratios = compute_ratios(fundamentals)
    screen = AAOIFIScreener().evaluate_compliance(fundamentals)
    out = fundamentals.copy()
    out["debt_ratio"] = ratios["debt_ratio"].to_numpy()
    out["cash_ratio"] = ratios["cash_ratio"].to_numpy()
    out["receivables_ratio"] = ratios["receivables_ratio"].to_numpy()
    out["market_cap_24m"] = ratios["market_cap_24m"].to_numpy()
    out["is_compliant"] = screen["is_compliant"].to_numpy()
    out["reason"] = screen["reason"].to_numpy()
    for col in FILING_EVENT_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA
    return out


def _fundamentals_for_events(events: pd.DataFrame, balance: pd.DataFrame) -> pd.DataFrame:
    if balance is None or balance.empty:
        return pd.DataFrame()
    sheets = balance.copy()
    sheets["_report"] = pd.to_datetime(sheets["report_date"], errors="coerce")
    sheets["_filed"] = pd.to_datetime(sheets["filed_date"], errors="coerce")
    grouped = {symbol: frame for symbol, frame in sheets.groupby(sheets["symbol"].astype(str))}
    rows: list[dict] = []
    for _, event in events.iterrows():
        symbol = str(event["symbol"])
        known = grouped.get(symbol)
        payload = {
            "symbol": symbol,
            "cik": event.get("cik") or "",
            "form": event.get("form") or "",
            "report_date": event.get("report_date"),
            "filed_date": event.get("filed_date"),
            "fiscal_period": event.get("fiscal_period") or "",
        }
        if known is None:
            rows.append(payload)
            continue
        report = pd.Timestamp(event["report_date"]) if pd.notna(event.get("report_date")) else None
        filed = pd.Timestamp(event["filed_date"])
        match = known[known["_filed"] <= filed]
        if report is not None:
            same = match[match["_report"] == report]
            if not same.empty:
                match = same
        if match.empty:
            rows.append(payload)
            continue
        # Prefer the full-year statement when a quarterly slice shares the date.
        match = match.copy()
        period = match["fiscal_period"].fillna("").astype(str).str.upper() if "fiscal_period" in match.columns else ""
        if isinstance(period, pd.Series):
            match["_rank"] = period.isin(["Q1", "Q2", "Q3", "Q4"]).astype(int)
        else:
            match["_rank"] = 0
        chosen = match.sort_values(["_filed", "_rank"], ascending=[False, True]).iloc[0]
        for col in (
            "total_debt",
            "short_term_debt",
            "long_term_debt",
            "cash_and_equiv",
            "interest_bearing_securities",
            "receivables",
            "liquid_assets",
            "shares_outstanding",
        ):
            if col in chosen.index:
                payload[col] = chosen[col]
        if payload.get("shares_outstanding") is None or pd.isna(payload.get("shares_outstanding")):
            payload["shares_outstanding"] = shares_known_on(balance, symbol, filed.date())
        rows.append(payload)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


