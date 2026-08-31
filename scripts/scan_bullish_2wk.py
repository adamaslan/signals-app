#!/usr/bin/env python3
"""Bullish 2-week candidate scanner.

DEPRECATED (design doc §3.6). The bullish + 10-day-horizon screen is now
`signals scan --preset bullish-2wk`. This script keeps the auto-calibration
step and confidence ranking the preset doesn't do yet; fold those in and
archive this in a follow-up PR.

Scans the universe for symbols that are:
1. Passing the publication gate on the bullish side only (confluence_score > 0.35)
2. Ranked by LLM confidence (calibrated against 10-day forward returns)

The 10-day forward-return horizon (~2 trading weeks) is the calibration baseline.
Symbols are ranked by confidence, with the most promising bullish candidates listed first.

Usage:
    python scripts/scan_bullish_2wk.py                           # scan full universe, top 20
    python scripts/scan_bullish_2wk.py --top 30 --dry-run         # dry-run, top 30
    python scripts/scan_bullish_2wk.py --calibration-symbols AAPL MSFT SPY    # custom calibration set
    python scripts/scan_bullish_2wk.py --skip-calibration         # reuse existing 10d calibration file

The script will:
1. Calibrate at 10-day horizon if no recent calibration exists (unless --skip-calibration)
2. Scan the universe with --direction bullish
3. Rank results by LLM confidence (descending)
4. Print and write the top N to a markdown file (calibration/bullish_2wk_candidates_YYYYMMDD.md)
"""
from __future__ import annotations

import argparse
import csv
import datetime
import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_script_dir = Path(__file__).resolve().parent
_project_root = _script_dir.parent
sys.path.insert(0, str(_project_root / "src"))
sys.path.insert(0, str(_project_root))

from scripts.calibrate import run_calibration  # noqa: E402
from scripts.scan_universe import scan_one_symbol, load_symbols_from_csv  # noqa: E402
from signals_app.config import (  # noqa: E402
    DEFAULT_PERIOD,
    get_settings,
)
from signals_app.data.fetcher import DataFetcher  # noqa: E402
from signals_app.indicators.compute import compute_indicators  # noqa: E402
from signals_app.detection.orchestrator import detect_all_signals  # noqa: E402
from signals_app.scoring.confluence import ConfluenceRanker  # noqa: E402
from signals_app.scoring.calibration import load_strength_hit_rates  # noqa: E402

logger = logging.getLogger(__name__)

CALIBRATION_FILE_10D = "./calibration/strength_hit_rates_10d.json"
OUTPUT_DIR = Path("calibration")


@dataclass
class BullishCandidate:
    """A bullish signal with ranking metadata."""

    ticker: str
    confluence_score: float
    confidence_label: str  # "HIGH", "MEDIUM", "LOW"
    bull_count: int
    bear_count: int
    sector: str = "N/A"

    # Map confidence label to numeric rank
    _LABEL_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}

    @property
    def rank_score(self) -> float:
        """Higher is better — use confidence_label rank, then confluence_score as tiebreaker."""
        return self._LABEL_RANK.get(self.confidence_label, 0) + (self.confluence_score / 1000.0)


def _ensure_10d_calibration(
    symbols: list[str],
    period: str = "2y",
) -> dict[str, float] | None:
    """Ensure a 10-day-horizon calibration file exists.

    If it doesn't exist or is missing, runs calibration on the provided symbol
    basket and writes to CALIBRATION_FILE_10D.

    Args:
        symbols: Symbols to backtest for calibration.
        period: How much history to use for backtest.

    Returns:
        The loaded calibration table (strength -> hit_rate), or None if calibration failed.
    """
    cal_path = Path(CALIBRATION_FILE_10D)

    # If it exists, assume it's current enough (skip_calibration covers the override).
    if cal_path.exists():
        logger.info("Using existing 10-day calibration at %s", cal_path)
        return load_strength_hit_rates(path=str(cal_path))

    logger.info("No 10-day calibration found — running calibration on %d symbols", len(symbols))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        rates = run_calibration(
            symbols=symbols,
            period=period,
            horizon_days=10,
            output_path=str(cal_path),
        )
        logger.info("Calibration complete: wrote %s", cal_path)
        return rates
    except RuntimeError as e:
        logger.error("Calibration failed: %s", e)
        return None


def scan_bullish_universe(
    symbols: list[str],
    period: str = DEFAULT_PERIOD,
    dry_run: bool = False,
    strength_hit_rates: dict[str, float] | None = None,
) -> list[BullishCandidate]:
    """Scan the universe for bullish signals only, ranked by confidence.

    Args:
        symbols: Tickers to scan.
        period: yfinance period for OHLCV data.
        dry_run: If True, skip LLM synthesis.
        strength_hit_rates: Pre-calibrated hit-rate table (loaded from 10d calibration).

    Returns:
        List of BullishCandidate, sorted by rank_score descending.
    """
    settings = get_settings()
    candidates: list[BullishCandidate] = []

    for ticker in symbols:
        try:
            fetcher = DataFetcher(settings=settings)
            ohlcv = fetcher.fetch(ticker, period)
            if len(ohlcv.df) < 20:
                logger.debug("bullish_2wk: %s insufficient bars (%d)", ticker, len(ohlcv.df))
                continue

            df = compute_indicators(ohlcv.df)
            signal_list = detect_all_signals(df)
            ranker = ConfluenceRanker()
            confluence = ranker.rank_signals(list(signal_list), strength_hit_rates=strength_hit_rates)

            # Gate: bullish side only
            if confluence.score < 0.35:  # Below bullish threshold
                logger.debug("bullish_2wk: %s confluence %.3f (not bullish)", ticker, confluence.score)
                continue

            candidate = BullishCandidate(
                ticker=ticker,
                confluence_score=confluence.score,
                confidence_label=confluence.confidence_label,
                bull_count=confluence.bull_count,
                bear_count=confluence.bear_count,
            )
            candidates.append(candidate)
            logger.info("bullish_2wk: %s passed gate — confluence=%.3f confidence_label=%s",
                        ticker, confluence.score, confluence.confidence_label)

        except Exception as exc:
            logger.warning("bullish_2wk: %s failed: %s", ticker, exc)

    # Sort by rank_score descending (highest confidence first)
    candidates.sort(key=lambda c: c.rank_score, reverse=True)
    return candidates


def load_sector_mapping() -> dict[str, str]:
    """Load ticker -> sector mapping from seed CSV if available."""
    sector_map: dict[str, str] = {}
    seed_path = Path(_project_root / "seed" / "universe_symbols.csv")
    if not seed_path.exists():
        return sector_map

    try:
        with open(seed_path, newline="") as f:
            for row in csv.DictReader(f):
                ticker = row.get("ticker", "").strip().upper()
                sector = row.get("sector_group", "N/A").strip()
                if ticker:
                    sector_map[ticker] = sector
    except Exception as e:
        logger.warning("Failed to load sector mapping: %s", e)

    return sector_map


def write_markdown_report(candidates: list[BullishCandidate], top_n: int) -> Path:
    """Write candidates to a markdown report."""
    output_file = OUTPUT_DIR / f"bullish_2wk_candidates_{datetime.date.today().strftime('%Y%m%d')}.md"
    output_file.parent.mkdir(parents=True, exist_ok=True)

    top_candidates = candidates[:top_n]
    sector_map = load_sector_mapping()

    with open(output_file, "w") as f:
        f.write("# Bullish 2-Week Candidates\n\n")
        f.write(f"**Scanned:** {datetime.datetime.now().isoformat()}\n")
        f.write(f"**Total bullish signals:** {len(candidates)}\n")
        f.write(f"**Top {top_n} listed below**\n\n")
        f.write("## Results\n\n")
        f.write("| Rank | Ticker | Confidence | Confluence | Bull | Bear | Sector |\n")
        f.write("|------|--------|------------|------------|----- |------|--------|\n")

        for i, cand in enumerate(top_candidates, 1):
            sector = sector_map.get(cand.ticker, "N/A")
            conf_score = f"{cand.confluence_score:.3f}"
            f.write(f"| {i} | {cand.ticker} | {cand.confidence_label} | {conf_score} | "
                    f"{cand.bull_count} | {cand.bear_count} | {sector} |\n")

    logger.info("Wrote results to %s", output_file)
    return output_file


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--calibration-symbols",
        nargs="+",
        default=None,
        help="Custom symbol list for 10-day calibration (default: use full universe)",
    )
    parser.add_argument(
        "--skip-calibration",
        action="store_true",
        help="Skip calibration — reuse existing strength_hit_rates_10d.json",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="Number of top candidates to report (default: 20)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip LLM synthesis; fast scan for preview",
    )
    parser.add_argument(
        "--seed",
        default="seed/universe_symbols.csv",
        help="Seed CSV with symbols to scan (default: seed/universe_symbols.csv)",
    )
    args = parser.parse_args()

    # Load symbols
    try:
        symbols = load_symbols_from_csv(args.seed)
    except FileNotFoundError:
        parser.error(f"Seed file not found: {args.seed}")

    symbols = sorted(set(symbols))
    logger.info("Loaded %d symbols from %s", len(symbols), args.seed)

    # Stage A: Calibration
    if not args.skip_calibration:
        cal_symbols = args.calibration_symbols or symbols  # Use full universe for calibration if not specified
        strength_hit_rates = _ensure_10d_calibration(cal_symbols)
        if strength_hit_rates is None:
            logger.warning("Calibration unavailable — will use uncalibrated confluence scores")
            strength_hit_rates = None
    else:
        strength_hit_rates = load_strength_hit_rates(path=CALIBRATION_FILE_10D)
        if strength_hit_rates is None:
            logger.warning("--skip-calibration set but no calibration file found; proceeding uncalibrated")

    # Stage B: Bullish scan
    logger.info("Scanning %d symbols for bullish signals (2-week horizon)...", len(symbols))
    candidates = scan_bullish_universe(
        symbols=symbols,
        period="1mo",  # 1 month lookback for 2-week forward horizon is reasonable
        dry_run=args.dry_run,
        strength_hit_rates=strength_hit_rates,
    )

    logger.info("Found %d bullish candidates", len(candidates))

    # Write markdown report
    output_path = write_markdown_report(candidates, top_n=args.top)

    # Print to console
    print(f"\n📈 Bullish 2-Week Candidates (top {args.top} of {len(candidates)} total)\n")
    print("| Rank | Ticker | Confidence | Confluence | Bull | Bear |")
    print("|------|--------|------------|------------|------|------|")
    for i, cand in enumerate(candidates[: args.top], 1):
        conf_score = f"{cand.confluence_score:.3f}"
        print(f"| {i:2d} | {cand.ticker:6s} | {cand.confidence_label:>10s} | {conf_score:>10s} | "
              f"{cand.bull_count:4d} | {cand.bear_count:4d} |")

    print(f"\nResults written to {output_path}")


if __name__ == "__main__":
    main()
