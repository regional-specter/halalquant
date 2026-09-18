"""Database schemas for the local DuckDB + Parquet cache."""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS prices (
    symbol VARCHAR NOT NULL,
    date DATE NOT NULL,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume BIGINT,
    adj_close DOUBLE,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS balance_sheets (
    symbol VARCHAR NOT NULL,
    report_date DATE NOT NULL,
    filed_date DATE NOT NULL,
    total_debt DOUBLE,
    short_term_debt DOUBLE,
    long_term_debt DOUBLE,
    cash_and_equiv DOUBLE,
    interest_bearing_securities DOUBLE,
    receivables DOUBLE,
    liquid_assets DOUBLE,
    market_cap DOUBLE,
    market_cap_24m DOUBLE,
    shares_outstanding DOUBLE,
    PRIMARY KEY (symbol, report_date, filed_date)
);

CREATE TABLE IF NOT EXISTS income_statements (
    symbol VARCHAR NOT NULL,
    report_date DATE NOT NULL,
    filed_date DATE NOT NULL,
    total_revenue DOUBLE,
    interest_income DOUBLE,
    non_compliant_income DOUBLE,
    ebitda DOUBLE,
    operating_cash_flow DOUBLE,
    capital_expenditure DOUBLE,
    free_cash_flow DOUBLE,
    PRIMARY KEY (symbol, report_date, filed_date)
);

CREATE TABLE IF NOT EXISTS dividends (
    symbol VARCHAR NOT NULL,
    ex_date DATE NOT NULL,
    dividend DOUBLE,
    adj_dividend DOUBLE,
    record_date DATE,
    payment_date DATE,
    PRIMARY KEY (symbol, ex_date)
);

CREATE TABLE IF NOT EXISTS financial_metrics (
    symbol VARCHAR NOT NULL,
    as_of DATE NOT NULL,
    freq VARCHAR NOT NULL,
    report_date DATE,
    filed_date DATE,
    total_debt DOUBLE,
    cash_and_equiv DOUBLE,
    interest_bearing_securities DOUBLE,
    receivables DOUBLE,
    liquid_assets DOUBLE,
    market_cap DOUBLE,
    market_cap_24m DOUBLE,
    debt_ratio DOUBLE,
    cash_ratio DOUBLE,
    receivables_ratio DOUBLE,
    total_revenue DOUBLE,
    non_compliant_income DOUBLE,
    impure_ratio DOUBLE,
    ebitda DOUBLE,
    operating_cash_flow DOUBLE,
    capital_expenditure DOUBLE,
    free_cash_flow DOUBLE,
    PRIMARY KEY (symbol, as_of, freq, report_date)
);

CREATE TABLE IF NOT EXISTS compliance_flags (
    symbol VARCHAR NOT NULL,
    as_of DATE NOT NULL,
    is_compliant BOOLEAN NOT NULL,
    debt_ratio DOUBLE,
    cash_ratio DOUBLE,
    receivables_ratio DOUBLE,
    standard VARCHAR NOT NULL,
    reason VARCHAR,
    PRIMARY KEY (symbol, as_of, standard)
);

CREATE TABLE IF NOT EXISTS sector_map (
    symbol VARCHAR NOT NULL,
    sector VARCHAR,
    PRIMARY KEY (symbol)
);

CREATE TABLE IF NOT EXISTS universe_members (
    universe VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    sector VARCHAR,
    sector_allowed BOOLEAN NOT NULL,
    PRIMARY KEY (universe, symbol)
);

CREATE TABLE IF NOT EXISTS dataset_meta (
    key VARCHAR PRIMARY KEY,
    value VARCHAR
);
"""

# Existing on-disk caches created before shares_outstanding / extra tables.
MIGRATION_SQL = (
    "ALTER TABLE balance_sheets ADD COLUMN IF NOT EXISTS shares_outstanding DOUBLE",
)

PRICE_TABLE_COLUMNS = (
    "symbol",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "adj_close",
)

BALANCE_SHEET_TABLE_COLUMNS = (
    "symbol",
    "report_date",
    "filed_date",
    "total_debt",
    "short_term_debt",
    "long_term_debt",
    "cash_and_equiv",
    "interest_bearing_securities",
    "receivables",
    "liquid_assets",
    "market_cap",
    "market_cap_24m",
    "shares_outstanding",
)

INCOME_TABLE_COLUMNS = (
    "symbol",
    "report_date",
    "filed_date",
    "total_revenue",
    "interest_income",
    "non_compliant_income",
    "ebitda",
    "operating_cash_flow",
    "capital_expenditure",
    "free_cash_flow",
)

DIVIDEND_TABLE_COLUMNS = (
    "symbol",
    "ex_date",
    "dividend",
    "adj_dividend",
    "record_date",
    "payment_date",
)

METRICS_TABLE_COLUMNS = (
    "symbol",
    "as_of",
    "freq",
    "report_date",
    "filed_date",
    "total_debt",
    "cash_and_equiv",
    "interest_bearing_securities",
    "receivables",
    "liquid_assets",
    "market_cap",
    "market_cap_24m",
    "debt_ratio",
    "cash_ratio",
    "receivables_ratio",
    "total_revenue",
    "non_compliant_income",
    "impure_ratio",
    "ebitda",
    "operating_cash_flow",
    "capital_expenditure",
    "free_cash_flow",
)

COMPLIANCE_TABLE_COLUMNS = (
    "symbol",
    "as_of",
    "is_compliant",
    "debt_ratio",
    "cash_ratio",
    "receivables_ratio",
    "standard",
    "reason",
)

ALLOWED_TABLES = frozenset(
    {
        "prices",
        "balance_sheets",
        "income_statements",
        "dividends",
        "financial_metrics",
        "compliance_flags",
        "sector_map",
        "universe_members",
        "dataset_meta",
    }
)
