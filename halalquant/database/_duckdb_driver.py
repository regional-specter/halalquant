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
        prepared = frame.copy()
        prepared["date"] = pd.to_datetime(prepared["date"]).dt.date
        self.con.register("_prices_tmp", prepared)
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
        prepared = frame.copy()
        for col in ("report_date", "filed_date"):
            if col in prepared.columns:
                prepared[col] = pd.to_datetime(prepared[col]).dt.date
        self.con.register("_bs_tmp", prepared)
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

    def read_balance_sheets(
        self,
        symbols: Optional[list[str]] = None,
    ) -> pd.DataFrame:
        clauses: list[str] = []
        params: list[object] = []
        if symbols:
            placeholders = ", ".join(["?"] * len(symbols))
            clauses.append(f"symbol IN ({placeholders})")
            params.extend(symbols)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self.con.execute(
            f"SELECT * FROM balance_sheets {where} ORDER BY symbol, report_date",
            params,
        ).fetchdf()

    def write_compliance(self, frame: pd.DataFrame) -> None:
        if frame.empty:
            return
        prepared = frame.copy()
        if "as_of" in prepared.columns:
            prepared["as_of"] = pd.to_datetime(prepared["as_of"]).dt.date
        self.con.register("_flags_tmp", prepared)
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

    def read_compliance(
        self,
        symbols: Optional[list[str]] = None,
        standard: Optional[str] = None,
    ) -> pd.DataFrame:
        clauses: list[str] = []
        params: list[object] = []
        if symbols:
            placeholders = ", ".join(["?"] * len(symbols))
            clauses.append(f"symbol IN ({placeholders})")
            params.extend(symbols)
        if standard:
            clauses.append("standard = ?")
            params.append(standard)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self.con.execute(
            f"SELECT * FROM compliance_flags {where} ORDER BY symbol, as_of",
            params,
        ).fetchdf()

    def read_table(self, table: str) -> pd.DataFrame:
        allowed = {"prices", "balance_sheets", "compliance_flags"}
        if table not in allowed:
            raise ValueError(f"Unknown table: {table}. Allowed: {sorted(allowed)}")
        return self.con.execute(f"SELECT * FROM {table}").fetchdf()

    def close(self) -> None:
        self.con.close()
