#!/usr/bin/env python3
"""Best-1 scan for the Signals App.

DEPRECATED (design doc §3.6). The both-direction screen this runs is now
`signals scan --preset best1`. This script still works — it keeps the top-N
confluence ranking the preset doesn't replicate yet — but new usage should
prefer the preset. Not archived: still referenced by tests/test_best1_scan.py.

This script intentionally follows the same L1 → L5 app pipeline as
`scan_universe.py`, but narrows the final output to the top `top_n` published
symbols by confluence score.

The important integration detail is that it uses the same `SignalWriter`
protocol and the same Supabase write path as the rest of the app, so it can
persist results into `engine_runs` and `signals` without inventing a second
storage layer.
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

_script_dir = Path(__file__).resolve().parent
_project_root = _script_dir.parent
sys.path.insert(0, str(_project_root / "src"))
sys.path.insert(0, str(_project_root))

from signals_app.config import DEFAULT_PERIOD, get_settings  # noqa: E402
from signals_app.data.fetcher import DataFetcher  # noqa: E402
from signals_app.db.supabase import (  # noqa: E402
    EngineRun,
    SignalRecord,
    SignalWriter,
    SupabaseWriter,
    confluence_result_to_signal_record,
)
from signals_app.detection.orchestrator import detect_all_signals  # noqa: E402
from signals_app.indicators.compute import compute_indicators  # noqa: E402
from signals_app.indicators.data_quality import score_data_quality  # noqa: E402
from signals_app.scoring.calibration import load_strength_hit_rates_from_supabase  # noqa: E402
from signals_app.scoring.confluence import ConfluenceRanker  # noqa: E402
from scripts.scan_universe import passes_publication_gate  # noqa: E402

logger = logging.getLogger(__name__)


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=_project_root, text=True
        ).strip()
    except Exception:
        return "unknown"


def evaluate_symbol(
    ticker: str,
    period: str = DEFAULT_PERIOD,
    writer: SignalWriter | None = None,
    run: EngineRun | None = None,
    settings: Any | None = None,
    dry_run: bool = False,
    direction: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Evaluate one ticker and return the published record if it clears the gate.

    The function mirrors `scan_one_symbol()` in `scripts/scan_universe.py`, but it
    is intentionally narrower: it returns the winner metadata and only persists
    the final record when `persist=True`.
    """
    settings = settings or get_settings()
    try:
        fetcher = DataFetcher(settings=settings)
        ohlcv = fetcher.fetch(ticker, period)
        if len(ohlcv.df) < 20:
            return {"ticker": ticker, "published": False, "record": None, "confluence_score": 0.0}

        data_quality = score_data_quality(ohlcv.df, period)
        df = compute_indicators(ohlcv.df)
        signal_list = detect_all_signals(df)
        ranker = ConfluenceRanker()
        strength_hit_rates = load_strength_hit_rates_from_supabase()
        confluence = ranker.rank_signals(list(signal_list), strength_hit_rates=strength_hit_rates)

        bar_ts = df.index[-1].isoformat()

        if writer is not None and not dry_run and persist:
            writer.ensure_symbol(ticker)
            writer.write_detector_hits(ticker, bar_ts, list(signal_list))

        if not passes_publication_gate(
            data_quality.score,
            len(signal_list),
            confluence.score,
            signal_list.degraded,
            direction=direction,
        ):
            return {
                "ticker": ticker,
                "published": False,
                "record": None,
                "confluence_score": confluence.score,
                "direction": None,
                "confidence": None,
            }

        ai_direction: str | None = None
        confidence: float | None = None
        evidence: list[dict[str, Any]] = []
        counter_evidence: list[dict[str, Any]] = []
        ai_degraded = False
        prompt_version: str | None = None

        if not dry_run:
            from signals_app.synthesis.mtf_llm import synthesize_single

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
                    if v == v and abs(v) != float("inf"):
                        features[col.lower()] = round(v, 4)
                except Exception:
                    pass

            signal = synthesize_single(
                ticker=ticker,
                timeframe="1D",
                features=features,
                settings=settings,
            )
            ai_direction = signal.direction.value
            confidence = signal.confidence
            ai_degraded = signal.ai_degraded
            prompt_version = signal.prompt_version
            evidence = [e.model_dump(mode="json") for e in signal.evidence.items if not e.is_counter]
            counter_evidence = [
                e.model_dump(mode="json") for e in signal.evidence.items if e.is_counter
            ]
        else:
            ai_direction = confluence.bias
            confidence = round(abs(confluence.score), 4)

        record = confluence_result_to_signal_record(
            ticker=ticker,
            period=period,
            bar_ts=bar_ts,
            confluence=confluence,
            data_quality_score=data_quality.score,
            data_quality_reasons=data_quality.reasons,
            direction=ai_direction,
            confidence=confidence,
            evidence=evidence,
            counter_evidence=counter_evidence,
            matrix=None,
            ai_degraded=ai_degraded,
            no_llm=dry_run,
            prompt_version=prompt_version,
            llm_model=None,
        )

        if writer is not None and run is not None and persist and not dry_run:
            writer.write_signal(run, record)

        return {
            "ticker": ticker,
            "published": True,
            "confluence_score": confluence.score,
            "direction": ai_direction or confluence.bias,
            "confidence": confidence,
            "record": record,
        }
    except Exception as exc:
        logger.warning("best1_scan: %s failed: %s", ticker, exc)
        return {"ticker": ticker, "published": False, "record": None, "confluence_score": 0.0, "error": str(exc)}


def run_best1_scan(
    symbols: list[str],
    writer: SignalWriter | None = None,
    top_n: int = 1,
    trigger: str = "manual",
    dry_run: bool = False,
    period: str = DEFAULT_PERIOD,
    direction: str | None = None,
) -> list[dict[str, Any]]:
    """Run the best-1 scan and return the winning published symbols.

    This keeps the same app contract as `scan_universe.py`: it starts a run,
    writes through `SignalWriter`, and finishes it once the winners are chosen.
    """
    if not symbols:
        return []

    settings = get_settings()
    run: EngineRun | None = None
    if writer is not None and not dry_run:
        run = writer.start_run(trigger=trigger, git_sha=_git_sha())

    evaluated = [
        evaluate_symbol(
            ticker=ticker,
            period=period,
            writer=None,
            run=None,
            settings=settings,
            dry_run=dry_run,
            direction=direction,
            persist=False,
        )
        for ticker in symbols
    ]
    published = [item for item in evaluated if item.get("published")]
    published.sort(
        key=lambda item: (
            float(item.get("confluence_score") or 0.0),
            float(item.get("confidence") or 0.0),
        ),
        reverse=True,
    )
    winners = published[:max(1, top_n)]

    if writer is not None and run is not None and not dry_run:
        for winner in winners:
            record = winner.get("record")
            if record is None:
                continue
            writer.ensure_symbol(record.ticker)
            writer.write_signal(run, record)

    if writer is not None and run is not None:
        ok = len(winners)
        failed = max(0, len(symbols) - ok)
        writer.finish_run(
            run,
            symbols_total=len(symbols),
            symbols_ok=ok,
            symbols_failed=failed,
            llm_provider=settings.llm_provider,
            status="ok" if failed == 0 else ("partial" if failed < len(symbols) else "failed"),
        )

    return winners


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbols", nargs="*", help="Ticker symbols to evaluate")
    parser.add_argument("--top-n", type=int, default=1, help="How many published winners to keep")
    parser.add_argument("--period", default=DEFAULT_PERIOD)
    parser.add_argument("--trigger", default="manual", choices=["cron", "manual", "backfill"])
    parser.add_argument("--direction", choices=["bullish", "bearish"], default=None)
    parser.add_argument("--dry-run", action="store_true", help="Compute and rank outputs but do not write to Supabase")
    args = parser.parse_args()

    symbols = [s.upper() for s in args.symbols]
    if not symbols:
        parser.error("no symbols given")

    writer = None if args.dry_run else SupabaseWriter()
    results = run_best1_scan(
        symbols=symbols,
        writer=writer,
        top_n=args.top_n,
        trigger=args.trigger,
        dry_run=args.dry_run,
        period=args.period,
        direction=args.direction,
    )

    for item in results:
        logger.info(
            "%s published=%s score=%.4f confidence=%s",
            item["ticker"],
            item["published"],
            item.get("confluence_score", 0.0),
            item.get("confidence"),
        )


if __name__ == "__main__":
    main()
