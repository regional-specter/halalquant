"""Sector and business-activity exclusion matrix."""

from __future__ import annotations

from typing import Iterable, Sequence

# Starter exclusion set. Expand with GICS / NAICS mappings later.
DEFAULT_EXCLUDED_SECTORS: frozenset[str] = frozenset(
    {
        "alcohol",
        "tobacco",
        "gambling",
        "pork",
        "weapons",
        "defense",
        "adult entertainment",
        "conventional banking",
        "conventional insurance",
        "interest-based finance",
    }
)


class SectorFilter:
    """Exclude tickers whose primary activity is non-compliant."""

    def __init__(self, excluded_sectors: Iterable[str] | None = None) -> None:
        self.excluded_sectors = {
            s.strip().lower() for s in (excluded_sectors or DEFAULT_EXCLUDED_SECTORS)
        }
        self.audit_log: list[dict[str, str]] = []

    def is_sector_allowed(self, sector: str) -> bool:
        return sector.strip().lower() not in self.excluded_sectors

    def filter_symbols(
        self,
        symbols: Sequence[str],
        sector_map: dict[str, str] | None = None,
    ) -> list[str]:
        """
        Return symbols that pass the sector screen.

        If no sector_map is provided, all symbols pass (no sector data yet).
        """
        self.audit_log = []
        if not sector_map:
            return list(symbols)

        kept: list[str] = []
        for symbol in symbols:
            sector = sector_map.get(symbol, "")
            if sector and not self.is_sector_allowed(sector):
                self.audit_log.append(
                    {
                        "symbol": symbol,
                        "sector": sector,
                        "reason": "excluded business activity",
                    }
                )
            else:
                kept.append(symbol)
        return kept
