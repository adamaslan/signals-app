"""The universe scan — fetch → indicators → detect → confluence → gate →
(synthesize) → persist, over a list of tickers.

This module is the scan pipeline's home. It was lifted out of
``scripts/scan_universe.py`` so the scan is an *adapter over one place*
rather than a second hand-assembly of the layers (design doc §2.2). Both
``signals_app.service.scan()`` and ``scripts/scan_universe.py``'s ``main()``
now call ``scan_universe()`` here.

Design invariants (unchanged from the original script):

- The publication gate runs BEFORE LLM synthesis — an unpublishable signal
  never pays for a synthesis call.
- Per-symbol isolation: one bad ticker is a tallied ``SymbolResult``, never
  an aborted run.
- Bounded fetch concurrency (yfinance throttles under load).
- Dependency-injected ``SignalWriter`` so this is testable without Supabase.
"""
from __future__ import annotations

import csv
import logging
import subprocess
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from signals_app.config import (
    DEFAULT_PERIOD,
    PUBLISH_MIN_CONFLUENCE_SCORE,
    PUBLISH_MIN_DATA_QUALITY,
    PUBLISH_MIN_SIGNALS,
    get_settings,
)
from signals_app.data.fetcher import DataFetcher
from signals_app.db.supabase import (
    EngineRun,
    SignalWriter,
    confluence_result_to_signal_record,
)
from signals_app.detection.orchestrator import detect_all_signals
from signals_app.indicators.compute import compute_indicators
from signals_app.indicators.data_quality import score_data_quality
from signals_app.scoring.calibration import load_strength_hit_rates_from_supabase
from signals_app.scoring.confluence import ConfluenceRanker, ConfluenceResult
from signals_app.scoring.features import build_feature_row, continuous_features_frame
from signals_app.scoring.model import LogisticScorer, confidence_label, load_active_scorer
from signals_app.scoring.probability import rank_pct as compute_rank_pct
from signals_app.scoring.regime import current_regime
from signals_app.scoring.mtf import SUPPORTED_TIMEFRAMES

logger = logging.getLogger(__name__)

MAX_CONCURRENT_FETCHES = 4

# Interim gate (plan §8): independent families agreeing, in place of a raw signal
# count that a single fan-out detector could satisfy alone.
PUBLISH_MIN_FAMILIES = 2

# EV gate (plan §8): expected excess return must clear a flat round-trip cost.
EST_ROUND_TRIP_COST = 0.0010  # 10 bps
BENCHMARK_SYMBOL = "SPY"
MARKET_HISTORY_PERIOD = "2y"
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
    p_outperform: float | None = None
    rank_pct: float | None = None


# Repo root — two levels up from src/signals_app/scanner.py. Used only to run
# `git rev-parse` for the engine_runs provenance stamp; a wrong cwd just yields
# "unknown", never an error.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=_PROJECT_ROOT, text=True
        ).strip()
    except Exception:  # noqa: BLE001 — provenance stamp is best-effort
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
    direction: str | None = None,
    agreeing_families: int | None = None,
) -> bool:
    """The publication gate — see config.py's PUBLISH_MIN_* constants and
    docs/backend-state-and-supabase-plan.md Part 3 §3 ("Selective").

    Most ticker-days should fail this gate. That is the point: an engine
    that always emits a direction carries no information.

    Args:
        direction: None (default) gates on |confluence_score| — either
            direction publishes, matching current behavior. "bullish"
            requires confluence_score to clear the threshold on the positive
            side only; "bearish" requires it on the negative side only.
        agreeing_families: When given (family-based scoring), replaces the raw
            ``total_signals`` floor with "at least PUBLISH_MIN_FAMILIES
            independent families agree" — a signal count is satisfiable by one
            detector's fan-out, a family count is not.
    """
    if direction not in (None, "bullish", "bearish"):
        raise ValueError(f"direction must be None, 'bullish', or 'bearish', got {direction!r}")
    if data_quality_score is None or data_quality_score < PUBLISH_MIN_DATA_QUALITY:
        return False
    if agreeing_families is not None:
        if agreeing_families < PUBLISH_MIN_FAMILIES:
            return False
    elif total_signals < PUBLISH_MIN_SIGNALS:
        return False
    if direction == "bullish":
        if confluence_score < PUBLISH_MIN_CONFLUENCE_SCORE:
            return False
    elif direction == "bearish":
        if confluence_score > -PUBLISH_MIN_CONFLUENCE_SCORE:
            return False
    elif abs(confluence_score) < PUBLISH_MIN_CONFLUENCE_SCORE:
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


def passes_ev_gate(
    data_quality_score: float | None,
    p_outperform: float,
    expected_excess: float | None,
    delta: float,
    direction: str | None = None,
    est_cost: float = EST_ROUND_TRIP_COST,
) -> bool:
    """Expected-value publication gate for the learned scorer (plan §8).

    Publishes when the calibrated probability is at least ``delta`` from a coin
    flip, the expected excess return points the same way and exceeds the
    round-trip cost, and the data is good enough.

    Args:
        delta: Minimum |p - 0.5|; set at training time to hit the target
            publish rate (``LogisticScorer.publish_delta``).
        direction: None gates both sides; "bullish"/"bearish" one side only.
        est_cost: Flat round-trip cost as a return fraction.
    """
    if direction not in (None, "bullish", "bearish"):
        raise ValueError(f"direction must be None, 'bullish', or 'bearish', got {direction!r}")
    if data_quality_score is None or data_quality_score < PUBLISH_MIN_DATA_QUALITY:
        return False
    if expected_excess is None or expected_excess != expected_excess:
        return False
    edge = p_outperform - 0.5
    if abs(edge) < delta:
        return False
    if edge * expected_excess <= 0 or abs(expected_excess) <= est_cost:
        return False
    if direction == "bullish":
        return edge > 0
    if direction == "bearish":
        return edge < 0
    return True


@dataclass(frozen=True)
class MarketContext:
    """Benchmark data shared by every symbol in a scan run (fetched once)."""

    regime: str | None
    benchmark_close: Any | None


def load_market_context(settings: Any) -> MarketContext:
    """Fetch the benchmark once and label today's regime.

    Never raises: with no benchmark the model still scores, with the regime
    indicators zeroed and relative-strength imputed.
    """
    try:
        bench = DataFetcher(settings=settings).fetch_daily_history(BENCHMARK_SYMBOL, MARKET_HISTORY_PERIOD)
        return MarketContext(regime=current_regime(bench), benchmark_close=bench["Close"])
    except Exception as exc:  # noqa: BLE001 — market context is optional
        logger.warning("market context unavailable (%s) — scoring without regime", exc)
        return MarketContext(regime=None, benchmark_close=None)


@dataclass(frozen=True)
class ModelScore:
    """The learned scorer's verdict on one symbol."""

    raw_p: float
    p_outperform: float
    expected_excess: float | None
    confidence_label: str
    drivers: list[dict[str, Any]]
    model_version: str
    rank_pct: float | None = None


@dataclass
class ScoredSymbol:
    """Everything phase one (score) hands to phase two (rank, gate, publish)."""

    ticker: str
    period: str
    bar_ts: str
    signal_list: Any
    confluence: ConfluenceResult
    data_quality: Any
    indicator_snapshot: dict[str, float]
    model: ModelScore | None = None


_LLM_INDICATOR_COLUMNS = ("RSI", "MACD", "ADX", "Close", "ATR", "Price_Change")


def _snapshot(current: Any) -> dict[str, float]:
    snapshot: dict[str, float] = {}
    for col in _LLM_INDICATOR_COLUMNS:
        try:
            v = float(current[col])
        except Exception:  # noqa: BLE001 — a missing column just isn't reported
            continue
        if v == v and abs(v) != float("inf"):  # not NaN/inf
            snapshot[col.lower()] = round(v, 4)
    return snapshot


def _model_score(
    scorer: LogisticScorer, df: Any, signal_list: Any, market: MarketContext
) -> ModelScore:
    continuous = continuous_features_frame(df, market.benchmark_close).iloc[-1]
    row = build_feature_row(list(signal_list), continuous, market.regime)
    X = scorer.matrix([row])
    raw = float(scorer.raw_proba(X)[0])
    p = float(scorer.predict_proba(X)[0])
    excess = float(scorer.expected_excess(X)[0])
    return ModelScore(
        raw_p=raw,
        p_outperform=p,
        expected_excess=None if excess != excess else excess,
        confidence_label=confidence_label(p, scorer.analog_support(raw)),
        drivers=scorer.drivers(X[0]),
        model_version=scorer.model_version,
    )


def score_symbol(
    ticker: str,
    period: str,
    settings: Any,
    strength_hit_rates: dict[str, float] | None = None,
    scorer: LogisticScorer | None = None,
    market: MarketContext | None = None,
) -> ScoredSymbol | SymbolResult:
    """Phase one: fetch -> indicators -> detect -> confluence (-> model score).

    Never raises. Returns a failed SymbolResult when the symbol cannot be scored.
    """
    try:
        fetcher = DataFetcher(settings=settings)
        ohlcv = fetcher.fetch(ticker, period)
        if len(ohlcv.df) < 20:
            return SymbolResult(ticker, ok=False, published=False, reason="insufficient_bars")

        data_quality = score_data_quality(ohlcv.df, period)
        df = compute_indicators(ohlcv.df)
        signal_list = detect_all_signals(df)
        confluence = ConfluenceRanker().rank_signals(
            list(signal_list), strength_hit_rates=strength_hit_rates
        )
        model = (
            _model_score(scorer, df, signal_list, market or MarketContext(None, None))
            if scorer is not None
            else None
        )
        return ScoredSymbol(
            ticker=ticker,
            period=period,
            bar_ts=df.index[-1].isoformat(),
            signal_list=signal_list,
            confluence=confluence,
            data_quality=data_quality,
            indicator_snapshot=_snapshot(df.iloc[-1]),
            model=model,
        )
    except Exception as exc:  # noqa: BLE001 — per-symbol isolation
        logger.warning("scan_universe: %s failed: %s", ticker, exc)
        return SymbolResult(ticker, ok=False, published=False, reason=str(exc))


def _format_drivers(drivers: list[dict[str, Any]]) -> str:
    return ", ".join(f"{d['feature']} ({d['contribution']:+.2f})" for d in drivers)


def publish_symbol(
    scored: ScoredSymbol,
    writer: SignalWriter | None,
    run: EngineRun | None,
    settings: Any,
    dry_run: bool,
    compute_matrix: bool = False,
    direction: str | None = None,
    delta: float | None = None,
) -> SymbolResult:
    """Phase two: persist detector hits, gate, optionally synthesize + persist.

    Never raises. ``delta`` is the EV gate's edge threshold; it is only used
    when ``scored.model`` is present.
    """
    ticker, period, confluence, model = scored.ticker, scored.period, scored.confluence, scored.model
    p_out = model.p_outperform if model else None
    rank = model.rank_pct if model else None
    try:
        if writer is not None and not dry_run:
            # symbols is the FK target for both detector_hits and signals —
            # must exist first for tickers scanned outside the seeded universe.
            writer.ensure_symbol(ticker)
            writer.write_detector_hits(ticker, scored.bar_ts, list(scored.signal_list))

        if model is not None:
            cleared = passes_ev_gate(
                scored.data_quality.score, model.p_outperform, model.expected_excess,
                delta if delta is not None else 0.03, direction=direction,
            )
        else:
            cleared = passes_publication_gate(
                scored.data_quality.score, len(scored.signal_list), confluence.score,
                scored.signal_list.degraded, direction=direction,
            )
        if not cleared:
            return SymbolResult(ticker, ok=True, published=False, reason="gated", p_outperform=p_out, rank_pct=rank)

        # Only symbols that cleared the gate pay for LLM synthesis.
        ai_direction: str | None = None
        confidence: float | None = None
        evidence: list[dict[str, Any]] = []
        counter_evidence: list[dict[str, Any]] = []
        ai_degraded = False
        prompt_version: str | None = None

        if not dry_run:
            from signals_app.synthesis.mtf_llm import synthesize_single

            timeframe = _PERIOD_TO_TIMEFRAME.get(period, "1D")
            features: dict[str, Any] = {
                "symbol": ticker,
                "period": period,
                "confluence_score": confluence.score,
                "bias": confluence.bias,
                "action": confluence.action,
                "bull_count": confluence.bull_count,
                "bear_count": confluence.bear_count,
                "total_signals": len(scored.signal_list),
                **scored.indicator_snapshot,
            }
            if model is not None:
                # The narrative should explain the model's actual reasons, not a
                # list of unanimous detectors (plan §8).
                features["p_outperform"] = round(model.p_outperform, 3)
                features["rank_pct"] = model.rank_pct
                features["model_drivers"] = _format_drivers(model.drivers)

            signal = synthesize_single(
                ticker=ticker, timeframe=timeframe, features=features, settings=settings,
            )
            ai_direction = signal.direction.value
            confidence = signal.confidence
            ai_degraded = signal.ai_degraded
            prompt_version = signal.prompt_version
            evidence = [e.model_dump(mode="json") for e in signal.evidence.items if not e.is_counter]
            counter_evidence = [e.model_dump(mode="json") for e in signal.evidence.items if e.is_counter]

            matrix: dict[str, Any] | None = None
            if compute_matrix:
                try:
                    matrix = build_matrix_for_symbol(ticker, settings)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("matrix: %s failed, publishing without it: %s", ticker, exc)

            record = confluence_result_to_signal_record(
                ticker=ticker,
                period=period,
                bar_ts=scored.bar_ts,
                confluence=confluence,
                data_quality_score=scored.data_quality.score,
                data_quality_reasons=scored.data_quality.reasons,
                direction=ai_direction,
                confidence=confidence,
                evidence=evidence,
                counter_evidence=counter_evidence,
                matrix=matrix,
                ai_degraded=ai_degraded,
                no_llm=False,
                prompt_version=prompt_version,
                rank_pct=rank,
                p_outperform=p_out,
                expected_excess=model.expected_excess if model else None,
                model_version=model.model_version if model else None,
            )
            if writer is not None and run is not None:
                writer.write_signal(run, record)

        return SymbolResult(ticker, ok=True, published=True, p_outperform=p_out, rank_pct=rank)

    except Exception as exc:  # noqa: BLE001 — per-symbol isolation
        logger.warning("scan_universe: %s failed: %s", ticker, exc)
        return SymbolResult(ticker, ok=False, published=False, reason=str(exc))


def scan_one_symbol(
    ticker: str,
    period: str,
    writer: SignalWriter | None,
    run: EngineRun | None,
    settings: Any,
    dry_run: bool,
    strength_hit_rates: dict[str, float] | None = None,
    compute_matrix: bool = False,
    direction: str | None = None,
) -> SymbolResult:
    """Run L1-L4 for one ticker, gate, optionally synthesize + persist.

    The legacy single-symbol path (no learned scorer, no cross-sectional rank):
    ``score_symbol`` then ``publish_symbol``. Never raises — every failure mode
    is returned as a SymbolResult so scan_universe() can tally without aborting.

    Args:
        direction: Forwarded to passes_publication_gate() — None gates both
            directions (default), "bullish"/"bearish" gates one side only.
    """
    scored = score_symbol(ticker, period, settings, strength_hit_rates)
    if isinstance(scored, SymbolResult):
        return scored
    return publish_symbol(scored, writer, run, settings, dry_run, compute_matrix, direction)


def _scan_with_model(
    symbols: list[str],
    period: str,
    writer: SignalWriter | None,
    run: EngineRun | None,
    settings: Any,
    dry_run: bool,
    strength_hit_rates: dict[str, float] | None,
    compute_matrix: bool,
    direction: str | None,
    max_concurrent: int,
    scorer: LogisticScorer,
    record: Callable[[SymbolResult], None],
) -> None:
    """Score every symbol, rank the run cross-sectionally, then gate + publish.

    ``rank_pct`` needs every score before any row is written (plan §4), so the
    scan splits into: score all -> rank -> gate/synthesize/persist.
    """
    market = load_market_context(settings)
    scored: list[ScoredSymbol] = []

    with ThreadPoolExecutor(max_workers=max_concurrent) as pool:
        futures = [
            pool.submit(score_symbol, t, period, settings, strength_hit_rates, scorer, market)
            for t in symbols
        ]
        for future in as_completed(futures):
            outcome = future.result()
            if isinstance(outcome, SymbolResult):
                record(outcome)
            else:
                scored.append(outcome)

    ranks = compute_rank_pct({s.ticker: s.model.raw_p for s in scored if s.model is not None})
    ranked = [
        replace(s, model=replace(s.model, rank_pct=ranks.get(s.ticker))) if s.model else s
        for s in scored
    ]

    delta = scorer.publish_delta()
    with ThreadPoolExecutor(max_workers=max_concurrent) as pool:
        futures = [
            pool.submit(publish_symbol, s, writer, run, settings, dry_run, compute_matrix, direction, delta)
            for s in ranked
        ]
        for future in as_completed(futures):
            record(future.result())


def scan_universe(
    symbols: list[str],
    period: str = DEFAULT_PERIOD,
    writer: SignalWriter | None = None,
    trigger: str = "manual",
    dry_run: bool = False,
    max_concurrent: int = MAX_CONCURRENT_FETCHES,
    compute_matrix: bool = False,
    direction: str | None = None,
    progress: Callable[[int, int, SymbolResult], None] | None = None,
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
        direction: Gate for one direction only — None gates both, "bullish"
            gates positive confluence only, "bearish" gates negative only.
        progress: Optional callback invoked as ``progress(done, total, result)``
            each time a symbol finishes — how ``service.scan()`` draws a bar or
            streams updates without this function knowing either exists. A
            raising callback is logged and ignored, never aborts the scan.

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
    total = len(symbols)

    def _record(result: SymbolResult) -> None:
        results.append(result)
        if progress is not None:
            try:
                progress(len(results), total, result)
            except Exception as exc:  # noqa: BLE001 — progress must never abort a scan
                logger.warning("scan progress callback raised: %s", exc)

    scorer = load_active_scorer()
    if scorer is None:
        with ThreadPoolExecutor(max_workers=max_concurrent) as pool:
            futures = {
                pool.submit(
                    scan_one_symbol,
                    t, period, writer, run, settings, dry_run, strength_hit_rates,
                    compute_matrix, direction,
                ): t
                for t in symbols
            }
            for future in as_completed(futures):
                _record(future.result())
    else:
        _scan_with_model(
            symbols, period, writer, run, settings, dry_run, strength_hit_rates,
            compute_matrix, direction, max_concurrent, scorer, _record,
        )

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
