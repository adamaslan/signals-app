#!/usr/bin/env python3
"""Robust one-month candidate scanner (bullish + bearish).

Combines the dedicated bullish scanner from scan_bullish_2wk.py with the
production-grade pipeline from scan_universe.py:

1. Calibrate at a 21-trading-day horizon (~1 month) if needed
2. Scan the universe with data-quality + publication gate (both directions)
3. Run LLM synthesis only for symbols that clear the gate
4. Rank by LLM confidence (fallback: calibrated confluence.strength)
5. Write a markdown report to docs/ with separate Bullish and Bearish sections
6. Optionally persist to Supabase

Usage:
    python scripts/scan_monthly_candidates.py                       # both directions, top 50 each
    python scripts/scan_monthly_candidates.py --dry-run             # no LLM/writes
    python scripts/scan_monthly_candidates.py --direction bullish   # bullish only
    python scripts/scan_monthly_candidates.py --write-supabase      # persist to Supabase
    python scripts/scan_monthly_candidates.py --shard 0/4           # shard across workers
"""
from __future__ import annotations

import argparse
import datetime
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

from scripts.calibrate import run_calibration
from scripts.scan_universe import (
    apply_shard,
    build_matrix_for_symbol,
    load_symbols_from_csv,
    parse_shard_spec,
    passes_publication_gate,
)
from signals_app.config import get_settings
from signals_app.data.fetcher import DataFetcher
from signals_app.db.supabase import (
    EngineRun,
    SignalWriter,
    SupabaseWriter,
    confluence_result_to_signal_record,
)
from signals_app.detection.orchestrator import detect_all_signals
from signals_app.indicators.compute import compute_indicators
from signals_app.indicators.data_quality import score_data_quality
from signals_app.scoring.calibration import load_strength_hit_rates
from signals_app.scoring.confluence import ConfluenceRanker
from signals_app.synthesis.mtf_llm import synthesize_single

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CALIBRATION_FILE_21D = "./calibration/strength_hit_rates_21d.json"
OUTPUT_DIR = _project_root / "docs"  # Reports go into docs/ at project root
HORIZON_DAYS = 21  # ~1 trading month
DEFAULT_SCAN_PERIOD = "6mo"
DEFAULT_CALIBRATION_PERIOD = "5y"
MAX_CONCURRENT_FETCHES = 4
DEFAULT_TOP_N = 50  # List top 50 for each side

_PERIOD_TO_TIMEFRAME = {
    "1d": "1D", "5d": "5D", "1mo": "1M", "3mo": "3M", "6mo": "6M", "1y": "1Y",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class MonthlyCandidate:
    """A signal that cleared the gate and (optionally) LLM synthesis."""

    ticker: str
    confluence_score: float
    confidence: float | None
    direction: str | None
    bull_count: int
    bear_count: int
    total_signals: int
    data_quality_score: float | None
    sector: str = "N/A"

    @property
    def rank_score(self) -> float:
        """Higher is better — use LLM confidence when available."""
        return self.confidence if self.confidence is not None else self.confluence_score


@dataclass
class ScanOutcome:
    """Result of scanning one symbol."""

    ticker: str
    candidate: MonthlyCandidate | None = None
    published: bool = False
    error: str | None = None
    reason: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=_project_root, text=True
        ).strip()
    except Exception:
        return "unknown"


def _ensure_calibration(
    symbols: list[str],
    period: str = DEFAULT_CALIBRATION_PERIOD,
    skip: bool = False,
) -> dict[str, float] | None:
    """Ensure a 21-day-horizon calibration file exists."""
    cal_path = Path(CALIBRATION_FILE_21D)

    if skip:
        if not cal_path.exists():
            logger.warning("--skip-calibration set but no 21d calibration file found")
            return None
        logger.info("Using existing 21d calibration at %s", cal_path)
        return load_strength_hit_rates(path=str(cal_path))

    if cal_path.exists():
        logger.info("Using existing 21d calibration at %s", cal_path)
        return load_strength_hit_rates(path=str(cal_path))

    logger.info("No 21d calibration found — running calibration on %d symbols", len(symbols))
    Path(CALIBRATION_FILE_21D).parent.mkdir(parents=True, exist_ok=True)

    try:
        rates = run_calibration(
            symbols=symbols,
            period=period,
            horizon_days=HORIZON_DAYS,
            output_path=str(cal_path),
        )
        logger.info("Calibration complete: wrote %s", cal_path)
        return rates
    except RuntimeError as e:
        logger.error("Calibration failed: %s", e)
        return None


def load_sector_mapping() -> dict[str, str]:
    """Load ticker -> sector mapping from seed CSV if available."""
    sector_map: dict[str, str] = {}
    seed_path = Path(_project_root / "seed" / "universe_symbols.csv")
    if not seed_path.exists():
        return sector_map

    try:
        import csv

        with open(seed_path, newline="") as f:
            for row in csv.DictReader(f):
                ticker = row.get("ticker", "").strip().upper()
                sector = row.get("sector_group", "N/A").strip()
                if ticker:
                    sector_map[ticker] = sector
    except Exception as e:
        logger.warning("Failed to load sector mapping: %s", e)

    return sector_map


# ---------------------------------------------------------------------------
# Per-symbol scanner
# ---------------------------------------------------------------------------
def _scan_one_symbol(
    ticker: str,
    period: str,
    settings: Any,
    dry_run: bool,
    strength_hit_rates: dict[str, float] | None,
    compute_matrix: bool,
    writer: SignalWriter | None,
    run: EngineRun | None,
    direction: str | None,
) -> ScanOutcome:
    """Run L1-L4, gate, optionally synthesize + persist for one symbol.

    Never raises — all exceptions are captured as ScanOutcome.
    """
    try:
        # L1: Fetch
        fetcher = DataFetcher(settings=settings)
        ohlcv = fetcher.fetch(ticker, period)
        if len(ohlcv.df) < 20:
            return ScanOutcome(ticker, error="insufficient_bars", reason="insufficient_bars")

        # L2: Data quality + indicators
        data_quality = score_data_quality(ohlcv.df, period)
        df = compute_indicators(ohlcv.df)
        signal_list = detect_all_signals(df)

        # L3: Confluence scoring
        ranker = ConfluenceRanker()
        confluence = ranker.rank_signals(list(signal_list), strength_hit_rates=strength_hit_rates)

        bar_ts = df.index[-1].isoformat()

        # Persist detector hits even if later gated
        if writer is not None and not dry_run:
            writer.ensure_symbol(ticker)
            writer.write_detector_hits(ticker, bar_ts, list(signal_list))

        # L4: Publication gate — with data quality + direction
        if not passes_publication_gate(
            data_quality_score=data_quality.score,
            total_signals=len(signal_list),
            confluence_score=confluence.score,
            ai_degraded=signal_list.degraded,
            direction=direction,
        ):
            return ScanOutcome(ticker, reason="gated")

        # Gate passed — only now pay for LLM synthesis
        synthesis_direction = None
        confidence = None
        evidence = []
        counter_evidence = []
        ai_degraded = False
        prompt_version = None
        matrix = None

        if not dry_run:
            timeframe = _PERIOD_TO_TIMEFRAME.get(period, "1D")
            current = df.iloc[-1]

            features = {
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
                    if v == v and abs(v) != float("inf"):
                        features[col.lower()] = round(v, 4)
                except Exception:
                    pass

            signal = synthesize_single(
                ticker=ticker,
                timeframe=timeframe,
                features=features,
                settings=settings,
            )
            synthesis_direction = signal.direction.value
            confidence = signal.confidence
            ai_degraded = signal.ai_degraded
            prompt_version = signal.prompt_version
            evidence = [e.model_dump(mode="json") for e in signal.evidence.items if not e.is_counter]
            counter_evidence = [
                e.model_dump(mode="json") for e in signal.evidence.items if e.is_counter
            ]

            if compute_matrix:
                try:
                    matrix = build_matrix_for_symbol(ticker, settings)
                except Exception as exc:
                    logger.warning("matrix: %s failed, continuing without it: %s", ticker, exc)

            if writer is not None and run is not None:
                record = confluence_result_to_signal_record(
                    ticker=ticker,
                    period=period,
                    bar_ts=bar_ts,
                    confluence=confluence,
                    data_quality_score=data_quality.score,
                    data_quality_reasons=data_quality.reasons,
                    direction=synthesis_direction,
                    confidence=confidence,
                    evidence=evidence,
                    counter_evidence=counter_evidence,
                    matrix=matrix,
                    ai_degraded=ai_degraded,
                    no_llm=False,
                    prompt_version=prompt_version,
                )
                writer.write_signal(run, record)
        else:
            # Dry run: no LLM confidence. Use calibrated confluence.strength as proxy.
            confidence = confluence.strength

        candidate = MonthlyCandidate(
            ticker=ticker,
            confluence_score=confluence.score,
            confidence=confidence,
            direction=synthesis_direction,
            bull_count=confluence.bull_count,
            bear_count=confluence.bear_count,
            total_signals=len(signal_list),
            data_quality_score=data_quality.score,
        )
        return ScanOutcome(ticker, candidate=candidate, published=not dry_run)

    except Exception as exc:
        logger.warning("monthly_scan: %s failed: %s", ticker, exc)
        return ScanOutcome(ticker, error=str(exc), reason=str(exc))


# ---------------------------------------------------------------------------
# Universe scanner
# ---------------------------------------------------------------------------
def scan_monthly_universe(
    symbols: list[str],
    period: str = DEFAULT_SCAN_PERIOD,
    writer: SignalWriter | None = None,
    trigger: str = "manual",
    dry_run: bool = False,
    max_concurrent: int = MAX_CONCURRENT_FETCHES,
    compute_matrix: bool = False,
    direction: str | None = None,  # None = both directions
    strength_hit_rates: dict[str, float] | None = None,
) -> tuple[list[MonthlyCandidate], dict[str, Any]]:
    """Scan all symbols and return candidates plus run stats.

    Args:
        direction: Gate direction — "bullish", "bearish", or None for both.
            When None, we collect candidates from both sides (confluence sign
            determines side for splitting later).
    """
    settings = get_settings()
    run: EngineRun | None = None

    if writer is not None and not dry_run:
        run = writer.start_run(trigger=trigger, git_sha=_git_sha())

    outcomes: list[ScanOutcome] = []
    candidates: list[MonthlyCandidate] = []
    started = time.perf_counter()

    with ThreadPoolExecutor(max_workers=max_concurrent) as pool:
        futures = {
            pool.submit(
                _scan_one_symbol,
                ticker,
                period,
                settings,
                dry_run,
                strength_hit_rates,
                compute_matrix,
                writer,
                run,
                direction,
            ): ticker
            for ticker in symbols
        }
        for future in as_completed(futures):
            outcome = future.result()
            outcomes.append(outcome)
            if outcome.candidate:
                candidates.append(outcome.candidate)

    ok = sum(1 for o in outcomes if not o.error)
    published = sum(1 for o in outcomes if o.published)
    failed = len(outcomes) - ok
    elapsed = time.perf_counter() - started

    logger.info(
        "monthly_scan: %d symbols, %d ok, %d failed, %d published, %.1fs",
        len(outcomes), ok, failed, published, elapsed,
    )

    if writer is not None and run is not None:
        failure_rate = failed / len(outcomes) if outcomes else 0.0
        status = "ok" if failure_rate == 0 else ("partial" if failure_rate < 0.2 else "failed")
        writer.finish_run(
            run,
            symbols_total=len(outcomes),
            symbols_ok=ok,
            symbols_failed=failed,
            llm_provider=settings.llm_provider,
            status=status,
        )

    # Sort by rank_score descending
    candidates.sort(key=lambda c: c.rank_score, reverse=True)
    stats = {"ok": ok, "published": published, "failed": failed, "elapsed": elapsed}
    return candidates, stats


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def write_markdown_report(
    bullish: list[MonthlyCandidate],
    bearish: list[MonthlyCandidate],
    top_n: int,
) -> Path:
    """Write a markdown report to docs/ with Bullish and Bearish sections."""
    output_file = OUTPUT_DIR / f"monthly_candidates_{datetime.date.today().strftime('%Y%m%d')}.md"
    output_file.parent.mkdir(parents=True, exist_ok=True)

    sector_map = load_sector_mapping()
    top_bullish = bullish[:top_n]
    top_bearish = bearish[:top_n]

    with open(output_file, "w") as f:
        f.write("# One-Month Candidate Report\n\n")
        f.write(f"**Scanned:** {datetime.datetime.now().isoformat()}\n")
        f.write(f"**Total bullish candidates:** {len(bullish)}\n")
        f.write(f"**Total bearish candidates:** {len(bearish)}\n")
        f.write(f"**Top {top_n} of each side listed below**\n\n")

        # Helper to write a table section
        def write_section(title, candidates):
            f.write(f"## {title}\n\n")
            f.write("| Rank | Ticker | Confidence | Confluence | Bull | Bear | Signals | Data Quality | Sector |\n")
            f.write("|------|--------|------------|------------|------|------|---------|-------------|--------|\n")
            for i, cand in enumerate(candidates, 1):
                sector = sector_map.get(cand.ticker, "N/A")
                conf_pct = f"{cand.confidence*100:.1f}%" if cand.confidence is not None else "N/A"
                conf_score = f"{cand.confluence_score:.3f}"
                dq = f"{cand.data_quality_score:.2f}" if cand.data_quality_score is not None else "N/A"
                f.write(
                    f"| {i} | {cand.ticker} | {conf_pct} | {conf_score} | {cand.bull_count} "
                    f"| {cand.bear_count} | {cand.total_signals} | {dq} | {sector} |\n"
                )
            f.write("\n")

        write_section("Bullish Candidates", top_bullish)
        write_section("Bearish Candidates", top_bearish)

    logger.info("Wrote monthly report to %s", output_file)
    return output_file


def print_summary(
    bullish: list[MonthlyCandidate],
    bearish: list[MonthlyCandidate],
    top_n: int,
) -> None:
    """Print a ranked table to the console."""
    print(f"\n📅 One-Month Candidate Report (top {top_n} each side)\n")
    print("## Bullish Candidates\n")
    print("| Rank | Ticker | Confidence | Confluence | Bull | Bear | Signals |")
    print("|------|--------|------------|------------|------|------|---------|")
    for i, cand in enumerate(bullish[:top_n], 1):
        conf_pct = f"{cand.confidence*100:.1f}%" if cand.confidence is not None else "N/A"
        conf_score = f"{cand.confluence_score:.3f}"
        print(f"| {i:2d} | {cand.ticker:6s} | {conf_pct:>10s} | {conf_score:>10s} | "
              f"{cand.bull_count:4d} | {cand.bear_count:4d} | {cand.total_signals:7d} |")

    print("\n## Bearish Candidates\n")
    print("| Rank | Ticker | Confidence | Confluence | Bull | Bear | Signals |")
    print("|------|--------|------------|------------|------|------|---------|")
    for i, cand in enumerate(bearish[:top_n], 1):
        conf_pct = f"{cand.confidence*100:.1f}%" if cand.confidence is not None else "N/A"
        conf_score = f"{cand.confluence_score:.3f}"
        print(f"| {i:2d} | {cand.ticker:6s} | {conf_pct:>10s} | {conf_score:>10s} | "
              f"{cand.bull_count:4d} | {cand.bear_count:4d} | {cand.total_signals:7d} |")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--seed", default="seed/universe_symbols.csv",
                        help="Seed CSV with a 'ticker' column")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_N,
                        help="Number of top candidates to report for each side (default: 50)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip LLM synthesis and all writes")
    parser.add_argument("--direction", choices=["bullish", "bearish", "both"],
                        default="both",
                        help="Gate direction (default: both). 'both' produces two ranked lists.")
    parser.add_argument("--skip-calibration", action="store_true",
                        help="Skip 21d backtest and reuse existing calibration file")
    parser.add_argument("--calibration-symbols", nargs="+", default=None,
                        help="Custom symbol list for 21d calibration")
    parser.add_argument("--calibration-period", default=DEFAULT_CALIBRATION_PERIOD,
                        help="History period for calibration backtest")
    parser.add_argument("--period", default=DEFAULT_SCAN_PERIOD,
                        help="yfinance period for the primary scan timeframe")
    parser.add_argument("--write-supabase", action="store_true",
                        help="Persist gated signals to Supabase")
    parser.add_argument("--trigger", default="manual", choices=["cron", "manual", "backfill"],
                        help="Trigger recorded on engine_runs")
    parser.add_argument("--max-concurrent", type=int, default=MAX_CONCURRENT_FETCHES,
                        help="Bounded fetch concurrency")
    parser.add_argument("--matrix", action="store_true",
                        help="Also build the 5-timeframe matrix for gated symbols")
    parser.add_argument("--shard", metavar="INDEX/TOTAL", default=None,
                        help="Process only every TOTAL-th symbol starting at INDEX, e.g. '0/4'")
    parser.add_argument("--limit", type=int, default=None,
                        help="Cap number of symbols scanned")
    args = parser.parse_args()

    # Load symbols
    try:
        symbols = load_symbols_from_csv(args.seed)
    except FileNotFoundError:
        parser.error(f"Seed file not found: {args.seed}")

    symbols = sorted(set(symbols))
    if args.shard:
        try:
            shard_index, shard_total = parse_shard_spec(args.shard)
        except ValueError as exc:
            parser.error(str(exc))
        symbols = apply_shard(symbols, shard_index, shard_total)

    if args.limit:
        symbols = symbols[: args.limit]

    logger.info("Loaded %d symbols", len(symbols))

    # Calibration (21d horizon)
    if not args.skip_calibration:
        cal_symbols = args.calibration_symbols or symbols
        strength_hit_rates = _ensure_calibration(cal_symbols, period=args.calibration_period)
        if strength_hit_rates is None:
            logger.warning("Proceeding with uncalibrated confluence scores")
    else:
        strength_hit_rates = _ensure_calibration(symbols, period=args.calibration_period, skip=True)
        if strength_hit_rates is None:
            logger.warning("No calibration file found; proceeding uncalibrated")

    # Direction handling
    gate_direction = None if args.direction == "both" else args.direction

    # Optional Supabase writer
    writer = None
    if args.write_supabase and not args.dry_run:
        writer = SupabaseWriter()

    try:
        candidates, stats = scan_monthly_universe(
            symbols=symbols,
            period=args.period,
            writer=writer,
            trigger=args.trigger,
            dry_run=args.dry_run,
            max_concurrent=args.max_concurrent,
            compute_matrix=args.matrix,
            direction=gate_direction,
            strength_hit_rates=strength_hit_rates,
        )
    finally:
        if writer is not None:
            writer.close()

    # Split candidates into bullish and bearish based on confluence sign.
    # If a specific direction was forced, one side will be empty.
    bullish = [c for c in candidates if c.confluence_score > 0]
    bearish = [c for c in candidates if c.confluence_score < 0]
    # If direction was forced, the other side should be empty already.
    # Sort each side by rank_score descending (already sorted globally, but ensure).
    bullish.sort(key=lambda c: c.rank_score, reverse=True)
    bearish.sort(key=lambda c: c.rank_score, reverse=True)

    # Report
    output_path = write_markdown_report(bullish, bearish, top_n=args.top)
    print_summary(bullish, bearish, top_n=args.top)

    print(f"\nStats: {stats['ok']} ok, {stats['failed']} failed, "
          f"{stats['published']} published, {stats['elapsed']:.1f}s")
    print(f"Results written to {output_path}")


if __name__ == "__main__":
    main()
