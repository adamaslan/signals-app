#!/usr/bin/env python3
"""Scan a universe of tickers, gate the results, and persist publishable
signals to Supabase.

Usage:
    python scripts/scan_universe.py AAPL MSFT GOOGL
    python scripts/scan_universe.py --seed seed/universe_symbols.csv --limit 5
    python scripts/scan_universe.py AAPL --dry-run   # gate + log, no writes

This is the entry point .github/workflows/signals-scan.yml calls.

**Thin shim (design doc §2.2 / step 4).** The scan pipeline now lives in
``signals_app.scanner`` and is reached through ``signals_app.service.scan()``;
the `signals scan` CLI subcommand is the preferred interface. This file
remains only so the Actions workflow keeps working unchanged — it is
``main()`` + ``argparse`` and nothing else. Everything it calls is imported
from ``signals_app.scanner``, and re-exported here so existing test imports
(``from scripts.scan_universe import passes_publication_gate`` etc.) still
resolve.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root / "src"))
sys.path.insert(0, str(_project_root))

from signals_app.config import DEFAULT_PERIOD  # noqa: E402
from signals_app.scanner import (  # noqa: E402
    MAX_CONCURRENT_FETCHES,
    SymbolResult,
    apply_shard,
    build_matrix_for_symbol,
    load_symbols_from_csv,
    parse_shard_spec,
    passes_publication_gate,
    scan_one_symbol,
    scan_universe,
)

# Re-exported so `from scripts.scan_universe import X` keeps working for tests
# and any other caller. New code should import from signals_app.scanner.
__all__ = [
    "MAX_CONCURRENT_FETCHES",
    "SymbolResult",
    "apply_shard",
    "build_matrix_for_symbol",
    "load_symbols_from_csv",
    "parse_shard_spec",
    "passes_publication_gate",
    "scan_one_symbol",
    "scan_universe",
    "main",
]

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("symbols", nargs="*", help="Ticker symbols to scan")
    parser.add_argument(
        "--seed", help="CSV file with a 'ticker' column (e.g. seed/universe_symbols.csv)"
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Cap the number of symbols scanned"
    )
    parser.add_argument(
        "--shard",
        default=None,
        metavar="INDEX/TOTAL",
        help=(
            "Process only every TOTAL-th symbol starting at INDEX (0-based), e.g. "
            "'0/4' or '3/4' — for GitHub Actions matrix sharding across a large "
            "universe. Sharding happens after sorting the full symbol list, so "
            "the same --shard value always selects the same tickers regardless "
            "of which shard runs first."
        ),
    )
    parser.add_argument("--period", default=DEFAULT_PERIOD)
    parser.add_argument("--trigger", default="manual", choices=["cron", "manual", "backfill"])
    parser.add_argument(
        "--dry-run", action="store_true", help="Gate + log only, no LLM calls or writes"
    )
    parser.add_argument("--max-concurrent", type=int, default=MAX_CONCURRENT_FETCHES)
    parser.add_argument(
        "--matrix",
        action="store_true",
        help=(
            "Also compute the 5-timeframe matrix for symbols that clear the "
            "publication gate. Up to 5x the fetches/LLM calls per gated symbol "
            "— opt-in, not the default."
        ),
    )
    parser.add_argument(
        "--direction",
        choices=["bullish", "bearish"],
        default=None,
        help=(
            "Gate for one direction only. 'bullish' requires positive confluence "
            "(>= 0.35), 'bearish' requires negative confluence (<= -0.35). "
            "Default (None) gates both directions."
        ),
    )
    args = parser.parse_args()

    symbols = list(args.symbols)
    if args.seed:
        symbols.extend(load_symbols_from_csv(args.seed))
    if not symbols:
        parser.error("no symbols given — pass tickers directly or use --seed")
    symbols = sorted(set(symbols))

    if args.shard:
        try:
            shard_index, shard_total = parse_shard_spec(args.shard)
        except ValueError as exc:
            parser.error(str(exc))
        else:
            symbols = apply_shard(symbols, shard_index, shard_total)

    if args.limit:
        symbols = symbols[: args.limit]

    writer = None
    if not args.dry_run:
        from signals_app.db.supabase import SupabaseWriter

        writer = SupabaseWriter()

    try:
        results = scan_universe(
            symbols,
            period=args.period,
            writer=writer,
            trigger=args.trigger,
            dry_run=args.dry_run,
            max_concurrent=args.max_concurrent,
            compute_matrix=args.matrix,
            direction=args.direction,
        )
    finally:
        if writer is not None:
            writer.close()

    published = [r.ticker for r in results if r.published]
    failed = [(r.ticker, r.reason) for r in results if not r.ok]
    print(f"Scanned {len(results)} symbols — {len(published)} published, {len(failed)} failed")
    if published:
        print(f"  published: {', '.join(published)}")
    if failed:
        print(f"  failed: {failed}")


if __name__ == "__main__":
    main()
