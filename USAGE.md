# How to use `halalquant`

This is the function-by-function guide. The [README](README.md) explains the math and package layout; this file shows **when to call each public function** and what a real DataFrame looks like.

Snapshots were captured on 15 August 2026 against live yfinance and SEC EDGAR. Prices, ratios, and filing dates will move. Re-run the same snippets with `python examples/walkthrough.py`. For a color terminal table of the live screen, run `python -m halalquant` (needs `pip install "halalquant[examples]"`).

No API key is required.

---

## Which function do I call?

Typical research loop: fetch prices → drop haram sectors → keep names that pass AAOIFI (or DJIM) → purify dividends on the survivors.

| You want… | Call | What you get |
| --- | --- | --- |
| OHLCV bars for a backtest | `hq.download()` | One row per symbol per trading day |
| “Which of these names are halal *today*?” | `hq.get_halal_universe()` | Only names that pass the chosen standard |
| “Does AAOIFI and DJIM disagree?” | `hq.compare_standards()` | One row per name, both verdicts |
| Ratio history for a chart or screen | `hq.get_financial_metrics()` | One row per annual filing, or calendar snapshots with `freq=` |
| How much of a dividend to donate | `hq.purify_dividends()` | One row per ex-date with `purification_amount` |
| The purification formula only | `hq.Purifier()` | Scalars / arrays, no network |
| To plug in your own data vendor | `YFinanceProvider`, `SECEdgarProvider`, `FilingsProvider` | Same DataFrame schemas as the public API |

`get_halal_universe` **drops** failures (and, by default, banks/insurers). `compare_standards` **keeps** every name that still has fundamentals so you can see *why* it failed.

---

## 1. `download` — prices

Use this the same way you would use `yfinance.download`, except the result is always a **long** frame (`symbol`, `date`, OHLCV) even for one ticker.

```python
import halalquant as hq

prices = hq.download("AAPL", start="2024-01-02", end="2024-01-08")
```

```text
symbol       date     open     high      low    close   volume  adj_close
  AAPL 2024-01-02 187.1500 188.4400 183.8900 185.6400 82488700   183.4040
  AAPL 2024-01-03 184.2200 185.8800 183.4300 184.2500 58414500   182.0308
  AAPL 2024-01-04 182.1500 183.0900 180.8800 181.9100 71983600   179.7189
  AAPL 2024-01-05 181.9900 182.7600 180.1700 181.1800 62379700   178.9977
```

`end` is treated as a range end for yfinance, so 8 January is not always in the last row. Pass several tickers as a list; they stack in the same long format.

---

## 2. `get_halal_universe` — the live screen

This is the function you call to build a tradable set. It:

1. Maps Yahoo sector/industry labels onto the exclusion list (banks, insurers, alcohol, …).
2. Pulls the latest **already-public** annual balance sheet (SEC if the ticker has a CIK, otherwise Yahoo).
3. Fills market cap as `shares × close`, including a 24-month average.
4. Applies AAOIFI (default) or DJIM thresholds.

```python
universe = hq.get_halal_universe(
    ["AAPL", "MSFT", "JPM"],
    standard="aaoifi",          # or "djim"
    apply_sector_filter=True,   # default
)
```

```text
symbol      as_of  is_compliant  debt_ratio  cash_ratio  receivables_ratio standard                          reason
  AAPL 2025-09-27          True      0.0280      0.0177             0.0306   aaoifi passes AAOIFI financial screens
  MSFT 2026-06-30          True      0.0095      0.0234             0.0481   aaoifi passes AAOIFI financial screens
```

`JPM` is missing because conventional banking is excluded **before** ratios run. `as_of` is the fiscal period of the statement that was used, not “today”.

AAOIFI limits (vs 24-month average market cap): debt `< 30%`, cash + interest-bearing securities `< 30%`, receivables + liquid assets `< 70%`. DJIM uses ~33% on those same families of ratios.

To inspect a bank’s financials anyway:

```python
hq.get_halal_universe(["AAPL", "JPM"], standard="djim", apply_sector_filter=False)
```

```text
symbol  is_compliant  debt_ratio  cash_ratio  receivables_ratio standard                        reason
  AAPL          True      0.0280      0.0177             0.0306     djim passes DJIM financial screens
   JPM         False      4.6813      0.5254             2.7711     djim  debt ratio exceeds threshold
```

JPM’s debt ratio is several times market cap — the financial screen fails even when the sector filter is off.

---

## 3. `compare_standards` — AAOIFI vs DJIM on the same names

Use this when you are choosing a rule set, or when you want failures left in the table. Ratios are identical; only the pass/fail cutoffs change. `agreement` is `True` when both standards reach the same verdict.

Non-US tickers work here too. `NESN.SW` is not in the SEC ticker map, so statements come from Yahoo.

```python
compared = hq.compare_standards(["AAPL", "MSFT", "NESN.SW"])
```

```text
 symbol      as_of  debt_ratio  cash_ratio  receivables_ratio  aaoifi_compliant  djim_compliant  agreement
   AAPL 2025-09-27      0.0280      0.0177             0.0306              True            True       True
NESN.SW 2025-12-31      0.2645      0.0271             0.0671              True            True       True
   MSFT 2026-06-30      0.0095      0.0234             0.0481              True            True       True
```

Nestlé’s debt ratio (~26%) is close to the AAOIFI 30% line and still under DJIM’s 33%. That is the kind of name where `agreement` can flip after a recapitalization — worth watching in a research notebook.

---

## 4. `get_financial_metrics` — ratios over time

`get_halal_universe` is a **cross-section** (latest filing). This function is a **panel**.

Default: one row per annual report whose `report_date` falls in `[start, end]`, using only filings that were already public by `end`.

```python
metrics = hq.get_financial_metrics("AAPL", start="2021-01-01", end="2024-12-31")
```

```text
symbol      as_of report_date filed_date  debt_ratio  cash_ratio  receivables_ratio  impure_ratio
  AAPL 2021-10-29  2021-09-25 2021-10-29      0.0663      0.0361             0.0512        0.0078
  AAPL 2022-10-28  2022-09-24 2022-10-28      0.0472      0.0209             0.0332        0.0072
  AAPL 2023-11-03  2023-09-30 2023-11-03      0.0406      0.0247             0.0365        0.0098
  AAPL 2024-11-01  2024-09-28 2024-11-01      0.0356      0.0242             0.0367           NaN
```

Read `filed_date` as “this row became usable for a backtest on this date”. FY2024 `impure_ratio` is `NaN` because the interest-income XBRL tag was missing on that 10-K at capture time — purification then has nothing to scale by.

Pass `freq="ME"` or `freq="QE"` when you want a calendar snapshot (month-end / quarter-end) instead of one row per 10-K. October 2023 still sees FY2022, because Apple’s FY2023 10-K was not filed until 3 November:

```python
monthly = hq.get_financial_metrics(
    "AAPL", start="2023-10-01", end="2023-12-31", freq="ME",
)
```

```text
symbol      as_of report_date filed_date  debt_ratio  cash_ratio
  AAPL 2023-10-31  2022-09-24 2022-10-28      0.0423      0.0188
  AAPL 2023-11-30  2023-09-30 2023-11-03      0.0400      0.0243
  AAPL 2023-12-31  2023-09-30 2023-11-03      0.0398      0.0242
```

Debt and cash ratios drift a little across Nov/Dec because market cap (price × shares) changes even though the balance sheet is the same.

---

## 5. `purify_dividends` — cash you should not keep

Even a compliant equity can earn a slice of interest income. This function downloads dividends, attaches the latest **income statement that was already public on the ex-date**, and returns `dividend × (interest income / revenue)`.

```python
purified = hq.purify_dividends("AAPL", start="2024-01-01", end="2024-12-31")
```

```text
symbol    ex_date  dividend report_date  impure_ratio  purification_amount
  AAPL 2024-02-09    0.2400  2023-09-30        0.0098               0.0023
  AAPL 2024-05-10    0.2500  2023-09-30        0.0098               0.0024
  AAPL 2024-08-12    0.2500  2023-09-30        0.0098               0.0024
  AAPL 2024-11-08    0.2500  2024-09-28           NaN                  NaN
```

The May 2024 dividend is matched to FY2023 (`report_date` 2023-09-30), not FY2024, because the FY2024 10-K was not public yet. Donate `0.0024` per share on that `0.25` dividend (~1% of the cash). The November row is `NaN` for the same missing interest tag noted above.

If you already have the numbers, skip the network and use `Purifier` directly:

```python
from halalquant import Purifier

p = Purifier()
p.impure_income_ratio(5.0, 100.0)          # 0.05
p.purification_amount(2.0, 5.0, 100.0)     # 0.10
```

---

## 6. Providers — where the rows come from

You do not need these for the five functions above. Use them when you want raw statements, or to inject a fake provider in tests.

| Class | Role |
| --- | --- |
| `YFinanceProvider` | Prices, dividends, sector map, and non-US annual statements |
| `SECEdgarProvider` | US / ADR companyfacts (true `filed_date`) |
| `FilingsProvider` | Router: exact ticker in the SEC map → EDGAR, else Yahoo |

Ticker matching is exact. `SAP.DE` is not treated as `SAP`, so a German listing does not silently pick up the US ADR’s 10-K.

Yahoo has no filing date. Non-US rows use `filed_date = report_date + 90 days`:

```text
AAPL FY2023 (SEC):
symbol report_date filed_date        total_debt   cash_and_equiv
  AAPL  2023-09-30 2023-11-03  101,266,000,000   29,965,000,000

NESN.SW latest annual (Yahoo):
 symbol report_date filed_date       total_debt  cash_and_equiv
NESN.SW  2025-12-31 2026-03-31   57,852,000,000    4,279,000,000
```

Apple’s 10-K arrived 34 days after fiscal year-end. Nestlé’s Yahoo row is stamped 90 days later by construction — weaker point-in-time than EDGAR, but usable when there is no CIK.

---

## 7. Sector filter and point-in-time helpers

These sit under the public API. Reach for them when you are assembling your own pipeline instead of `get_halal_universe`.

```python
from halalquant.screening import SectorFilter
from halalquant.utils._pit_adjustments import as_of_filter

sector_filter = SectorFilter()
kept = sector_filter.filter_symbols(
    ["AAPL", "JPM", "MET"],
    sector_map={
        "AAPL": "technology",
        "JPM": "conventional banking",
        "MET": "conventional insurance",
    },
)
# kept == ["AAPL"]
# sector_filter.audit_log records JPM and MET
```

```python
# Latest AAPL balance sheet that was already public on 2023-11-10
# → FY2023, filed 2023-11-03 (the day before would still be FY2022)
known = as_of_filter(balance_sheets, as_of="2023-11-10")
```

```text
symbol report_date filed_date
  AAPL  2023-09-30 2023-11-03
```

---

## Putting it together

```python
import halalquant as hq

candidates = ["AAPL", "MSFT", "NESN.SW", "SAP.DE", "JPM"]

prices = hq.download(candidates, start="2024-01-01", end="2024-12-31")
halal = hq.get_halal_universe(candidates)          # JPM dropped
side_by_side = hq.compare_standards(candidates)    # still no JPM (sector)
history = hq.get_financial_metrics("AAPL", start="2020-01-01", end="2024-12-31")
zakat_cash = hq.purify_dividends(halal["symbol"], start="2024-01-01", end="2024-12-31")
```

Feed `prices` and `halal["symbol"]` into your own backtester. Keep `zakat_cash["purification_amount"]` next to dividend income so the strategy’s P&L is net of purification.
