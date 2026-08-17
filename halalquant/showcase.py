"""
Terminal showcase of the public API — meant to be screenshotted for the README.

    pip install "halalquant[examples]"
    python -m halalquant
    python -m halalquant --svg docs/showcase.svg
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

try:
    from rich import box
    from rich.align import Align
    from rich.console import Console, Group
    from rich.panel import Panel
    from rich.rule import Rule
    from rich.table import Table
    from rich.text import Text
except ImportError:
    sys.stderr.write(
        "This demo needs the Rich extra.\n"
        "  pip install 'halalquant[examples]'\n"
        "  pip install -e '.[examples]'   # from a clone\n"
    )
    raise SystemExit(1)

import halalquant as hq
from halalquant.providers import FilingsProvider, SECEdgarProvider, YFinanceProvider
from halalquant.screening import SectorFilter

TICKERS = ["AAPL", "MSFT", "NVDA", "KO", "JPM", "PFE"]
GOLD = "#C9A227"
MINT = "#9BE7C4"
PASS = "#3DDB8A"
FAIL = "#FF6B6B"
WARN = "#F4B942"
MUTED = "grey58"

AAOIFI_DEBT = 0.30
AAOIFI_CASH = 0.30
AAOIFI_RECV = 0.70


def activity_label(raw: str) -> str:
    aliases = {
        "conventional banking": "banking",
        "conventional insurance": "insurance",
        "consumer defensive": "consumer",
        "consumer cyclical": "consumer",
        "healthcare": "healthcare",
        "technology": "technology",
        "communication services": "comms",
    }
    return aliases.get(raw, raw)


def verdict_text(ok: bool) -> Text:
    if ok:
        return Text.from_markup(f"[bold {PASS}]PASS[/]")
    return Text.from_markup(f"[bold {FAIL}]FAIL[/]")


def pct_cell(value: object, limit: float) -> Text:
    if value is None or value != value:  # NaN
        return Text("—", style=MUTED)
    number = float(value)
    style = PASS if number < limit else FAIL
    return Text(f"{number * 100:5.2f}%", style=style)


def header_panel(version: str) -> Panel:
    title = Text()
    title.append("halalquant", style=f"bold {GOLD}")
    title.append("  ·  ", style="dim")
    title.append("Shariah-compliant quant data engine", style=f"italic {MINT}")
    subtitle = Text.from_markup(
        f"[dim]v{version}  ·  AAOIFI / DJIM  ·  yfinance + SEC EDGAR  ·  no API key[/]"
    )
    return Panel(
        Align.center(Group(title, subtitle)),
        border_style=GOLD,
        padding=(1, 2),
        box=box.ROUNDED,
    )


def screening_table(
    compared,
    sector_map: dict[str, str],
    excluded: dict[str, str],
) -> Table:
    table = Table(
        title="Live universe screen",
        title_style=f"bold {MINT}",
        box=box.SIMPLE_HEAVY,
        header_style=f"bold {GOLD}",
        border_style="grey23",
        pad_edge=False,
        expand=False,
        show_lines=False,
    )
    table.add_column("Ticker", style="bold white", no_wrap=True, min_width=6)
    table.add_column("Activity", no_wrap=True, min_width=10)
    table.add_column("AAOIFI", justify="center", no_wrap=True, min_width=6)
    table.add_column("DJIM", justify="center", no_wrap=True, min_width=4)
    table.add_column("Debt", justify="right", no_wrap=True, min_width=6)
    table.add_column("Cash", justify="right", no_wrap=True, min_width=6)
    table.add_column("Recv.", justify="right", no_wrap=True, min_width=6)
    table.add_column("Note", no_wrap=True, min_width=12)

    by_symbol = compared.set_index("symbol") if not compared.empty else compared
    for ticker in TICKERS:
        activity = activity_label(sector_map.get(ticker, "—"))
        if ticker in excluded:
            table.add_row(
                ticker,
                Text(activity, style=WARN),
                Text("EXCL", style=f"bold {WARN}"),
                Text("EXCL", style=f"bold {WARN}"),
                Text("—", style=MUTED),
                Text("—", style=MUTED),
                Text("—", style=MUTED),
                Text("sector screen", style=WARN),
            )
            continue
        if compared.empty or ticker not in by_symbol.index:
            table.add_row(
                ticker,
                activity,
                Text("—", style=MUTED),
                Text("—", style=MUTED),
                Text("—", style=MUTED),
                Text("—", style=MUTED),
                Text("—", style=MUTED),
                Text("no fundamentals", style=MUTED),
            )
            continue
        row = by_symbol.loc[ticker]
        aaoifi_ok = bool(row["aaoifi_compliant"])
        djim_ok = bool(row["djim_compliant"])
        note = str(row["aaoifi_reason"])
        if aaoifi_ok and djim_ok:
            note = "passes both"
        elif note == "debt ratio exceeds threshold":
            note = "debt high"
        elif note == "cash ratio exceeds threshold":
            note = "cash high"
        elif note == "receivables ratio exceeds threshold":
            note = "recv high"
        table.add_row(
            ticker,
            Text(activity, style=MUTED),
            verdict_text(aaoifi_ok),
            verdict_text(djim_ok),
            pct_cell(row["debt_ratio"], AAOIFI_DEBT),
            pct_cell(row["cash_ratio"], AAOIFI_CASH),
            pct_cell(row["receivables_ratio"], AAOIFI_RECV),
            Text(note, style="white" if aaoifi_ok else FAIL),
        )
    return table


def purification_table(frame) -> Table:
    table = Table(
        title="AAPL dividend purification (2024)",
        title_style=f"bold {MINT}",
        box=box.SIMPLE_HEAVY,
        header_style=f"bold {GOLD}",
        border_style="grey23",
        pad_edge=False,
        expand=False,
    )
    table.add_column("Ex-date", no_wrap=True)
    table.add_column("Dividend", justify="right")
    table.add_column("Impure ratio", justify="right")
    table.add_column("Donate / share", justify="right")
    table.add_column("Keep / share", justify="right")

    for _, row in frame.iterrows():
        ex_date = str(row["ex_date"])[:10]
        dividend = float(row["dividend"])
        ratio = float(row["impure_ratio"]) if row["impure_ratio"] == row["impure_ratio"] else 0.0
        donate = float(row["purification_amount"]) if row["purification_amount"] == row["purification_amount"] else 0.0
        table.add_row(
            ex_date,
            f"${dividend:.4f}",
            Text(f"{ratio * 100:.2f}%", style=WARN if ratio else MUTED),
            Text(f"${donate:.4f}", style=GOLD),
            Text(f"${dividend - donate:.4f}", style=PASS),
        )
    return table


def summary_line(
    compared,
    excluded: dict[str, str],
) -> Text:
    n = len(TICKERS)
    n_excl = len(excluded)
    if compared.empty:
        aaoifi_pass = djim_pass = 0
    else:
        screened = compared[~compared["symbol"].isin(excluded.keys())]
        aaoifi_pass = int(screened["aaoifi_compliant"].sum()) if not screened.empty else 0
        djim_pass = int(screened["djim_compliant"].sum()) if not screened.empty else 0
    text = Text(justify="center")
    text.append(f"screened {n} names", style=MUTED)
    text.append("  ·  ", style="dim")
    text.append(f"{aaoifi_pass} pass AAOIFI", style=PASS)
    text.append("  ·  ", style="dim")
    text.append(f"{djim_pass} pass DJIM", style=PASS)
    text.append("  ·  ", style="dim")
    text.append(f"{n_excl} sector exclusion{'s' if n_excl != 1 else ''}", style=WARN)
    return text


def footer() -> Text:
    text = Text(justify="center")
    text.append("pip install halalquant", style=f"bold {GOLD}")
    text.append("   github.com/regional-specter/halalquant", style=MUTED)
    return text


def run_library():
    market = YFinanceProvider()
    filings = FilingsProvider(sec=SECEdgarProvider(), yahoo=market)
    sector_filter = SectorFilter()

    sector_map = market.get_sector_map(TICKERS)
    sector_filter.filter_symbols(TICKERS, sector_map=sector_map or None)
    excluded = {entry["symbol"]: entry["reason"] for entry in sector_filter.audit_log}

    compared = hq.compare_standards(
        TICKERS,
        apply_sector_filter=False,
        provider=market,
        filings=filings,
    )
    purified = hq.purify_dividends(
        "AAPL",
        start="2024-01-01",
        end="2024-12-31",
        provider=market,
        filings=filings,
    )
    return compared, sector_map, excluded, purified


def render(console: Console, compared, sector_map, excluded, purified) -> None:
    console.print()
    console.print(header_panel(hq.__version__))
    console.print()
    console.print(
        Text("Ratios vs 24-month average market cap  ·  red = over AAOIFI limit", style=MUTED, justify="center")
    )
    console.print()
    console.print(screening_table(compared, sector_map, excluded))
    console.print(Align.center(summary_line(compared, excluded)))
    console.print()
    console.print(Rule(style="grey23"))
    console.print()
    if purified is None or purified.empty:
        console.print("[dim]No AAPL dividends in the requested window.[/]")
    else:
        console.print(purification_table(purified))
    console.print()
    console.print(Align.center(footer()))
    console.print()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pretty terminal demo of halalquant.")
    parser.add_argument(
        "--svg",
        nargs="?",
        const="docs/showcase.svg",
        default=None,
        help="Write a README-ready SVG (default path: docs/showcase.svg).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    console = Console(
        record=True,
        width=100,
        force_terminal=True,
        color_system="truecolor",
        highlight=False,
    )

    console.print()
    sys.stderr.write("Fetching live yfinance + SEC EDGAR data…\n")
    try:
        compared, sector_map, excluded, purified = run_library()
    except Exception as exc:
        console.print(f"[bold {FAIL}]Showcase failed:[/] {exc}")
        raise SystemExit(1) from exc

    render(console, compared, sector_map, excluded, purified)

    if args.svg:
        target = Path(args.svg)
        target.parent.mkdir(parents=True, exist_ok=True)
        console.save_svg(
            str(target),
            title=f"halalquant  ·  {date.today().isoformat()}",
        )
        # Printed after save_svg so it is not baked into the README image.
        console.print(f"[dim]Wrote {target}[/]")
