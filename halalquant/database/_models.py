"""Database schemas for prices, balance sheets, and compliance flags."""

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
    PRIMARY KEY (symbol, report_date, filed_date)
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
"""
