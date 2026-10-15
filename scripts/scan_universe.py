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
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root / "src"))
sys.path.insert(0, str(_project_root))

from signals_app.config import DEFAULT_PERIOD  # noqa: E402
from signals_app.scanner import (  # noqa: E402
    FAILED_RUN_FAILURE_RATE,
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
_PUBLIC_REASONS = {"gated", "insufficient_bars"}


def _reason_code(reason: str | None) -> str | None:
    """Keep error details out of uploaded workflow artifacts."""
    if reason is None:
        return None
    return reason if reason in _PUBLIC_REASONS else "error"


def main(argv: list[str] | None = None) -> int:
    """Run the scan and return a workflow-facing exit status."""
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
    parser.add_argument("--report-file", type=Path, help="Write a JSON outcome artifact")
    parser.add_argument(
        "--max-failure-rate",
        type=float,
        default=FAILED_RUN_FAILURE_RATE,
        help=(
            "Fail the command when this fraction of scanned symbols fails. "
            f"Default and maximum: {FAILED_RUN_FAILURE_RATE}, the rate at which "
            "the run is persisted as failed."
        ),
    )
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
    args = parser.parse_args(argv)

    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be non-negative")
    if not 1 <= args.max_concurrent <= MAX_CONCURRENT_FETCHES:
        parser.error(f"--max-concurrent must be between 1 and {MAX_CONCURRENT_FETCHES}")
    if not 0 < args.max_failure_rate <= FAILED_RUN_FAILURE_RATE:
        parser.error(
            f"--max-failure-rate must be greater than 0 and at most {FAILED_RUN_FAILURE_RATE}"
        )

    symbols = [symbol.strip().upper() for symbol in args.symbols]
    if args.seed:
        symbols.extend(load_symbols_from_csv(args.seed))
    if not symbols:
        parser.error("no symbols given — pass tickers directly or use --seed")
    symbols = sorted({symbol for symbol in symbols if symbol})

    universe_hash = hashlib.sha256("\n".join(symbols).encode()).hexdigest()
    if args.shard:
        try:
            shard_index, shard_total = parse_shard_spec(args.shard)
        except ValueError as exc:
            parser.error(str(exc))
        else:
            symbols = apply_shard(symbols, shard_index, shard_total)

    if args.limit is not None:
        symbols = symbols[: args.limit]
    if not symbols:
        parser.error("selection contains no symbols")

    writer = None
    if not args.dry_run:
        from signals_app.db.supabase import SupabaseWriter

        writer = SupabaseWriter()

    writer_close_error: Exception | None = None
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
            # Defer a cleanup failure so the outcome report is still written;
            # it is re-raised below once the report exists.
            try:
                writer.close()
            except Exception as exc:  # noqa: BLE001 - re-raised after the report
                writer_close_error = exc
                logger.exception("writer cleanup failed")

    passed_gate = [r.ticker for r in results if r.published]
    failed = [(r.ticker, r.reason) for r in results if not r.ok]
    publication_label = "would publish" if args.dry_run else "published"
    print(f"Scanned {len(results)} symbols — {len(passed_gate)} {publication_label}, {len(failed)} failed")
    if passed_gate:
        print(f"  {publication_label}: {', '.join(passed_gate)}")
    if failed:
        print(f"  failed: {failed}")
    if args.report_file is not None:
        report = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "universe_sha256": universe_hash,
            "shard": args.shard,
            "period": args.period,
            "dry_run": args.dry_run,
            "direction": args.direction or "both",
            "attempted": len(results),
            "valid": len(results) - len(failed),
            "gated": sum(r.ok and not r.published for r in results),
            "published": 0 if args.dry_run else len(passed_gate),
            "would_publish": len(passed_gate) if args.dry_run else 0,
            "failed": len(failed),
            "stale": None,  # SymbolResult does not expose freshness yet.
            "results": [
                {
                    "ticker": r.ticker,
                    "ok": r.ok,
                    "published": r.published and not args.dry_run,
                    "would_publish": r.published and args.dry_run,
                    "reason_code": _reason_code(r.reason),
                    "p_outperform": r.p_outperform,
                    "rank_pct": r.rank_pct,
                }
                for r in results
            ],
        }
        args.report_file.parent.mkdir(parents=True, exist_ok=True)
        args.report_file.write_text(json.dumps(report, indent=2) + "\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write(
                f"### Signals scan ({args.shard or 'pilot'}, {'preview' if args.dry_run else 'write'})\n"
                f"Attempted: {len(results)} · Valid: {len(results) - len(failed)} · "
                f"Gated: {sum(r.ok and not r.published for r in results)} · "
                f"{publication_label.title()}: {len(passed_gate)} · Failed: {len(failed)}\n\n"
            )
    if writer_close_error is not None:
        raise writer_close_error
    if not results:
        logger.error("scan selected no symbols")
        return 1
    if len(failed) / len(results) >= args.max_failure_rate:
        logger.error("scan failure rate reached %.0f%% threshold", args.max_failure_rate * 100)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
