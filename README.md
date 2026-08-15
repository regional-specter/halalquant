<div align="center">

# Shariah-Compliant Quant Data Engine

[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Contributions Welcome](https://img.shields.io/badge/Contributions-Welcome-brightgreen)](https://github.com/your-username/halalquant/issues)

</div>


`halalquant` is a Python library for **Shariah-compliant quantitative strategies**. It sits on top of public market data (yfinance) and filings (SEC EDGAR for US issuers/ADRs, Yahoo annual statements otherwise), then applies AAOIFI / DJIM screening, sector exclusions, and dividend purification. You get strategy-ready pandas DataFrames through a yfinance-shaped API — no paid vendor key required.

The library is the **ingestion + compliance layer**: fetch prices, screen a universe, purify dividends, then feed clean frames into your own backtest or execution stack.

---

## From Market Data to Strategy-Ready Signals

```text
[ yfinance ]  prices, dividends, sector, non-US annual statements
[ SEC EDGAR ]  US (and ADR) balance sheets & income (point-in-time filings)
        │
        ▼
[ Shariah Screening Engine ]  AAOIFI / DJIM + sector filters
        │
        ▼
[ Purification Engine ]  impure income × dividend
        │
        ▼
[ Unified Strategy API ]
  hq.download()  ·  hq.get_halal_universe()  ·  hq.purify_dividends()
  hq.compare_standards()  ·  hq.get_financial_metrics()
```

---

## Table of Contents

* [Who this is for](#who-this-is-for)
* [Prerequisites](#prerequisites)
* [Setup & Installation](#setup--installation)
* [Worked example](#worked-example)
* [Full function guide](USAGE.md)
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

* **If you are a student of Islamic finance / quant:** Start with [USAGE.md](USAGE.md) to see each function’s output, then read this README top to bottom for the screening math.
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

No API key is required. Prices and dividends come from yfinance. US (and ADR) fundamentals come from the public SEC EDGAR companyfacts API; other issuers use Yahoo annual statements.

```python
import halalquant as hq

prices = hq.download("AAPL", start="2024-01-01", end="2024-06-01")
universe = hq.get_halal_universe(["AAPL", "MSFT"], standard="aaoifi")
purified = hq.purify_dividends("AAPL", start="2024-01-01", end="2024-12-31")
comparison = hq.compare_standards(["AAPL", "MSFT"])
metrics = hq.get_financial_metrics("AAPL", start="2020-01-01", end="2024-12-31")
```

What each call is for, with captured DataFrames, is in **[USAGE.md](USAGE.md)**. Re-generate the snapshots with `python examples/walkthrough.py`.

---

## Worked example

`download` returns a long OHLCV frame (captured 15 Aug 2026):

```python
hq.download("AAPL", start="2024-01-02", end="2024-01-08")
```

```text
symbol       date     open     high      low    close   volume  adj_close
  AAPL 2024-01-02 187.1500 188.4400 183.8900 185.6400 82488700   183.4040
  AAPL 2024-01-03 184.2200 185.8800 183.4300 184.2500 58414500   182.0308
  AAPL 2024-01-04 182.1500 183.0900 180.8800 181.9100 71983600   179.7189
  AAPL 2024-01-05 181.9900 182.7600 180.1700 181.1800 62379700   178.9977
```

`get_halal_universe` keeps names that pass AAOIFI and drops excluded sectors. `JPM` is not in the result because conventional banking is filtered before ratios run:

```python
hq.get_halal_universe(["AAPL", "MSFT", "JPM"])
```

```text
symbol      as_of  is_compliant  debt_ratio  cash_ratio  receivables_ratio standard                          reason
  AAPL 2025-09-27          True      0.0280      0.0177             0.0306   aaoifi passes AAOIFI financial screens
  MSFT 2026-06-30          True      0.0095      0.0234             0.0481   aaoifi passes AAOIFI financial screens
```

| Function | Use it when you need… |
| --- | --- |
| `download()` | Price history for a backtest |
| `get_halal_universe()` | The names that pass AAOIFI or DJIM *now* |
| `compare_standards()` | Both verdicts on the same tickers (failures stay in the table) |
| `get_financial_metrics()` | Ratio history, or month/quarter snapshots with `freq="ME"` / `"QE"` |
| `purify_dividends()` | The share of each dividend to donate |

---

## Code Structure

```text
halalquant/
├── halalquant/                       # Core library package
│   ├── __init__.py                  # Exposes top-level data loaders
│   ├── api.py                       # download(), get_halal_universe(), purify_dividends(),
│   │                                # compare_standards(), get_financial_metrics()
│   ├── base.py                      # BaseDataProvider and BaseScreener interfaces
│   ├── providers/                   # Data adaptors
│   │   ├── _base_provider.py        # HTTP helper used by SEC
│   │   ├── _yfinance.py             # Prices, dividends, sector; non-US statements
│   │   ├── _sec_edgar.py            # US filings via SEC companyfacts
│   │   ├── _filings.py              # Router: SEC if CIK exists, else Yahoo
│   │   └── _fmp.py                  # Optional FMP adaptor (not used by the public API)
│   ├── screening/                   # Shariah filtering logic
│   │   ├── _aaoifi.py               # AAOIFI compliance engine (debt & liquidity math)
│   │   ├── _djim.py                 # Dow Jones Islamic Market compliance rules
│   │   ├── _compare.py              # Side-by-side AAOIFI vs DJIM helper
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
├── examples/
│   └── walkthrough.py               # Prints live DataFrames for USAGE.md
├── tests/
│   ├── test_api.py                  # Live download / universe / compare / metrics / purify
│   ├── test_aaoifi_screening.py     # AAOIFI threshold math
│   ├── test_djim_screening.py       # DJIM 33% thresholds + AAOIFI comparison math
│   ├── test_purification.py         # Purification formula
│   ├── test_pit_data.py             # Look-ahead bias prevention helpers
│   └── test_sec_edgar.py            # Live SEC companyfacts mapping
├── USAGE.md                         # Function-by-function guide with captured output
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
.download() / .get_halal_universe() / .purify_dividends()
.compare_standards() / .get_financial_metrics()
        │
        ▼
Returns OHLCV, compliance metrics, or purification amounts
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

Public usage:

```python
import halalquant as hq

prices = hq.download("AAPL", start="2024-01-01", end="2024-06-01")
universe = hq.get_halal_universe(["AAPL", "MSFT"], standard="aaoifi")
purified = hq.purify_dividends("AAPL", start="2024-01-01", end="2024-12-31")
comparison = hq.compare_standards(["AAPL", "MSFT"])
metrics = hq.get_financial_metrics("AAPL", start="2020-01-01", end="2024-12-31")
```

Annotated output for every call is in [USAGE.md](USAGE.md).

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

Trailing market cap is `shares outstanding ×` yfinance close prices (spot and 24-month average).

---

## Step 3: Sector Filters & Alternate Standards

Before financial ratios run, business-activity screens exclude clearly non-compliant sectors (alcohol, gambling, conventional banking, weapons, etc.). Yahoo sector/industry labels are mapped onto that vocabulary (for example, “Banks - Diversified” → `conventional banking`).

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

**DJIM** (Dow Jones Islamic Market) uses a parallel rule set with typical ~33% thresholds. Switch standards from the strategy API, or compare both at once:

```python
universe = hq.get_halal_universe(["AAPL", "MSFT"], standard="djim")
side_by_side = hq.compare_standards(["AAPL", "MSFT"])
# columns: debt_ratio, aaoifi_compliant, djim_compliant, agreement, ...
```

For research notebooks, pull screening ratios over a date range (one row per annual filing, or calendar snapshots with `freq="ME"` / `"QE"`):

```python
metrics = hq.get_financial_metrics("AAPL", start="2020-01-01", end="2024-12-31")
# debt_ratio, cash_ratio, receivables_ratio, impure_ratio, ...
monthly = hq.get_financial_metrics("AAPL", start="2023-01-01", end="2023-12-31", freq="ME")
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

Or fetch live dividends (yfinance) and income (SEC) in one call:

```python
import halalquant as hq

purified = hq.purify_dividends("AAPL", start="2024-01-01", end="2024-12-31")
# columns include dividend, impure_ratio, purification_amount
```

Interest income is used as a conservative proxy for non-compliant income when a finer breakdown is not in the filing.

---

## Step 5: Point-In-Time Data

`download()`, `get_halal_universe()`, `compare_standards()`, `get_financial_metrics()`, and `purify_dividends()` fetch on demand and return pandas DataFrames. Nothing is written to a local database.

Point-in-time helpers ensure you never use a filing that was not yet public on the decision date. SEC `filed_date` is the as-of cutoff. Yahoo statements have no filing date, so non-US rows use `report_date + 90 days`.

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

We use `pytest` against the real public API: yfinance prices, SEC EDGAR filings, and Yahoo annual statements for non-US tickers. Screening-threshold math is checked with small numeric examples so 30% vs 33% cannot drift.

Run the full test suite (needs network):

```bash
pytest tests/
python examples/walkthrough.py   # live DataFrames for USAGE.md
```

Current coverage includes:

| Test file | What it verifies |
| --- | --- |
| `test_api.py` | Live `download` / `get_halal_universe` / `compare_standards` / metrics / purification / non-US Yahoo statements |
| `test_sec_edgar.py` | Live SEC mapping for AAPL, JPM, MET (including original 10-K filed dates) |
| `test_aaoifi_screening.py` | Debt / cash threshold pass-fail math |
| `test_djim_screening.py` | DJIM 33% thresholds and AAOIFI vs DJIM disagreement |
| `test_purification.py` | Impure ratio and purification amount |
| `test_pit_data.py` | No look-ahead on filings or prices |

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
- [x] `download()` (yfinance-backed)
- [x] `get_halal_universe()`
- [x] `purify_dividends()`
- [x] `compare_standards()`
- [x] `get_financial_metrics()`

### Providers

- [x] yfinance prices, dividends, and sector map
- [x] SEC EDGAR companyfacts for US balance sheets and income
- [x] Trailing 24-month market cap from prices × shares outstanding
- [x] Non-US annual statements via Yahoo when no SEC CIK exists

### Screening

- [x] Sector / business-activity exclusion list
- [x] Yahoo sector/industry → exclusion labels
- [x] AAOIFI debt / cash / receivables ratios
- [x] DJIM rule-set wrapper
- [x] Side-by-side AAOIFI vs DJIM comparison helpers

### Purification

- [x] Impure income ratio
- [x] Dividend purification amount
- [x] Frame-level `purify_frame()` helper
- [x] Public `purify_dividends()` fetch path

### Storage & PIT

- [x] Point-in-time filing filter
- [x] Point-in-time price cutoff
- [x] Bank / insurer us-gaap tags (deposits, loans, cash due from banks)
- [x] Original 10-K filed date kept when later 10-Ks restate comparatives

### Tests

- [x] AAOIFI screening
- [x] Purification math
- [x] PIT look-ahead guards
- [x] Public API tests against live yfinance + SEC
- [x] SEC mapping tests (AAPL / JPM / MET)
- [x] DJIM threshold math

### Planned

- [x] First tagged `v0.1.0` release

---

## What's Next

`v0.1.0` is tagged. Non-US issuers use Yahoo annual statements (90-day publication lag instead of a true `filed_date`). SEC remains the PIT source for US CIKs.

Track the plain-English checklist in [`main.todo`](main.todo).

---
