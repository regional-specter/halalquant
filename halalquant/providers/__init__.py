"""Vendor data adaptors."""

from halalquant.providers._fmp import FMPProvider
from halalquant.providers._sec_edgar import SECEdgarProvider

__all__ = ["FMPProvider", "SECEdgarProvider"]
