"""Vectorized local SQL query engine backed by DuckDB."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

import duckdb
import pandas as pd

from halalquant.database._models import SCHEMA_SQL


class DuckDBDriver:
    """Thin DuckDB wrapper for local cache read/write."""

    def __init__(self, path: Optional[Union[str, Path]] = None) -> None:
        self.path = str(path) if path else ":memory:"
        self.con = duckdb.connect(self.path)
        self.init_schema()

    def init_schema(self) -> None:
        self.con.execute(SCHEMA_SQL)

    def write_prices(self, frame: pd.DataFrame) -> None:
        if frame.empty:
            return
        self.con.register("_prices_tmp", frame)
        self.con.execute(
            """
            INSERT OR REPLACE INTO prices
            SELECT symbol, date, open, high, low, close, volume, adj_close
            FROM _prices_tmp
            """
        )
        self.con.unregister("_prices_tmp")

    def read_prices(
        self,
        symbols: Optional[list[str]] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> pd.DataFrame:
        clauses: list[str] = []
        params: list[object] = []
        if symbols:
            placeholders = ", ".join(["?"] * len(symbols))
            clauses.append(f"symbol IN ({placeholders})")
            params.extend(symbols)
        if start:
            clauses.append("date >= ?")
            params.append(start)
        if end:
            clauses.append("date <= ?")
            params.append(end)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self.con.execute(
            f"SELECT * FROM prices {where} ORDER BY symbol, date",
            params,
        ).fetchdf()

    def write_balance_sheets(self, frame: pd.DataFrame) -> None:
        if frame.empty:
            return
        self.con.register("_bs_tmp", frame)
        self.con.execute(
            """
            INSERT OR REPLACE INTO balance_sheets
            SELECT
                symbol, report_date, filed_date, total_debt, short_term_debt,
                long_term_debt, cash_and_equiv, interest_bearing_securities,
                receivables, liquid_assets, market_cap, market_cap_24m
            FROM _bs_tmp
            """
        )
        self.con.unregister("_bs_tmp")

    def write_compliance(self, frame: pd.DataFrame) -> None:
        if frame.empty:
            return
        self.con.register("_flags_tmp", frame)
        self.con.execute(
            """
            INSERT OR REPLACE INTO compliance_flags
            SELECT
                symbol, as_of, is_compliant, debt_ratio, cash_ratio,
                receivables_ratio, standard, reason
            FROM _flags_tmp
            """
        )
        self.con.unregister("_flags_tmp")

    def close(self) -> None:
        self.con.close()
