#!/usr/bin/env python3
"""One-time historical backfill — writes every historical bar's detector
hits to Supabase, giving scripts/calibrate.py (Phase 7) a real corpus to
work with on day one instead of waiting months for the live scanner to
accumulate CALIBRATION_MIN_BUCKET_SIZE (30) samples per strength bucket.

Usage:
    python scripts/backfill_history.py AAPL MSFT GOOGL --period 5y
    python scripts/backfill_history.py --seed seed/universe_symbols.csv --limit 5 --period 5y

Phase 6 of docs/backend-state-and-supabase-plan.md. workflow_dispatch only
— this is a one-time bootstrap, not something to run on a schedule.
"""
from __future__ import annotations

import argparse
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_script_dir = Path(__file__).resolve().parent
_project_root = _script_dir.parent
sys.path.insert(0, str(_project_root / "src"))
sys.path.insert(0, str(_project_root))

from scripts.scan_universe import load_symbols_from_csv  # noqa: E402
from signals_app.config import get_settings  # noqa: E402
from signals_app.data.fetcher import DataFetcher  # noqa: E402
from signals_app.db.supabase import SignalWriter, SupabaseWriter  # noqa: E402
from signals_app.detection.historical import scan_historical  # noqa: E402
from signals_app.indicators.compute import compute_indicators  # noqa: E402

logger = logging.getLogger(__name__)

MAX_CONCURRENT_FETCHES = 4
# fetcher.py's PERIOD_TO_INTERVAL maps "5y"/"2y" to weekly bars and "1y" to
# daily (yfinance restricts daily-interval history to roughly a year). Both
# clear MIN_HISTORICAL_LOOKBACK (200) by a similar margin in practice
# (verified: 1y -> 252 daily bars / 52 scannable; 5y -> 262 weekly bars / 62
# scannable) — 5y's weekly bars cover ~5x the calendar range at the cost of
# representing weekly, not daily, price action. Kept at the plan's original
# "5y" default; pass --period 1y for a denser same-timeframe-as-live-scan
# corpus instead.
DEFAULT_BACKFILL_PERIOD = "5y"


@dataclass
class BackfillResult:
    """Outcome of backfilling one symbol."""

    ticker: str
    ok: bool
    bars_scanned: int
    hits_written: int
    reason: str | None = None


def backfill_one_symbol(
    ticker: str, period: str, writer: SignalWriter | None, settings: Any
) -> BackfillResult:
    """Scan a symbol's full history and write every bar's detector hits.

    writer=None runs the fetch + scan but skips all writes — a dry-run
    timing/sanity check. Never raises — mirrors scan_universe.py's
    per-symbol isolation so one bad ticker doesn't abort a multi-hour run.
    """
    try:
        fetcher = DataFetcher(settings=settings)
        ohlcv = fetcher.fetch(ticker, period)
        if len(ohlcv.df) < 20:
            return BackfillResult(
                ticker, ok=False, bars_scanned=0, hits_written=0, reason="insufficient_bars"
            )

        df = compute_indicators(ohlcv.df)
        bars = scan_historical(df)
        if not bars:
            return BackfillResult(
                ticker, ok=True, bars_scanned=0, hits_written=0, reason="below_min_lookback"
            )

        if writer is not None:
            writer.ensure_symbol(ticker)

        hits_written = 0
        for bar in bars:
            if not bar.signals:
                continue
            if writer is not None:
                writer.write_detector_hits(ticker, bar.date.isoformat(), bar.signals)
            hits_written += len(bar.signals)

        return BackfillResult(ticker, ok=True, bars_scanned=len(bars), hits_written=hits_written)

    except Exception as exc:
        logger.warning("backfill: %s failed: %s", ticker, exc)
        return BackfillResult(ticker, ok=False, bars_scanned=0, hits_written=0, reason=str(exc))


def backfill_history(
    symbols: list[str],
    period: str = DEFAULT_BACKFILL_PERIOD,
    writer: SignalWriter | None = None,
    max_concurrent: int = MAX_CONCURRENT_FETCHES,
) -> list[BackfillResult]:
    """Backfill historical detector hits for a list of symbols.

    Args:
        symbols: Ticker symbols to backfill.
        period: yfinance period string — needs to clear MIN_HISTORICAL_LOOKBACK
            (200 bars) plus a meaningful scan window. 5y is the plan's default.
        writer: SignalWriter implementation. None runs the scan but skips all
            writes — useful for a dry-run timing/sanity check.
        max_concurrent: Bounded fetch concurrency (yfinance throttles).

    Returns:
        One BackfillResult per input symbol.
    """
    settings = get_settings()
    results: list[BackfillResult] = []

    with ThreadPoolExecutor(max_workers=max_concurrent) as pool:
        futures = {
            pool.submit(backfill_one_symbol, t, period, writer, settings): t
            for t in symbols
        }
        for future in as_completed(futures):
            results.append(future.result())

    ok = sum(1 for r in results if r.ok)
    total_hits = sum(r.hits_written for r in results)
    logger.info(
        "backfill_history: %d symbols, %d ok, %d total detector hits written",
        len(results), ok, total_hits,
    )
    return results


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("symbols", nargs="*", help="Ticker symbols to backfill")
    parser.add_argument("--seed", help="CSV file with a 'ticker' column")
    parser.add_argument("--limit", type=int, default=None, help="Cap the number of symbols")
    parser.add_argument("--period", default=DEFAULT_BACKFILL_PERIOD)
    parser.add_argument("--dry-run", action="store_true", help="Scan only, no writes")
    parser.add_argument("--max-concurrent", type=int, default=MAX_CONCURRENT_FETCHES)
    args = parser.parse_args()

    symbols = list(args.symbols)
    if args.seed:
        symbols.extend(load_symbols_from_csv(args.seed))
    if not symbols:
        parser.error("no symbols given — pass tickers directly or use --seed")
    symbols = sorted(set(symbols))
    if args.limit:
        symbols = symbols[: args.limit]

    writer = None
    if not args.dry_run:
        writer = SupabaseWriter()

    try:
        results = backfill_history(
            symbols, period=args.period, writer=writer, max_concurrent=args.max_concurrent
        )
    finally:
        if writer is not None:
            writer.close()

    ok = [r for r in results if r.ok]
    failed = [(r.ticker, r.reason) for r in results if not r.ok]
    total_hits = sum(r.hits_written for r in results)
    print(f"Backfilled {len(ok)}/{len(results)} symbols — {total_hits} total detector hits")
    if failed:
        print(f"  failed: {failed}")


if __name__ == "__main__":
    main()
