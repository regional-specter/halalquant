<div align="center">

# Shariah-Compliant Quant Data Engine

[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Contributions Welcome](https://img.shields.io/badge/Contributions-Welcome-brightgreen)](https://github.com/your-username/halalquant/issues)

</div>


I implemented an end-to-end data abstraction layer for **Shariah-compliant quantitative strategies**. You can use this library to fetch market data, compute point-in-time financial metrics, enforce strict Shariah compliance screening, calculate purification ratios, and serve clean data to backtesting and execution engines via a unified Python API.

This project goes beyond simple API calls to build a full, installable Python package—from vectorized balance sheet ratios to automated compliance audit trails.

---

## From Raw Data Vendors to Strategy-Ready Signals

Here is the data pipeline architecture, end to end:

```text
[ Raw Vendor Data / SEC EDGAR ]
        │
        ▼
[ Shariah Screening Engine ]
        │
        ▼
[ Purification Engine ]
        │
        ▼
[ Unified Strategy API ]  ≈  yfinance-style download() + get_halal_universe()
```

---

## Table of Contents

* [Who this is for](#who-this-is-for)
* [Prerequisites](#prerequisites)
* [Setup & Installation](#setup--installation)
* [Code Structure](#code-structure)
* [Step 1: The Unified Data API](#step-1-the-unified-data-api)
* [Step 2: Financial Ratio Screening (The Math)](#step-2-financial-ratio-screening-the-math)
* [Step 3: Sector Filters & Alternate Standards](#step-3-sector-filters--alternate-standards)
* [Step 4: Purification Engine](#step-4-purification-engine)
* [Step 5: Point-In-Time Data](#step-5-point-in-time-data)
* [Step 6: Verification & Testing](#step-6-verification--testing)
* [Progress](#progress)
* [What's Next](#whats-next)

---

## Who this is for

* **If you are a student of Islamic finance / quant:** Read top to bottom. Each section starts with the screening formula, then shows the vectorized Python implementation.
* **If you are a developer:** The repo is an editable Python package. Clone it, inspect the modules, run `pytest`, and `import halalquant` into your own strategies the same way you would use `yfinance`.
* **If you are building a halal backtest stack:** This library is the ingestion + compliance layer—screen the universe, purify dividends, then feed clean frames into your execution or research engine.

---

## Prerequisites

You need a basic understanding of Python Object-Oriented Programming (OOP), pandas DataFrames, and introductory financial statement literacy (debt, cash, receivables, market cap).

| Topic | Focus Area | Recommended Resource |
| --- | --- | --- |
| **Python OOP** | Classes, Inheritance, Abstract Base Classes | [Python OOP Tutorial](https://docs.python.org/3/tutorial/classes.html) |
| **pandas / NumPy** | Vectorized ratios, boolean masks, DataFrame schemas | [pandas User Guide](https://pandas.pydata.org/docs/user_guide/index.html) |
| **Shariah screening** | AAOIFI / DJIM financial thresholds | [AAOIFI Standards overview](https://aaoifi.com/) |
| **Point-in-time data** | Filing dates vs report dates (no look-ahead) | [Quantopian / Zipline PIT concepts](https://www.quantopian.com/) |

---

## Setup & Installation

Clone the repository and install it in editable mode (`-e`). This places `halalquant` on your Python import path so you can import it from anywhere on your system.

```bash
git clone https://github.com/your-username/halalquant.git
cd halalquant
pip install -e .
```

To run the automated test suite, install the dev extras:

```bash
pip install -e ".[dev]"
```

Set a vendor API key in your script (recommended). The env var still works as a fallback:

```python
import halalquant as hq

hq.api_key = "your_key_here"
```

```bash
export FMP_API_KEY="your_key_here"
```

---

## Code Structure

```text
halalquant/
├── halalquant/                       # Core library package
│   ├── __init__.py                  # Exposes top-level data loaders
│   ├── api.py                       # download() and get_halal_universe()
│   ├── base.py                      # BaseDataProvider and BaseScreener interfaces
│   ├── providers/                   # Vendor data adaptors (FMP, SEC)
│   │   ├── _base_provider.py        # Abstract fetcher class
│   │   ├── _fmp.py                  # Financial Modeling Prep wrapper
│   │   └── _sec_edgar.py            # SEC EDGAR parser for raw XBRL files
│   ├── screening/                   # Shariah filtering logic
│   │   ├── _aaoifi.py               # AAOIFI compliance engine (debt & liquidity math)
│   │   ├── _djim.py                 # Dow Jones Islamic Market compliance rules
│   │   └── _sector_filter.py        # Sector / business activity exclusion matrix
│   ├── purification/                # Dividend purification utilities
│   │   └── _purifier.py             # Impure income ratio calculators
│   ├── database/                    # Optional storage helpers (not used by download())
│   │   ├── _cache.py                # Cache-before-fetch + Parquet mirrors
│   │   ├── _duckdb_driver.py        # Vectorized local SQL query engine
│   │   └── _models.py               # Database schemas (prices, balance sheets, flags)
│   └── utils/                       # Shared helpers
│       ├── _pit_adjustments.py      # Point-In-Time restatement logic (no look-ahead bias)
│       └── validation.py            # Symbol and date range validators
├── tests/
│   ├── test_aaoifi_screening.py     # Unit tests verifying financial ratio thresholds
│   ├── test_purification.py         # Tests for dividend purification math
│   ├── test_pit_data.py             # Look-ahead bias prevention tests
│   └── test_providers.py            # API / provider stub tests
├── main.todo
├── pyproject.toml
└── README.md
```

---

## Step 1: The Unified Data API

Every data provider in `halalquant` adheres to a strict Object-Oriented interface enforced by `BaseDataProvider`. Strategy engines only interact with standard Python data structures (`pandas` DataFrames) containing normalized headers.

```text
Data Request (Symbol, Date Range)
        │
        ▼
.get_halal_universe() / .download()
        │
        ▼
Returns Compliant Tickers & Metrics  (or OHLCV frames)
```

```python
# halalquant/base.py
from abc import ABC, abstractmethod
import pandas as pd

class BaseDataProvider(ABC):
    """Strict interface every vendor adaptor must implement."""

    @abstractmethod
    def get_prices(self, symbols, start, end) -> pd.DataFrame:
        ...

    @abstractmethod
    def get_balance_sheet(self, symbols, as_of=None) -> pd.DataFrame:
        ...

    @abstractmethod
    def get_income_statement(self, symbols, as_of=None) -> pd.DataFrame:
        ...


class BaseScreener(ABC):
    """Strict interface every Shariah screening engine must implement."""

    @abstractmethod
    def evaluate_compliance(self, fundamentals: pd.DataFrame) -> pd.DataFrame:
        ...
```

Public usage mirrors a simple `yfinance`-style workflow:

```python
import halalquant as hq

hq.api_key = "your_key_here"

prices = hq.download("AAPL", start="2024-01-01", end="2024-06-01")
universe = hq.get_halal_universe(["AAPL", "MSFT"], standard="aaoifi")
```

---

## Step 2: Financial Ratio Screening (The Math)

The core Shariah screening logic evaluates financial ratios against standard thresholds (e.g., AAOIFI guidelines). Financial ratios must be computed dynamically using point-in-time financial statements and trailing market cap values.

### The Mathematics

To qualify as Shariah-compliant under AAOIFI rules, a company must pass three core financial thresholds (computed against the 24-month market capitalization average $MC_{24m}$):

1. **Total Debt Ratio:**

$$\text{Ratio}_{\text{Debt}} = \frac{\text{Short-Term Debt} + \text{Long-Term Debt}}{MC_{24m}} < 0.30$$

2. **Interest-Bearing Securities Ratio:**

$$\text{Ratio}_{\text{Cash}} = \frac{\text{Cash} + \text{Interest-Bearing Deposits}}{MC_{24m}} < 0.30$$

3. **Receivables & Liquid Assets Ratio:**

$$\text{Ratio}_{\text{Receivables}} = \frac{\text{Accounts Receivable} + \text{Liquid Assets}}{MC_{24m}} < 0.70$$

### Vectorized Implementation

```python
# halalquant/screening/_aaoifi.py
import numpy as np
from halalquant.base import BaseScreener

class AAOIFIScreener(BaseScreener):
    def __init__(self, debt_threshold=0.30, cash_threshold=0.30, receivables_threshold=0.70):
        self.debt_threshold = debt_threshold
        self.cash_threshold = cash_threshold
        self.receivables_threshold = receivables_threshold

    def evaluate_arrays(
        self,
        total_debt: np.ndarray,
        cash_and_equiv: np.ndarray,
        market_cap_24m: np.ndarray,
        receivables_and_liquid: np.ndarray | None = None,
    ) -> np.ndarray:
        """
        Evaluates compliance across vectorized array inputs.
        Returns a boolean array where True indicates a compliant asset.
        """
        with np.errstate(divide="ignore", invalid="ignore"):
            debt_ratio = total_debt / market_cap_24m
            cash_ratio = cash_and_equiv / market_cap_24m
            compliant = (debt_ratio < self.debt_threshold) & (
                cash_ratio < self.cash_threshold
            )
            if receivables_and_liquid is not None:
                recv_ratio = receivables_and_liquid / market_cap_24m
                compliant = compliant & (recv_ratio < self.receivables_threshold)
            compliant = compliant & np.isfinite(market_cap_24m) & (market_cap_24m > 0)
        return compliant
```

`evaluate_compliance()` wraps the same math over a fundamentals DataFrame and returns audit-friendly columns: `is_compliant`, `debt_ratio`, `cash_ratio`, `receivables_ratio`, and `reason`.

---

## Step 3: Sector Filters & Alternate Standards

Before financial ratios run, business-activity screens exclude clearly non-compliant sectors (alcohol, gambling, conventional banking, weapons, etc.).

```python
# halalquant/screening/_sector_filter.py
from halalquant.screening import SectorFilter

sector_filter = SectorFilter()
kept = sector_filter.filter_symbols(
    ["AAPL", "JPM"],
    sector_map={"AAPL": "technology", "JPM": "conventional banking"},
)
# kept == ["AAPL"]
# sector_filter.audit_log records why JPM was removed
```

**DJIM** (Dow Jones Islamic Market) uses a parallel rule set with typical ~33% thresholds. Switch standards from the strategy API:

```python
universe = hq.get_halal_universe(["AAPL", "MSFT"], standard="djim")
```

---

## Step 4: Purification Engine

Even compliant equities may earn a small share of impure income. Purification estimates how much of a dividend should be donated.

### The Mathematics

$$\text{Impure Ratio} = \frac{\text{Non-Compliant Income}}{\text{Total Revenue}}$$

$$\text{Purification Amount} = \text{Dividend} \times \text{Impure Ratio}$$

```python
# halalquant/purification/_purifier.py
from halalquant.purification import Purifier

purifier = Purifier()
ratio = purifier.impure_income_ratio(non_compliant_income=5.0, total_revenue=100.0)
amount = purifier.purification_amount(
    dividend=2.0,
    non_compliant_income=5.0,
    total_revenue=100.0,
)
# ratio == 0.05, amount == 0.10
```

---

## Step 5: Point-In-Time Data

`download()` and `get_halal_universe()` fetch from the vendor and return pandas DataFrames. Nothing is written to a local database.

Point-in-time helpers ensure you never use a filing that was not yet public on the decision date.

```python
# halalquant/utils/_pit_adjustments.py
from halalquant.utils._pit_adjustments import as_of_filter, prevent_lookahead_prices

# Keep only filings with filed_date <= as_of, then the latest report per symbol
known = as_of_filter(balance_sheets, as_of="2024-09-01")

# Drop any price bars after the decision date
prices_pit = prevent_lookahead_prices(prices, as_of="2024-01-05")
```

This is the difference between a toy screener and a backtest-safe compliance engine.

---

## Step 6: Verification & Testing

We use `pytest` to lock the screening math, purification formulas, PIT guards, and live FMP mocks.

Run the full test suite:

```bash
pytest tests/
```

Current coverage includes:

| Test file | What it verifies |
| --- | --- |
| `test_aaoifi_screening.py` | Debt / cash threshold pass-fail masks |
| `test_purification.py` | Impure ratio and purification amount |
| `test_pit_data.py` | No look-ahead on filings or prices |
| `test_providers.py` | Live FMP mocks and yfinance-style `download()` / `get_halal_universe()` |

### Example Test Case (`tests/test_aaoifi_screening.py`)

```python
import numpy as np
from halalquant.screening._aaoifi import AAOIFIScreener

def test_evaluate_arrays_pass_and_fail():
    screener = AAOIFIScreener(debt_threshold=0.30, cash_threshold=0.30)

    total_debt = np.array([10.0, 40.0])
    cash = np.array([5.0, 5.0])
    mc = np.array([100.0, 100.0])

    result = screener.evaluate_arrays(total_debt, cash, mc)
    assert bool(result[0]) is True   # 10/100 < 0.30
    assert bool(result[1]) is False  # 40/100 >= 0.30
```

---

## Progress

### Core

- [x] BaseDataProvider
- [x] BaseScreener
- [x] Canonical price / balance-sheet / compliance schemas
- [x] Symbol and date-range validation
- [x] `download()` (yfinance-like surface)
- [x] `get_halal_universe()`

### Providers

- [x] AbstractFetcher base
- [x] FMP provider (live stable API)
- [x] SEC EDGAR provider stub
- [x] Live FMP price + balance-sheet + income fetch
- [x] Live FMP market-cap + 24-month trailing average
- [ ] Live SEC XBRL mapping
- [x] Normalize FMP provider output to one schema end-to-end

### Screening

- [x] Sector / business-activity exclusion list
- [x] Sector audit log
- [x] AAOIFI debt / cash / receivables ratios
- [x] DJIM rule-set wrapper
- [x] Compute 24-month average market cap (FMP historical market cap)
- [ ] Side-by-side AAOIFI vs DJIM comparison helpers

### Purification

- [x] Impure income ratio
- [x] Dividend purification amount
- [x] Frame-level `purify_frame()` helper

### Storage & PIT

- [x] Point-in-time filing filter
- [x] Point-in-time price cutoff
- [ ] Optional local cache (not used by the public API)

### Tests

- [x] AAOIFI screening
- [x] Purification math
- [x] PIT look-ahead guards
- [x] Provider / API fetch tests
- [ ] DJIM unit tests
- [x] Live provider mock tests (`responses`)

### Planned

- [x] Wire live FMP endpoints
- [x] 24-month trailing market-cap calculator
- [ ] Financial-metric helpers over a date range
- [ ] Live SEC EDGAR companyfacts mapping
- [ ] First tagged `0.1.0` release after a smoke test with a real API key

---

## What's Next

Live FMP fetch is in place (`download()` / `get_halal_universe()` return DataFrames, nothing is cached). Remaining work:

1. **SEC EDGAR** — map companyfacts XBRL tags into the shared balance-sheet schema
2. **Metric helpers** — financial ratios over a date range for research notebooks
3. **Smoke test + tag** — run `download` / `get_halal_universe` with a real `FMP_API_KEY`, then tag `0.1.0`

Track the plain-English checklist in [`main.todo`](main.todo).

---
