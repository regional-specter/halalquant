"""Composite filings router: SEC EDGAR when a CIK exists, otherwise Yahoo."""

from __future__ import annotations

from typing import Optional, Sequence

import pandas as pd

from halalquant.base import BALANCE_SHEET_COLUMNS, DateLike, INCOME_COLUMNS, BaseDataProvider
from halalquant.providers._sec_edgar import SECEdgarProvider
from halalquant.providers._yfinance import YFinanceProvider


class FilingsProvider(BaseDataProvider):
    """
    Fundamentals router used by the public API.

    Tickers in the SEC company-tickers map (US issuers and many ADRs) use
    EDGAR companyfacts with true filing dates. All other symbols use Yahoo
    annual statements with a 90-day publication lag in place of filed_date.
    """

    def __init__(
        self,
        sec: Optional[SECEdgarProvider] = None,
        yahoo: Optional[YFinanceProvider] = None,
    ) -> None:
        self.sec = sec or SECEdgarProvider()
        self.yahoo = yahoo or YFinanceProvider()

    def get_prices(
        self,
        symbols: Sequence[str],
        start: DateLike,
        end: DateLike,
    ) -> pd.DataFrame:
        _ = (symbols, start, end)
        raise NotImplementedError(
            "FilingsProvider does not serve prices. Use YFinanceProvider for OHLCV."
        )

    def get_balance_sheet(
        self,
        symbols: Sequence[str],
        as_of: Optional[DateLike] = None,
    ) -> pd.DataFrame:
        return self._split_fetch("get_balance_sheet", symbols, as_of, extra_cols=["shares_outstanding"])

    def get_income_statement(
        self,
        symbols: Sequence[str],
        as_of: Optional[DateLike] = None,
    ) -> pd.DataFrame:
        return self._split_fetch("get_income_statement", symbols, as_of)

    def _split_fetch(
        self,
        method: str,
        symbols: Sequence[str],
        as_of: Optional[DateLike],
        extra_cols: Optional[list[str]] = None,
    ) -> pd.DataFrame:
        empty_cols = list(BALANCE_SHEET_COLUMNS if method == "get_balance_sheet" else INCOME_COLUMNS)
        if extra_cols:
            empty_cols = empty_cols + extra_cols
        sec_symbols: list[str] = []
        yahoo_symbols: list[str] = []
        for symbol in symbols:
            if self.sec.has_cik(symbol):
                sec_symbols.append(symbol)
            else:
                yahoo_symbols.append(symbol)
        frames: list[pd.DataFrame] = []
        if sec_symbols:
            frames.append(getattr(self.sec, method)(sec_symbols, as_of=as_of))
        if yahoo_symbols:
            frames.append(getattr(self.yahoo, method)(yahoo_symbols, as_of=as_of))
        frames = [frame for frame in frames if frame is not None and not frame.empty]
        if not frames:
            return pd.DataFrame(columns=empty_cols)
        return (
            pd.concat(frames, ignore_index=True)
            .sort_values(["symbol", "report_date"])
            .reset_index(drop=True)
        )
