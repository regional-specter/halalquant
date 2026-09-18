"""Command-line helpers: ``python -m halalquant prepare``."""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

from halalquant.database._dataset import prepare_dataset


def prepare_main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m halalquant prepare",
        description=(
            "Fetch S&P 500 (or explicit tickers) once and write a local "
            "AAOIFI metrics database (DuckDB + Parquet under ~/.halalquant/)."
        ),
    )
    parser.add_argument(
        "--universe",
        default="sp500",
        help="Named universe when --tickers is omitted (default: sp500).",
    )
    parser.add_argument(
        "--tickers",
        default=None,
        help="Comma-separated symbols. Overrides --universe.",
    )
    parser.add_argument("--start", default=None, help="Inclusive start date (YYYY-MM-DD).")
    parser.add_argument("--end", default=None, help="Inclusive end date (YYYY-MM-DD).")
    parser.add_argument(
        "--freq",
        default="ME",
        help="Calendar frequency for the PIT panel (ME, QE). Pass none for annual only.",
    )
    parser.add_argument(
        "--cache",
        default=None,
        help="DuckDB file path. Default: ~/.halalquant/cache.duckdb",
    )
    parser.add_argument(
        "--no-sector-filter",
        action="store_true",
        help="Keep banks/insurers and other excluded sectors in the fetch.",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Ignore existing cache rows and refetch.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    freq: Optional[str]
    if str(args.freq).strip().lower() in {"", "none", "annual"}:
        freq = None
    else:
        freq = args.freq
    try:
        summary = prepare_dataset(
            tickers=args.tickers,
            universe=args.universe,
            start=args.start,
            end=args.end,
            freq=freq,
            cache=args.cache if args.cache else True,
            apply_sector_filter=not args.no_sector_filter,
            force_refresh=args.force_refresh,
            progress=True,
        )
    except Exception as exc:
        sys.stderr.write(f"prepare failed: {exc}\n")
        raise SystemExit(1) from exc
    print()
    print(summary.to_string(index=False))
    print(f"\n[{len(summary)} symbol(s)]")
