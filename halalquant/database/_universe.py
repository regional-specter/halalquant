"""Named equity universes used to warm the local dataset."""

from __future__ import annotations

from io import StringIO
from urllib.error import URLError
from urllib.request import urlopen

import pandas as pd

_SP500_CSV_URLS = (
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv",
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv",
)
_SP500_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def list_universe(name: str = "sp500") -> pd.DataFrame:
    """
    Return current members of a named universe.

    ``sp500`` is the live S&P 500 list (Yahoo-style symbols, e.g. ``BRK-B``).
    """
    key = str(name).strip().lower()
    if key in {"sp500", "s&p500", "s&p 500"}:
        return sp500_constituents()
    raise ValueError(f"Unknown universe {name!r}. Use 'sp500' or pass tickers= explicitly.")


def sp500_constituents() -> pd.DataFrame:
    """Current S&P 500 members with Yahoo tickers and GICS sectors."""
    errors: list[str] = []
    for url in _SP500_CSV_URLS:
        try:
            frame = _read_csv_url(url)
            out = _normalize_sp500(frame)
            if not out.empty:
                return out
        except (OSError, URLError, ValueError) as exc:
            errors.append(f"{url}: {exc}")
    try:
        tables = pd.read_html(_SP500_WIKI_URL)
    except (OSError, URLError, ValueError, ImportError) as exc:
        errors.append(f"wikipedia: {exc}")
        tables = []
    for table in tables:
        try:
            out = _normalize_sp500(table)
        except ValueError:
            continue
        if not out.empty:
            return out
    raise ValueError(
        "Could not download the S&P 500 list. Pass tickers= explicitly. "
        + "; ".join(errors)
    )


def yahoo_symbol(symbol: str) -> str:
    """Map index-file tickers (BRK.B) onto yfinance symbols (BRK-B)."""
    return str(symbol).strip().upper().replace(".", "-")


def _read_csv_url(url: str, timeout: float = 30.0) -> pd.DataFrame:
    with urlopen(url, timeout=timeout) as response:  # noqa: S310 — fixed public CSV
        payload = response.read().decode("utf-8")
    return pd.read_csv(StringIO(payload))


def _normalize_sp500(frame: pd.DataFrame) -> pd.DataFrame:
    rename = {}
    for col in frame.columns:
        key = str(col).strip().lower()
        if key in {"symbol", "ticker"}:
            rename[col] = "symbol"
        elif key in {"security", "name"}:
            rename[col] = "name"
        elif ("gics" in key and "sub" in key and "industry" in key) or key in {
            "gics sub-industry",
            "sub-industry",
            "industry",
        }:
            rename[col] = "industry"
        elif ("gics" in key and "sector" in key) or key == "sector":
            rename[col] = "sector"
    out = frame.rename(columns=rename)
    if "symbol" not in out.columns:
        raise ValueError("universe table has no symbol column")
    out["symbol"] = out["symbol"].map(yahoo_symbol)
    if "sector" not in out.columns:
        out["sector"] = pd.NA
    if "name" not in out.columns:
        out["name"] = pd.NA
    if "industry" not in out.columns:
        out["industry"] = pd.NA
    out = out.dropna(subset=["symbol"])
    out = out[out["symbol"] != ""]
    out = out.drop_duplicates(subset=["symbol"], keep="first")
    return out[["symbol", "name", "sector", "industry"]].reset_index(drop=True)


def map_universe_activity(sector: object, industry: object = None) -> str:
    """Map GICS / Yahoo sector+industry labels onto the exclusion vocabulary."""
    from halalquant.providers._yfinance import _map_yahoo_activity

    return _map_yahoo_activity(str(sector or ""), str(industry or ""))
