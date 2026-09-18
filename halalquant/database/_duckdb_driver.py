"""Vectorized local SQL query engine backed by DuckDB."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence, Union

import pandas as pd

from halalquant.database._models import (
    ALLOWED_TABLES,
    BALANCE_SHEET_TABLE_COLUMNS,
    COMPLIANCE_TABLE_COLUMNS,
    DIVIDEND_TABLE_COLUMNS,
    INCOME_TABLE_COLUMNS,
    METRICS_TABLE_COLUMNS,
    MIGRATION_SQL,
    PRICE_TABLE_COLUMNS,
    SCHEMA_SQL,
)

try:
    import duckdb
except ImportError as exc:  # pragma: no cover
    duckdb = None  # type: ignore[assignment]
    _DUCKDB_IMPORT_ERROR: Optional[BaseException] = exc
else:
    _DUCKDB_IMPORT_ERROR = None

_CACHE_EXTRA_HINT = "Optional cache extras are missing. Install with: pip install 'halalquant[cache]'"


class DuckDBDriver:
    """Thin DuckDB wrapper for local cache read/write."""

    def __init__(self, path: Optional[Union[str, Path]] = None) -> None:
        if duckdb is None:
            raise ImportError(_CACHE_EXTRA_HINT) from _DUCKDB_IMPORT_ERROR
        self.path = str(path) if path else ":memory:"
        self.con = duckdb.connect(self.path)
        self.init_schema()

    def init_schema(self) -> None:
        self.con.execute(SCHEMA_SQL)
        for statement in MIGRATION_SQL:
            self.con.execute(statement)
        self._ensure_metrics_primary_key()

    def _ensure_metrics_primary_key(self) -> None:
        """Keep restated 10-K comparatives: PK must include report_date."""
        rows = self.con.execute(
            """
            SELECT constraint_column_names
            FROM duckdb_constraints()
            WHERE table_name = 'financial_metrics' AND constraint_type = 'PRIMARY KEY'
            """
        ).fetchall()
        cols: list[str] = []
        if rows:
            raw = rows[0][0]
            if isinstance(raw, (list, tuple)):
                cols = [str(c).lower() for c in raw]
            else:
                cols = [part.strip().lower() for part in str(raw).strip("[]").split(",")]
        if "report_date" in cols:
            return
        self.con.execute("DROP TABLE IF EXISTS financial_metrics")
        self.con.execute(SCHEMA_SQL)

    def write_prices(self, frame: pd.DataFrame) -> None:
        self._insert("prices", frame, PRICE_TABLE_COLUMNS, date_cols=("date",))

    def read_prices(
        self,
        symbols: Optional[list[str]] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> pd.DataFrame:
        clauses: list[str] = []
        params: list[object] = []
        if symbols:
            clauses.append(self._in("symbol", symbols, params))
        if start:
            clauses.append("date >= ?")
            params.append(start)
        if end:
            clauses.append("date <= ?")
            params.append(end)
        return self._select("prices", clauses, params, "symbol, date")

    def price_coverage(self, symbols: Sequence[str]) -> pd.DataFrame:
        if not symbols:
            return pd.DataFrame(columns=["symbol", "min_date", "max_date", "n_rows"])
        params: list[object] = list(symbols)
        placeholders = ", ".join(["?"] * len(symbols))
        return self.con.execute(
            f"""
            SELECT symbol, MIN(date) AS min_date, MAX(date) AS max_date, COUNT(*) AS n_rows
            FROM prices
            WHERE symbol IN ({placeholders})
            GROUP BY symbol
            """,
            params,
        ).fetchdf()

    def write_balance_sheets(self, frame: pd.DataFrame) -> None:
        self._insert(
            "balance_sheets",
            frame,
            BALANCE_SHEET_TABLE_COLUMNS,
            date_cols=("report_date", "filed_date"),
        )

    def read_balance_sheets(
        self,
        symbols: Optional[list[str]] = None,
    ) -> pd.DataFrame:
        clauses: list[str] = []
        params: list[object] = []
        if symbols:
            clauses.append(self._in("symbol", symbols, params))
        return self._select("balance_sheets", clauses, params, "symbol, report_date, filed_date")

    def write_income_statements(self, frame: pd.DataFrame) -> None:
        self._insert(
            "income_statements",
            frame,
            INCOME_TABLE_COLUMNS,
            date_cols=("report_date", "filed_date"),
        )

    def read_income_statements(
        self,
        symbols: Optional[list[str]] = None,
    ) -> pd.DataFrame:
        clauses: list[str] = []
        params: list[object] = []
        if symbols:
            clauses.append(self._in("symbol", symbols, params))
        return self._select("income_statements", clauses, params, "symbol, report_date, filed_date")

    def write_dividends(self, frame: pd.DataFrame) -> None:
        self._insert(
            "dividends",
            frame,
            DIVIDEND_TABLE_COLUMNS,
            date_cols=("ex_date", "record_date", "payment_date"),
        )

    def read_dividends(
        self,
        symbols: Optional[list[str]] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> pd.DataFrame:
        clauses: list[str] = []
        params: list[object] = []
        if symbols:
            clauses.append(self._in("symbol", symbols, params))
        if start:
            clauses.append("ex_date >= ?")
            params.append(start)
        if end:
            clauses.append("ex_date <= ?")
            params.append(end)
        return self._select("dividends", clauses, params, "symbol, ex_date")

    def write_metrics(self, frame: pd.DataFrame) -> None:
        self._insert(
            "financial_metrics",
            frame,
            METRICS_TABLE_COLUMNS,
            date_cols=("as_of", "report_date", "filed_date"),
        )

    def read_metrics(
        self,
        symbols: Optional[list[str]] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        freq: Optional[str] = None,
    ) -> pd.DataFrame:
        clauses: list[str] = []
        params: list[object] = []
        if symbols:
            clauses.append(self._in("symbol", symbols, params))
        if freq:
            clauses.append("freq = ?")
            params.append(freq)
        date_col = "as_of" if freq and freq != "annual" else "report_date"
        if start:
            clauses.append(f"{date_col} >= ?")
            params.append(start)
        if end:
            clauses.append(f"{date_col} <= ?")
            params.append(end)
        return self._select("financial_metrics", clauses, params, "symbol, as_of, freq")

    def write_compliance(self, frame: pd.DataFrame) -> None:
        self._insert(
            "compliance_flags",
            frame,
            COMPLIANCE_TABLE_COLUMNS,
            date_cols=("as_of",),
        )

    def read_compliance(
        self,
        symbols: Optional[list[str]] = None,
        standard: Optional[str] = None,
    ) -> pd.DataFrame:
        clauses: list[str] = []
        params: list[object] = []
        if symbols:
            clauses.append(self._in("symbol", symbols, params))
        if standard:
            clauses.append("standard = ?")
            params.append(standard)
        return self._select("compliance_flags", clauses, params, "symbol, as_of")

    def write_sector_map(self, mapping: dict[str, str]) -> None:
        if not mapping:
            return
        frame = pd.DataFrame({"symbol": list(mapping.keys()), "sector": list(mapping.values())})
        self._insert("sector_map", frame, ("symbol", "sector"))

    def read_sector_map(self, symbols: Optional[list[str]] = None) -> dict[str, str]:
        clauses: list[str] = []
        params: list[object] = []
        if symbols:
            clauses.append(self._in("symbol", symbols, params))
        frame = self._select("sector_map", clauses, params, "symbol")
        if frame.empty:
            return {}
        return {
            str(row["symbol"]): str(row["sector"])
            for _, row in frame.iterrows()
            if pd.notna(row["sector"])
        }

    def write_universe(self, frame: pd.DataFrame) -> None:
        self._insert(
            "universe_members",
            frame,
            ("universe", "symbol", "sector", "sector_allowed"),
        )

    def read_universe(self, universe: Optional[str] = None) -> pd.DataFrame:
        clauses: list[str] = []
        params: list[object] = []
        if universe:
            clauses.append("universe = ?")
            params.append(universe)
        return self._select("universe_members", clauses, params, "universe, symbol")

    def write_meta(self, items: dict[str, str]) -> None:
        if not items:
            return
        frame = pd.DataFrame({"key": list(items.keys()), "value": [str(v) for v in items.values()]})
        self._insert("dataset_meta", frame, ("key", "value"))

    def read_meta(self) -> dict[str, str]:
        frame = self._select("dataset_meta", [], [], "key")
        if frame.empty:
            return {}
        return {str(row["key"]): str(row["value"]) for _, row in frame.iterrows()}

    def read_table(self, table: str) -> pd.DataFrame:
        if table not in ALLOWED_TABLES:
            raise ValueError(f"Unknown table: {table}. Allowed: {sorted(ALLOWED_TABLES)}")
        return self.con.execute(f"SELECT * FROM {table}").fetchdf()

    def close(self) -> None:
        self.con.close()

    def _insert(
        self,
        table: str,
        frame: pd.DataFrame,
        columns: Sequence[str],
        date_cols: Sequence[str] = (),
    ) -> None:
        if frame is None or frame.empty:
            return
        prepared = _align_frame(frame, columns, date_cols)
        alias = f"_{table}_tmp"
        self.con.register(alias, prepared)
        cols = ", ".join(columns)
        self.con.execute(f"INSERT OR REPLACE INTO {table} ({cols}) SELECT {cols} FROM {alias}")
        self.con.unregister(alias)

    def _select(
        self,
        table: str,
        clauses: list[str],
        params: list[object],
        order: str,
    ) -> pd.DataFrame:
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self.con.execute(
            f"SELECT * FROM {table} {where} ORDER BY {order}",
            params,
        ).fetchdf()

    @staticmethod
    def _in(column: str, values: Sequence[str], params: list[object]) -> str:
        placeholders = ", ".join(["?"] * len(values))
        params.extend(values)
        return f"{column} IN ({placeholders})"


def _align_frame(
    frame: pd.DataFrame,
    columns: Sequence[str],
    date_cols: Sequence[str],
) -> pd.DataFrame:
    prepared = frame.copy()
    for col in columns:
        if col not in prepared.columns:
            prepared[col] = pd.NA
    for col in date_cols:
        if col in prepared.columns:
            prepared[col] = pd.to_datetime(prepared[col], errors="coerce").dt.date
    return prepared.loc[:, list(columns)]
