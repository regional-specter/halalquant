"""Vendor data adaptors."""

from halalquant.providers._sec_edgar import SECEdgarProvider
from halalquant.providers._yfinance import YFinanceProvider

__all__ = ["SECEdgarProvider", "YFinanceProvider"]
