#!/usr/bin/env python3
"""Scan a universe of tickers, gate the results, and persist publishable
signals to Supabase.

Usage:
    python scripts/scan_universe.py AAPL MSFT GOOGL
    python scripts/scan_universe.py --seed seed/universe_symbols.csv --limit 5
    python scripts/scan_universe.py AAPL --dry-run   # gate + log, no writes

This is the entry point .github/workflows/signals-scan.yml calls (Phase 4 of
docs/backend-state-and-supabase-plan.md). Design notes:

- Calls the pipeline layer-by-layer (fetch -> indicators -> detect ->
  confluence) rather than reusing api/routes.py's get_signals(), because the
  publication gate must run BEFORE LLM synthesis — paying for a synthesis
  call on a signal that will be rejected is pure waste (see the plan's
  "Cost and rate-limit envelope" section).
- Per-symbol isolation: one bad ticker is a tallied failure, never an
  aborted run — the same discipline detect_all_signals() already applies
  to individual detectors, applied one level up.
- Bounded concurrency via a semaphore (yfinance throttles under load).
- Dependency-injected writer (SignalWriter Protocol) so this is testable
  without a live Supabase project.
"""
from __future__ import annotations

import argparse
import csv
import logging
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_script_dir = Path(__file__).resolve().parent
_project_root = _script_dir.parent
sys.path.insert(0, str(_project_root / "src"))
sys.path.insert(0, str(_project_root))

from signals_app.config import (  # noqa: E402
    DEFAULT_PERIOD,
    PUBLISH_MIN_CONFLUENCE_SCORE,
    PUBLISH_MIN_DATA_QUALITY,
    PUBLISH_MIN_SIGNALS,
    get_settings,
)
from signals_app.data.fetcher import DataFetcher  # noqa: E402
from signals_app.db.supabase import (  # noqa: E402
    EngineRun,
    SignalWriter,
    confluence_result_to_signal_record,
)
from signals_app.detection.orchestrator import detect_all_signals  # noqa: E402
from signals_app.indicators.compute import compute_indicators  # noqa: E402
from signals_app.indicators.data_quality import score_data_quality  # noqa: E402
from signals_app.scoring.calibration import load_strength_hit_rates_from_supabase  # noqa: E402
from signals_app.scoring.confluence import ConfluenceRanker  # noqa: E402
from signals_app.scoring.mtf import SUPPORTED_TIMEFRAMES  # noqa: E402

logger = logging.getLogger(__name__)

MAX_CONCURRENT_FETCHES = 4
_PERIOD_TO_TIMEFRAME: dict[str, str] = {
    "1d": "1D", "5d": "5D", "1mo": "1M", "3mo": "3M", "6mo": "6M", "1y": "1Y",
}
# Inverse mapping for Phase 10 (multi-timeframe matrix): SUPPORTED_TIMEFRAMES
# ("1D","5D","1M","3M","6M") -> the yfinance period string DataFetcher expects.
_TIMEFRAME_TO_PERIOD: dict[str, str] = {
    "1D": "1d", "5D": "5d", "1M": "1mo", "3M": "3mo", "6M": "6mo",
}


@dataclass
class SymbolResult:
    """Outcome of scanning one symbol."""

    ticker: str
    ok: bool
    published: bool
    reason: str | None = None  # why it wasn't published, or the error


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=_project_root, text=True
        ).strip()
    except Exception:
        return "unknown"


def load_symbols_from_csv(path: str) -> list[str]:
    """Read the `ticker` column from a seed CSV (see seed/universe_symbols.csv)."""
    with open(path, newline="") as f:
        return [row["ticker"].strip().upper() for row in csv.DictReader(f) if row.get("ticker")]


def parse_shard_spec(spec: str) -> tuple[int, int]:
    """Parse a "INDEX/TOTAL" shard spec (e.g. "0/4") into (index, total).

    Raises:
        ValueError: If the spec isn't parseable as two ints, or index is out
            of range for total (0 <= index < total).
    """
    try:
        index_str, total_str = spec.split("/")
        index, total = int(index_str), int(total_str)
    except ValueError as exc:
        raise ValueError(f"--shard must be INDEX/TOTAL (e.g. '0/4'), got {spec!r}") from exc
    if not (0 <= index < total):
        raise ValueError(f"--shard index must satisfy 0 <= INDEX < TOTAL, got {spec!r}")
    return index, total


def apply_shard(symbols: list[str], shard_index: int, shard_total: int) -> list[str]:
    """Select every shard_total-th symbol starting at shard_index, from an
    already-sorted list — so the same shard spec always selects the same
    tickers regardless of which GitHub Actions matrix job runs first.
    """
    return [s for i, s in enumerate(symbols) if i % shard_total == shard_index]


def passes_publication_gate(
    data_quality_score: float | None,
    total_signals: int,
    confluence_score: float,
    ai_degraded: bool,
) -> bool:
    """The publication gate — see config.py's PUBLISH_MIN_* constants and
    docs/backend-state-and-supabase-plan.md Part 3 §3 ("Selective").

    Most ticker-days should fail this gate. That is the point: an engine
    that always emits a direction carries no information.
    """
    if data_quality_score is None or data_quality_score < PUBLISH_MIN_DATA_QUALITY:
        return False
    if total_signals < PUBLISH_MIN_SIGNALS:
        return False
    if abs(confluence_score) < PUBLISH_MIN_CONFLUENCE_SCORE:
        return False
    return True


def build_matrix_for_symbol(ticker: str, settings: Any) -> dict[str, Any] | None:
    """Compute the full 5-timeframe matrix (Phase 10) for a symbol that has
    already cleared the single-period publication gate.

    Fetches SUPPORTED_TIMEFRAMES's 5 periods, scores each with
    compute_multi_timeframe(), then calls build_timeframe_matrix() — which
    makes up to 5 LLM calls, one per timeframe. Deliberately only called for
    already-gated symbols (see scan_one_symbol) so the 5x fetch/LLM cost is
    never paid for a symbol that would be rejected anyway.

    Returns:
        The TimeframeMatrix as a JSON-serializable dict (matches
        SignalMatrixRow.tsx's expected shape exactly), or None if fewer than
        2 timeframes had enough data to score — a 1-timeframe "matrix" isn't
        informative and isn't worth the LLM spend to synthesize.
    """
    import asyncio

    from signals_app.scoring.mtf import compute_multi_timeframe
    from signals_app.synthesis.mtf_llm import build_timeframe_matrix

    dfs_by_timeframe: dict[str, Any] = {}
    for tf in SUPPORTED_TIMEFRAMES:
        period = _TIMEFRAME_TO_PERIOD.get(tf)
        if period is None:
            continue
        try:
            fetcher = DataFetcher(settings=settings)
            df = fetcher.fetch(ticker, period).df
            if len(df) >= 20:
                dfs_by_timeframe[tf] = df
        except Exception as exc:
            logger.warning("matrix: %s %s fetch failed: %s", ticker, tf, exc)

    if len(dfs_by_timeframe) < 2:
        logger.info(
            "matrix: %s — only %d/%d timeframes had data, skipping matrix",
            ticker, len(dfs_by_timeframe), len(SUPPORTED_TIMEFRAMES),
        )
        return None

    mtf_result = compute_multi_timeframe(ticker, dfs_by_timeframe)

    features_by_timeframe: dict[str, dict[str, Any]] = {}
    for tf, ts in mtf_result.timeframe_scores.items():
        features_by_timeframe[tf] = {
            "symbol": ticker,
            "confluence_score": ts.result.score,
            "bias": ts.result.bias,
            "action": ts.result.action,
            "bull_count": ts.result.bull_count,
            "bear_count": ts.result.bear_count,
            "total_signals": ts.result.total_signals,
        }

    if not features_by_timeframe:
        return None

    loop = asyncio.new_event_loop()
    try:
        matrix = loop.run_until_complete(
            build_timeframe_matrix(ticker, features_by_timeframe, settings=settings)
        )
    finally:
        loop.close()

    return matrix.model_dump(mode="json")  # type: ignore[no-any-return]


def scan_one_symbol(
    ticker: str,
    period: str,
    writer: SignalWriter | None,
    run: EngineRun | None,
    settings: Any,
    dry_run: bool,
    strength_hit_rates: dict[str, float] | None = None,
    compute_matrix: bool = False,
) -> SymbolResult:
    """Run L1-L4 for one ticker, gate, optionally synthesize + persist.

    Never raises — every failure mode is caught and returned as a
    SymbolResult so scan_universe() can tally without aborting the run.
    """
    try:
        fetcher = DataFetcher(settings=settings)
        ohlcv = fetcher.fetch(ticker, period)
        if len(ohlcv.df) < 20:
            return SymbolResult(ticker, ok=False, published=False, reason="insufficient_bars")

        data_quality = score_data_quality(ohlcv.df, period)
        df = compute_indicators(ohlcv.df)
        signal_list = detect_all_signals(df)

        ranker = ConfluenceRanker()
        confluence = ranker.rank_signals(list(signal_list), strength_hit_rates=strength_hit_rates)

        bar_ts = df.index[-1].isoformat()

        if writer is not None and not dry_run:
            # symbols is the FK target for both detector_hits and signals —
            # must exist first for tickers scanned outside the seeded universe.
            writer.ensure_symbol(ticker)
            writer.write_detector_hits(ticker, bar_ts, list(signal_list))

        if not passes_publication_gate(
            data_quality.score, len(signal_list), confluence.score, signal_list.degraded
        ):
            return SymbolResult(ticker, ok=True, published=False, reason="gated")

        # Only symbols that cleared the gate pay for LLM synthesis.
        direction: str | None = None
        confidence: float | None = None
        evidence: list[dict[str, Any]] = []
        counter_evidence: list[dict[str, Any]] = []
        ai_degraded = False
        prompt_version: str | None = None

        if not dry_run:
            from signals_app.synthesis.mtf_llm import synthesize_single

            timeframe = _PERIOD_TO_TIMEFRAME.get(period, "1D")
            current = df.iloc[-1]
            features: dict[str, Any] = {
                "symbol": ticker,
                "period": period,
                "confluence_score": confluence.score,
                "bias": confluence.bias,
                "action": confluence.action,
                "bull_count": confluence.bull_count,
                "bear_count": confluence.bear_count,
                "total_signals": len(signal_list),
            }
            for col in ["RSI", "MACD", "ADX", "Close", "ATR", "Price_Change"]:
                try:
                    v = float(current[col])
                    if v == v and abs(v) != float("inf"):  # not NaN/inf
                        features[col.lower()] = round(v, 4)
                except Exception:
                    pass

            signal = synthesize_single(
                ticker=ticker, timeframe=timeframe, features=features, settings=settings,
            )
            direction = signal.direction.value
            confidence = signal.confidence
            ai_degraded = signal.ai_degraded
            prompt_version = signal.prompt_version
            evidence = [
                e.model_dump(mode="json") for e in signal.evidence.items if not e.is_counter
            ]
            counter_evidence = [
                e.model_dump(mode="json") for e in signal.evidence.items if e.is_counter
            ]

            matrix: dict[str, Any] | None = None
            if compute_matrix:
                try:
                    matrix = build_matrix_for_symbol(ticker, settings)
                except Exception as exc:
                    logger.warning("matrix: %s failed, publishing without it: %s", ticker, exc)

            record = confluence_result_to_signal_record(
                ticker=ticker,
                period=period,
                bar_ts=bar_ts,
                confluence=confluence,
                data_quality_score=data_quality.score,
                data_quality_reasons=data_quality.reasons,
                direction=direction,
                confidence=confidence,
                evidence=evidence,
                counter_evidence=counter_evidence,
                matrix=matrix,
                ai_degraded=ai_degraded,
                no_llm=False,
                prompt_version=prompt_version,
            )
            if writer is not None and run is not None:
                writer.write_signal(run, record)

        return SymbolResult(ticker, ok=True, published=True)

    except Exception as exc:
        logger.warning("scan_universe: %s failed: %s", ticker, exc)
        return SymbolResult(ticker, ok=False, published=False, reason=str(exc))


def scan_universe(
    symbols: list[str],
    period: str = DEFAULT_PERIOD,
    writer: SignalWriter | None = None,
    trigger: str = "manual",
    dry_run: bool = False,
    max_concurrent: int = MAX_CONCURRENT_FETCHES,
    compute_matrix: bool = False,
) -> list[SymbolResult]:
    """Run the pipeline over a universe and persist gated results.

    Args:
        symbols: Ticker symbols to scan.
        period: yfinance period string.
        writer: SignalWriter implementation (SupabaseWriter, or None for a
            dry run / local smoke test with no persistence).
        trigger: 'cron' | 'manual' | 'backfill' — recorded on engine_runs.
        dry_run: Skip LLM synthesis and all writes; just run L1-L4 and log
            what would have been gated/published.
        max_concurrent: Bounded fetch concurrency (yfinance throttles).
        compute_matrix: Also compute the 5-timeframe matrix (Phase 10) for
            symbols that clear the publication gate — up to 5x the fetches
            and LLM calls per gated symbol, so opt-in rather than default.

    Returns:
        One SymbolResult per input symbol.
    """
    settings = get_settings()
    run: EngineRun | None = None
    if writer is not None and not dry_run:
        run = writer.start_run(trigger=trigger, git_sha=_git_sha())

    # Fetched once per scan run, not once per symbol — same table read
    # regardless of which ticker is being scored. None (no active
    # generation yet, or Supabase unreachable) falls through to
    # ConfluenceRanker's existing uncalibrated default.
    strength_hit_rates = load_strength_hit_rates_from_supabase()

    results: list[SymbolResult] = []
    started = time.perf_counter()

    with ThreadPoolExecutor(max_workers=max_concurrent) as pool:
        futures = {
            pool.submit(
                scan_one_symbol,
                t, period, writer, run, settings, dry_run, strength_hit_rates, compute_matrix,
            ): t
            for t in symbols
        }
        for future in as_completed(futures):
            results.append(future.result())

    ok = sum(1 for r in results if r.ok)
    published = sum(1 for r in results if r.published)
    failed = len(results) - ok
    elapsed = time.perf_counter() - started

    logger.info(
        "scan_universe: %d symbols, %d ok, %d failed, %d published, %.1fs",
        len(results), ok, failed, published, elapsed,
    )

    if writer is not None and run is not None:
        failure_rate = failed / len(results) if results else 0.0
        status = "ok" if failure_rate == 0 else ("partial" if failure_rate < 0.2 else "failed")
        writer.finish_run(
            run,
            symbols_total=len(results),
            symbols_ok=ok,
            symbols_failed=failed,
            llm_provider=settings.llm_provider,
            status=status,
        )

    return results


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
            "universe (Phase 9). Sharding happens after sorting the full symbol "
            "list, so the same --shard value always selects the same tickers "
            "regardless of which shard runs first."
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
            "Also compute the 5-timeframe matrix (Phase 10) for symbols that "
            "clear the publication gate. Up to 5x the fetches/LLM calls per "
            "gated symbol — opt-in, not the default."
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
