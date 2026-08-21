#!/usr/bin/env python3
"""Optimal one-month candidate scanner — combines rolling-window confluence,
state/transition weighting, category diversity, and multi-timeframe context.

Produces a ranked report of bullish and bearish candidates (default: top 50 each)
written to docs/optimal_monthly_candidates_YYYYMMDD.md.

Usage:
    python scripts/scan_optimal_monthly.py                       # both directions, top 50 each
    python scripts/scan_optimal_monthly.py --dry-run             # no LLM/writes, fast preview
    python scripts/scan_optimal_monthly.py --direction bullish   # bullish only
    python scripts/scan_optimal_monthly.py --write-supabase      # persist to Supabase
    python scripts/scan_optimal_monthly.py --matrix              # enable MTF matrix for top candidates
    python scripts/scan_optimal_monthly.py --shard 0/4           # shard across workers
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
from signals_app.detection.historical import scan_historical
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
OUTPUT_DIR = _project_root / "docs"
HORIZON_DAYS = 21
DEFAULT_SCAN_PERIOD = "1y"
DEFAULT_CALIBRATION_PERIOD = "5y"
MAX_CONCURRENT_FETCHES = 4
DEFAULT_TOP_N = 50
WINDOW_SIZE = 10                      # number of recent bars for aggregation
MIN_CATEGORIES = 4                    # minimum distinct categories required
MAX_LLM_CANDIDATES = 200              # cap on LLM synthesis per run
TRANSITION_BONUS = 0.2                # added to score per transition signal
CATEGORY_BONUS = {
    "MA_CROSS": 0.5,
    "MACD": 0.5,
    "STOCHASTIC": 0.3,
    "ICHIMOKU": 0.3,
    "OBV_CMF": 0.3,
}

_PERIOD_TO_TIMEFRAME = {
    "1d": "1D", "5d": "5D", "1mo": "1M", "3mo": "3M", "6mo": "6M", "1y": "1Y",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class WindowMetrics:
    """Aggregated confluence metrics over the last N bars."""

    avg_score: float
    trend_score: float          # slope over window (last half - first half)
    bull_bars: int
    bear_bars: int
    total_bars: int
    category_diversity: int
    transition_count: int
    final_score: float          # combined deterministic score for ranking


@dataclass
class Candidate:
    """A symbol that passed the deterministic gate."""

    ticker: str
    window: WindowMetrics
    confluence_score: float      # latest bar raw confluence
    bull_count: int
    bear_count: int
    total_signals: int
    data_quality_score: float | None
    latest_signals: Any          # kept for LLM features
    bar_ts: str
    confidence: float | None = None
    direction: str | None = None
    sector: str = "N/A"
    record: Any = None           # SignalRecord for Supabase write

    @property
    def rank_score(self) -> float:
        return self.confidence if self.confidence is not None else self.window.final_score


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


def load_sector_mapping() -> dict[str, str]:
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


def _ensure_calibration(symbols, period=DEFAULT_CALIBRATION_PERIOD, skip=False):
    cal_path = Path(CALIBRATION_FILE_21D)
    if skip:
        if not cal_path.exists():
            logger.warning("--skip-calibration set but no 21d calibration file found")
            return None
        return load_strength_hit_rates(path=str(cal_path))
    if cal_path.exists():
        logger.info("Using existing 21d calibration at %s", cal_path)
        return load_strength_hit_rates(path=str(cal_path))
    logger.info("No 21d calibration found — running calibration on %d symbols", len(symbols))
    cal_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        rates = run_calibration(
            symbols=symbols,
            period=period,
            horizon_days=HORIZON_DAYS,
            output_path=str(cal_path),
        )
        return rates
    except RuntimeError as e:
        logger.error("Calibration failed: %s", e)
        return None


def is_transition_signal(signal_name: str) -> bool:
    """Heuristic: transition signals often contain these keywords."""
    transition_markers = ["CROSS", "BREAKOUT", "DIVERGENCE", "SPIKE", "TK "]
    return any(marker in signal_name.upper() for marker in transition_markers)


def enhance_confluence(confluence, signals: list) -> float:
    """Adjust base confluence score with category and transition bonuses."""
    base = confluence.score
    if base == 0:
        return 0.0
    sign = 1 if base > 0 else -1
    # Category bonus
    categories = set()
    transition_count = 0
    for sig in signals:
        cat = getattr(sig, "category", None)
        if cat:
            categories.add(cat)
            bonus = CATEGORY_BONUS.get(cat, 0.0)
            base += sign * bonus
        if is_transition_signal(sig.signal):
            transition_count += 1
    # Transition bonus
    base += sign * TRANSITION_BONUS * transition_count
    # Diversity penalty if too few categories
    if len(categories) < MIN_CATEGORIES:
        base *= 0.7  # reduce confidence
    return base


def compute_window_metrics(
    ticker: str,
    df: Any,
    period: str,
    settings: Any,
    strength_hit_rates: dict[str, float] | None,
) -> tuple[WindowMetrics | None, Any, float | None, int, int, int]:
    """Run historical scan, aggregate window confluence, and return metrics.

    Returns:
        (WindowMetrics or None, latest_signals, data_quality_score, bull_count, bear_count, total_signals)
    """
    data_quality = score_data_quality(df, period)
    df_indicators = compute_indicators(df)
    bars = scan_historical(df_indicators)
    if not bars:
        return None, None, data_quality.score, 0, 0, 0

    # Keep only the last WINDOW_SIZE bars that have signals
    bars_with_signals = [b for b in bars if b.signals]
    if not bars_with_signals:
        return None, None, data_quality.score, 0, 0, 0
    recent_bars = bars_with_signals[-WINDOW_SIZE:]

    ranker = ConfluenceRanker()
    scores = []
    bull_bars = 0
    bear_bars = 0
    transition_counts = []
    category_set = set()
    latest_signals = None
    latest_bull = latest_bear = latest_total = 0

    for i, bar in enumerate(recent_bars):
        signals = list(bar.signals)
        confluence = ranker.rank_signals(signals, strength_hit_rates=strength_hit_rates)
        enhanced = enhance_confluence(confluence, signals)
        scores.append(enhanced)
        if enhanced > 0.35:
            bull_bars += 1
        elif enhanced < -0.35:
            bear_bars += 1
        transition_counts.append(sum(1 for s in signals if is_transition_signal(s.signal)))
        for s in signals:
            if s.category:
                category_set.add(s.category)
        if i == len(recent_bars) - 1:
            latest_signals = signals
            latest_bull = confluence.bull_count
            latest_bear = confluence.bear_count
            latest_total = len(signals)

    avg_score = sum(scores) / len(scores)
    # Trend: difference between mean of last half and mean of first half
    half = len(scores) // 2
    first_half = scores[:half]
    second_half = scores[half:]
    trend_score = (sum(second_half) / len(second_half)) - (sum(first_half) / len(first_half)) if half > 0 else 0.0
    transition_count_total = sum(transition_counts)
    category_diversity = len(category_set)

    # Final deterministic score: combine average, trend, and transition activity
    final_score = avg_score + 0.5 * trend_score + 0.1 * (transition_count_total / len(scores))

    metrics = WindowMetrics(
        avg_score=avg_score,
        trend_score=trend_score,
        bull_bars=bull_bars,
        bear_bars=bear_bars,
        total_bars=len(scores),
        category_diversity=category_diversity,
        transition_count=transition_count_total,
        final_score=final_score,
    )
    return metrics, latest_signals, data_quality.score, latest_bull, latest_bear, latest_total


# ---------------------------------------------------------------------------
# Per-symbol scanning
# ---------------------------------------------------------------------------
def scan_symbol(
    ticker: str,
    period: str,
    settings: Any,
    strength_hit_rates: dict[str, float] | None,
) -> Candidate | None:
    """Fetch, compute rolling window metrics, and gate. Returns Candidate if passed."""
    try:
        fetcher = DataFetcher(settings=settings)
        ohlcv = fetcher.fetch(ticker, period)
        if len(ohlcv.df) < 20:
            return None

        metrics, latest_signals, dq_score, bull, bear, total = compute_window_metrics(
            ticker, ohlcv.df, period, settings, strength_hit_rates
        )
        if metrics is None or latest_signals is None:
            return None

        # Gate: require sufficient data quality, signal count, and category diversity
        if dq_score is None or dq_score < 0.6:  # approximate PUBLISH_MIN_DATA_QUALITY
            return None
        if total < 5:  # PUBLISH_MIN_SIGNALS
            return None
        if metrics.category_diversity < MIN_CATEGORIES:
            return None
        # Direction gate: absolute score must clear threshold
        if abs(metrics.final_score) < 0.35:
            return None

        # Determine direction from final_score sign
        direction = "bullish" if metrics.final_score > 0 else "bearish"
        bar_ts = ohlcv.df.index[-1].isoformat()

        return Candidate(
            ticker=ticker,
            window=metrics,
            confluence_score=metrics.avg_score,  # or use latest bar raw? we'll use window avg
            bull_count=bull,
            bear_count=bear,
            total_signals=total,
            data_quality_score=dq_score,
            latest_signals=latest_signals,
            bar_ts=bar_ts,
            direction=direction,
        )
    except Exception as exc:
        logger.warning("optimal_scan: %s failed: %s", ticker, exc)
        return None


# ---------------------------------------------------------------------------
# Main scan orchestration
# ---------------------------------------------------------------------------
def scan_optimal_universe(
    symbols: list[str],
    period: str = DEFAULT_SCAN_PERIOD,
    writer: SignalWriter | None = None,
    trigger: str = "manual",
    dry_run: bool = False,
    max_concurrent: int = MAX_CONCURRENT_FETCHES,
    compute_matrix: bool = False,
    direction_gate: str | None = None,  # "bullish", "bearish", or None for both
    strength_hit_rates: dict[str, float] | None = None,
    max_llm_candidates: int = MAX_LLM_CANDIDATES,
) -> tuple[list[Candidate], dict[str, Any]]:
    settings = get_settings()
    run: EngineRun | None = None
    if writer is not None and not dry_run:
        run = writer.start_run(trigger=trigger, git_sha=_git_sha())

    candidates: list[Candidate] = []
    started = time.perf_counter()

    with ThreadPoolExecutor(max_workers=max_concurrent) as pool:
        futures = {
            pool.submit(scan_symbol, t, period, settings, strength_hit_rates): t
            for t in symbols
        }
        for future in as_completed(futures):
            cand = future.result()
            if cand:
                # Apply direction filter if specified
                if direction_gate == "bullish" and cand.window.final_score < 0:
                    continue
                if direction_gate == "bearish" and cand.window.final_score > 0:
                    continue
                candidates.append(cand)

    # Sort by deterministic window score
    candidates.sort(key=lambda c: c.window.final_score, reverse=True)

    ok = sum(1 for c in candidates if c is not None)
    published = len(candidates)
    failed = len(symbols) - ok
    elapsed = time.perf_counter() - started
    logger.info(
        "optimal_scan: %d symbols, %d passed deterministic gate, %.1fs",
        len(symbols), published, elapsed,
    )

    # LLM synthesis for top candidates only
    llm_applied = 0
    if not dry_run and candidates:
        top_for_llm = candidates[:max_llm_candidates]
        for cand in top_for_llm:
            try:
                df = DataFetcher(settings=settings).fetch(cand.ticker, period).df
                df = compute_indicators(df)
                current = df.iloc[-1]
                features = {
                    "symbol": cand.ticker,
                    "period": period,
                    "confluence_score": cand.confluence_score,
                    "bias": "bullish" if cand.window.final_score > 0 else "bearish",
                    "action": "BUY" if cand.window.final_score > 0 else "SELL",
                    "bull_count": cand.bull_count,
                    "bear_count": cand.bear_count,
                    "total_signals": cand.total_signals,
                }
                for col in ["RSI", "MACD", "ADX", "Close", "ATR", "Price_Change"]:
                    try:
                        v = float(current[col])
                        if v == v and abs(v) != float("inf"):
                            features[col.lower()] = round(v, 4)
                    except Exception:
                        pass
                signal = synthesize_single(
                    ticker=cand.ticker,
                    timeframe=_PERIOD_TO_TIMEFRAME.get(period, "1D"),
                    features=features,
                    settings=settings,
                )
                cand.confidence = signal.confidence
                cand.direction = signal.direction.value
                cand.record = confluence_result_to_signal_record(
                    ticker=cand.ticker,
                    period=period,
                    bar_ts=cand.bar_ts,
                    confluence=ConfluenceRanker().rank_signals(
                        list(cand.latest_signals), strength_hit_rates=strength_hit_rates
                    ),
                    data_quality_score=cand.data_quality_score,
                    data_quality_reasons=[],
                    direction=cand.direction,
                    confidence=cand.confidence,
                    evidence=[e.model_dump(mode="json") for e in signal.evidence.items if not e.is_counter],
                    counter_evidence=[e.model_dump(mode="json") for e in signal.evidence.items if e.is_counter],
                    matrix=None,
                    ai_degraded=signal.ai_degraded,
                    no_llm=False,
                    prompt_version=signal.prompt_version,
                )
                if writer is not None and run is not None:
                    writer.ensure_symbol(cand.ticker)
                    writer.write_signal(run, cand.record)
                llm_applied += 1
            except Exception as exc:
                logger.warning("LLM synthesis failed for %s: %s", cand.ticker, exc)

        # Optional matrix for top 10 after LLM
        if compute_matrix:
            for cand in candidates[:10]:
                try:
                    matrix = build_matrix_for_symbol(cand.ticker, settings)
                    if cand.record and matrix:
                        cand.record.matrix = matrix
                        # re-write updated record
                        if writer is not None and run is not None:
                            writer.write_signal(run, cand.record)
                except Exception as exc:
                    logger.warning("matrix: %s failed: %s", cand.ticker, exc)

    # Final sort by confidence if available
    if not dry_run:
        candidates.sort(key=lambda c: c.rank_score, reverse=True)

    if writer is not None and run is not None:
        writer.finish_run(
            run,
            symbols_total=len(symbols),
            symbols_ok=ok,
            symbols_failed=failed,
            llm_provider=settings.llm_provider,
            status="ok" if failed == 0 else "partial",
        )

    stats = {"ok": ok, "published": published, "failed": failed, "elapsed": elapsed, "llm_applied": llm_applied}
    return candidates, stats


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def write_markdown_report(bullish, bearish, top_n: int) -> Path:
    output_file = OUTPUT_DIR / f"optimal_monthly_candidates_{datetime.date.today().strftime('%Y%m%d')}.md"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    sector_map = load_sector_mapping()

    with open(output_file, "w") as f:
        f.write("# Optimal One-Month Candidate Report\n\n")
        f.write(f"**Scanned:** {datetime.datetime.now().isoformat()}\n")
        f.write(f"**Total bullish candidates:** {len(bullish)}\n")
        f.write(f"**Total bearish candidates:** {len(bearish)}\n")
        f.write(f"**Top {top_n} each side listed below**\n\n")

        def write_section(title, candidates):
            f.write(f"## {title}\n\n")
            f.write("| Rank | Ticker | Confidence | Window Score | Bull | Bear | Signals | Categories | Transitions | Sector |\n")
            f.write("|------|--------|------------|--------------|------|------|---------|------------|-------------|--------|\n")
            for i, cand in enumerate(candidates, 1):
                sector = sector_map.get(cand.ticker, "N/A")
                conf_pct = f"{cand.confidence*100:.1f}%" if cand.confidence is not None else "N/A"
                f.write(
                    f"| {i} | {cand.ticker} | {conf_pct} | {cand.window.final_score:.3f} "
                    f"| {cand.bull_count} | {cand.bear_count} | {cand.total_signals} "
                    f"| {cand.window.category_diversity} | {cand.window.transition_count} | {sector} |\n"
                )
            f.write("\n")

        write_section("Bullish Candidates", bullish[:top_n])
        write_section("Bearish Candidates", bearish[:top_n])

    logger.info("Wrote optimal report to %s", output_file)
    return output_file


def print_summary(bullish, bearish, top_n):
    print(f"\n📅 Optimal One-Month Candidates (top {top_n} each side)\n")
    print("## Bullish\n")
    print("| Rank | Ticker | Confidence | Window Score | Bull | Bear | Signals |")
    print("|------|--------|------------|--------------|------|------|---------|")
    for i, cand in enumerate(bullish[:top_n], 1):
        conf = f"{cand.confidence*100:.1f}%" if cand.confidence else "N/A"
        print(f"| {i:2d} | {cand.ticker:6s} | {conf:>10s} | {cand.window.final_score:>12.3f} | "
              f"{cand.bull_count:4d} | {cand.bear_count:4d} | {cand.total_signals:7d} |")
    print("\n## Bearish\n")
    print("| Rank | Ticker | Confidence | Window Score | Bull | Bear | Signals |")
    print("|------|--------|------------|--------------|------|------|---------|")
    for i, cand in enumerate(bearish[:top_n], 1):
        conf = f"{cand.confidence*100:.1f}%" if cand.confidence else "N/A"
        print(f"| {i:2d} | {cand.ticker:6s} | {conf:>10s} | {cand.window.final_score:>12.3f} | "
              f"{cand.bull_count:4d} | {cand.bear_count:4d} | {cand.total_signals:7d} |")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", default="seed/universe_symbols.csv")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_N)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--direction", choices=["bullish", "bearish", "both"], default="both")
    parser.add_argument("--skip-calibration", action="store_true")
    parser.add_argument("--calibration-symbols", nargs="+", default=None)
    parser.add_argument("--calibration-period", default=DEFAULT_CALIBRATION_PERIOD)
    parser.add_argument("--period", default=DEFAULT_SCAN_PERIOD)
    parser.add_argument("--write-supabase", action="store_true")
    parser.add_argument("--trigger", default="manual", choices=["cron", "manual", "backfill"])
    parser.add_argument("--max-concurrent", type=int, default=MAX_CONCURRENT_FETCHES)
    parser.add_argument("--matrix", action="store_true")
    parser.add_argument("--shard", metavar="INDEX/TOTAL")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-llm-candidates", type=int, default=MAX_LLM_CANDIDATES)
    args = parser.parse_args()

    symbols = load_symbols_from_csv(args.seed)
    symbols = sorted(set(symbols))
    if args.shard:
        shard_index, shard_total = parse_shard_spec(args.shard)
        symbols = apply_shard(symbols, shard_index, shard_total)
    if args.limit:
        symbols = symbols[:args.limit]
    logger.info("Loaded %d symbols", len(symbols))

    # Calibration
    if not args.skip_calibration:
        cal_symbols = args.calibration_symbols or symbols
        strength_hit_rates = _ensure_calibration(cal_symbols, args.calibration_period)
    else:
        strength_hit_rates = _ensure_calibration(symbols, args.calibration_period, skip=True)
    if strength_hit_rates is None:
        logger.warning("Proceeding without calibration weights")

    # Direction
    gate_direction = None if args.direction == "both" else args.direction

    # Supabase writer
    writer = None
    if args.write_supabase and not args.dry_run:
        writer = SupabaseWriter()

    try:
        candidates, stats = scan_optimal_universe(
            symbols=symbols,
            period=args.period,
            writer=writer,
            trigger=args.trigger,
            dry_run=args.dry_run,
            max_concurrent=args.max_concurrent,
            compute_matrix=args.matrix,
            direction_gate=gate_direction,
            strength_hit_rates=strength_hit_rates,
            max_llm_candidates=args.max_llm_candidates,
        )
    finally:
        if writer:
            writer.close()

    # Split
    bullish = [c for c in candidates if c.window.final_score > 0]
    bearish = [c for c in candidates if c.window.final_score < 0]
    bullish.sort(key=lambda c: c.rank_score, reverse=True)
    bearish.sort(key=lambda c: c.rank_score, reverse=True)

    # Report
    output_path = write_markdown_report(bullish, bearish, args.top)
    print_summary(bullish, bearish, args.top)

    print(f"\nStats: {stats['ok']} passed gate, {stats['llm_applied']} LLM synth, {stats['failed']} failed, {stats['elapsed']:.1f}s")
    print(f"Report: {output_path}")


if __name__ == "__main__":
    main()