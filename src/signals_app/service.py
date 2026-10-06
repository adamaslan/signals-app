"""The one seam every consumer goes through.

Framework-agnostic: no FastAPI, no Typer, no MCP types cross this boundary.
Every function takes typed args in and returns a Pydantic model (or a list /
dataclass of them). No ``print``, no ``argparse``, no ``HTTPException``. Raises
:class:`SignalsError` subclasses that the adapters translate into their own
error shapes.

This module is the fix for the "five ways to invoke the engine, no two agree"
problem (see ``docs/signals-app-docs/signals-as-api-cli-mcp.md`` §1.1). The
FastAPI routes, the ``signals`` CLI, and the MCP server are all thin adapters
over the functions here.

The rule that keeps it honest (§2.2): **no consumer imports from
``signals_app.detection`` / ``.scoring`` / ``.synthesis`` / ``.indicators`` /
``.data`` directly — they import ``signals_app.service`` only.** Enforced by
``tests/test_layering.py``.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from backtests.engine import (
    BASELINE_UP_KEY,
    HitRateBucket,
    merge_hit_rate_buckets,
    score_historical_signals,
)
from signals_app.config import (
    BACKTEST_FORWARD_HORIZON_DAYS,
    DEFAULT_PERIOD,
    MAX_MANUAL_BACKTEST_SYMBOLS,
    EVIDENCE_FILE,
    MIN_HISTORICAL_LOOKBACK,
    RANKER_MODE,
    SIGNALS_APP_CODE_VERSION,
    SUGGEST_DETECT_PERIOD,
    THRESHOLDS_FILE,
    VALID_PERIODS,
    get_settings,
)
from signals_app.data.fetcher import DataFetcher
from signals_app.db.ops import RunRecord, get_ticker_history, record_run
from signals_app.detection.base import MutableSignal
from signals_app.detection.historical import scan_historical
from signals_app.detection.orchestrator import detect_all_signals, get_default_detectors
from signals_app.hypotheses import (
    BacktestHypothesis,
    HypothesisFocus,
    HypothesisVerdict,
    evaluate_hypothesis,
    suggest_hypotheses,
)
from signals_app.indicators.compute import compute_indicators
from signals_app.indicators.data_quality import score_data_quality
from signals_app.schemas.signal_output import Signal, SignalOutput, SignalState
from signals_app.scoring.calibration import load_strength_hit_rates
from signals_app.scoring.production import build_production_ranker
from signals_app.synthesis.mtf_llm import synthesize_single

logger = logging.getLogger(__name__)

__all__ = [
    # exceptions
    "SignalsError",
    "SymbolNotFound",
    "InsufficientData",
    "InvalidPeriod",
    "UpstreamUnavailable",
    # result models
    "BatchResult",
    "BacktestResult",
    "UniverseBacktestResult",
    "DetectorInfo",
    "HealthReport",
    "ScanProgress",
    "ScanResult",
    "ScanSymbolOutcome",
    # functions
    "analyze",
    "analyze_many",
    "backtest",
    "backtest_many",
    "brief",
    "brief_many",
    "brief_to_rag_document",
    "rag_documents",
    "TickerBrief",
    "RagDocument",
    "history",
    "detectors",
    "health",
    "scan",
]

# The minimum bar count the single-symbol pipeline needs before it will run —
# mirrors the check that lived inline in routes.get_signals.
_MIN_ANALYZE_BARS = 20

# Batch fan-out defaults. analyze_many defaults to no_llm=True because a batch
# with LLM synthesis on costs real money per symbol (§3.4).
_DEFAULT_BATCH_CONCURRENCY = 4

_PERIOD_TO_TIMEFRAME: dict[str, str] = {
    "1d": "1D",
    "5d": "5D",
    "1mo": "1M",
    "3mo": "3M",
    "6mo": "6M",
    "1y": "1Y",
}


class _TTLCache:
    """Small bounded in-process TTL cache (per Cloud Run instance, best-effort)."""

    def __init__(self, ttl_seconds: float, max_entries: int) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._data: dict[Any, tuple[float, Any]] = {}

    def get(self, key: Any) -> Any | None:
        hit = self._data.get(key)
        if hit is None:
            return None
        expires_at, value = hit
        if time.monotonic() >= expires_at:
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: Any, value: Any) -> None:
        if len(self._data) >= self._max:
            oldest = min(self._data, key=lambda k: self._data[k][0])
            self._data.pop(oldest, None)
        self._data[key] = (time.monotonic() + self._ttl, value)

    def clear(self) -> None:
        self._data.clear()


# Backtests replay ~2y of daily bars per symbol — seconds of CPU — and their
# answer only moves once per trading day. Consumers like the portal's council
# grounding call with an 8s timeout, so a warm cache is the difference between
# grounding and "omitted".
_BACKTEST_CACHE_TTL_SECONDS = 6 * 60 * 60
_BACKTEST_CACHE_MAX_ENTRIES = 1024
_backtest_cache = _TTLCache(_BACKTEST_CACHE_TTL_SECONDS, _BACKTEST_CACHE_MAX_ENTRIES)

# Coalesces concurrent cold-key requests (batch endpoints run up to 4 at once,
# and separate callers can overlap too) onto one in-flight replay instead of
# each caller repeating the full CPU + yfinance fetch independently.
_backtest_inflight: dict[tuple[str, str, int], asyncio.Task[BacktestResult]] = {}
_backtest_inflight_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Domain exceptions — adapters translate these (§2.1). Names are fixed by the
# design doc (§2.1); ruff's N818 "Error suffix" rule doesn't apply here.
# ---------------------------------------------------------------------------


class SignalsError(Exception):  # noqa: N818
    """Base class for every error the service raises deliberately."""


class SymbolNotFound(SignalsError):  # noqa: N818
    """The ticker is unknown to the upstream data provider, or returned no data."""


class InsufficientData(SignalsError):  # noqa: N818
    """Not enough bars to run the requested analysis for the requested window."""


class InvalidPeriod(SignalsError):  # noqa: N818
    """The period string is not in ``VALID_PERIODS``."""


class UpstreamUnavailable(SignalsError):  # noqa: N818
    """yfinance / the LLM provider / Supabase was unreachable or errored."""


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BatchResult:
    """Outcome of a fan-out over many symbols.

    Partial success is first-class: ``analyze_many`` never raises for one bad
    symbol, it puts it in ``failed``. Adapters map this to CLI exit 6 / an MCP
    payload carrying both lists / an HTTP 207-style body.
    """

    ok: list[SignalOutput] = field(default_factory=list)
    failed: list[BatchFailure] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        """True when every requested symbol produced a signal."""
        return not self.failed

    @property
    def partial(self) -> bool:
        """True when some — but not all — symbols succeeded."""
        return bool(self.ok) and bool(self.failed)


@dataclass(frozen=True)
class BatchFailure:
    """One symbol that failed inside a batch, with a machine-usable reason."""

    symbol: str
    error_type: str
    message: str


@dataclass(frozen=True)
class BacktestResult:
    """Historical hit-rate for one symbol, grouped by category and by strength."""

    symbol: str
    period: str
    horizon_days: int
    bars_scanned: int
    by_category: list[HitRateBucket]
    by_strength: list[HitRateBucket]
    by_signal: list[HitRateBucket] = field(default_factory=list)
    # Scored bars that rose over the horizon / scored bars — the chance rate.
    up_bars: int = 0
    scored_bars: int = 0


@dataclass(frozen=True)
class UniverseBacktestResult:
    """Merged hit-rate across a basket.

    Merged via ``backtests.engine.merge_hit_rate_buckets`` — the correct
    weighted merge (sum hits / sum totals), not a mean of per-symbol rates.
    """

    symbols_ok: list[str]
    symbols_failed: list[BatchFailure]
    horizon_days: int
    by_category: list[HitRateBucket]
    by_strength: list[HitRateBucket]
    by_signal: list[HitRateBucket] = field(default_factory=list)
    up_bars: int = 0
    scored_bars: int = 0
    period: str = "2y"

    @property
    def up_rate(self) -> float | None:
        """Fraction of scored bars that rose — the basket's chance baseline."""
        return self.up_bars / self.scored_bars if self.scored_bars else None


@dataclass(frozen=True)
class DetectorInfo:
    """One registered detector, self-describing."""

    name: str
    category: str
    description: str
    calibrated_hit_rate: float | None


@dataclass(frozen=True)
class ScanProgress:
    """One tick of scan progress, passed to a ``scan(progress=...)`` callback."""

    done: int
    total: int
    ticker: str
    ok: bool
    published: bool
    reason: str | None = None


@dataclass(frozen=True)
class ScanSymbolOutcome:
    """The final state of one scanned symbol."""

    ticker: str
    ok: bool
    published: bool
    reason: str | None = None


@dataclass(frozen=True)
class ScanResult:
    """Aggregate outcome of a universe scan.

    ``published`` is deliberately the small number — most ticker-days should
    fail the publication gate; that is what makes the engine selective.
    """

    symbols_total: int
    symbols_ok: int
    symbols_failed: int
    symbols_published: int
    dry_run: bool
    trigger: str
    elapsed_seconds: float
    outcomes: list[ScanSymbolOutcome] = field(default_factory=list)

    @property
    def partial(self) -> bool:
        """True when some symbols failed but not all — the exit-6 case."""
        return 0 < self.symbols_failed < self.symbols_total


@dataclass(frozen=True)
class HealthReport:
    """Reachability of each upstream plus the active LLM provider."""

    yfinance_ok: bool
    llm_provider: str
    llm_configured: bool
    supabase_configured: bool
    code_version: str
    detail: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """True when the data provider is reachable — the one hard dependency."""
        return self.yfinance_ok


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalize_period(period: str) -> str:
    """Lower/strip a period string and validate it against ``VALID_PERIODS``.

    Raises:
        InvalidPeriod: If the value is not a supported period.
    """
    p = period.lower().strip()
    if p not in VALID_PERIODS:
        raise InvalidPeriod(
            f"Invalid period '{period}'. Valid periods: {', '.join(VALID_PERIODS)}"
        )
    return p


def _normalize_symbol(symbol: str) -> str:
    """Upper/strip a ticker symbol."""
    s = symbol.upper().strip()
    if not s:
        raise SymbolNotFound("empty symbol")
    return s


def _build_features(
    symbol: str, period: str, confluence_result: Any, df: pd.DataFrame
) -> dict[str, Any]:
    """Build the LLM feature dict from pipeline results.

    Moved verbatim from ``routes._build_features`` — the adapters no longer
    know this shape exists.
    """
    import math

    current = df.iloc[-1] if len(df) > 0 else None

    features: dict[str, Any] = {
        "symbol": symbol,
        "period": period,
        "confluence_score": confluence_result.score,
        "bias": confluence_result.bias,
        "action": confluence_result.action,
        "bull_count": confluence_result.bull_count,
        "bear_count": confluence_result.bear_count,
        "total_signals": confluence_result.total_signals,
    }

    if current is not None:
        for col_key in ["RSI", "MACD", "ADX", "Close", "Volume", "ATR", "Price_Change"]:
            try:
                val = current.get(col_key) if hasattr(current, "get") else current[col_key]
                if val is not None:
                    v = float(val)
                    features[col_key.lower()] = (
                        round(v, 4) if not (math.isnan(v) or math.isinf(v)) else None
                    )
            except Exception:  # noqa: BLE001 — a missing/odd column must not abort synthesis
                pass

    return features


def _build_state(confluence_result: Any, features: dict[str, Any], df: pd.DataFrame) -> SignalState:
    """Expose the deterministic pipeline numbers that feed synthesis."""
    as_of: str | None = None
    if len(df) > 0 and hasattr(df.index[-1], "strftime"):
        as_of = df.index[-1].strftime("%Y-%m-%d")
    return SignalState(
        as_of=as_of,
        confluence_score=features.get("confluence_score"),
        bias=features.get("bias"),
        action=features.get("action"),
        confidence_label=getattr(confluence_result, "confidence_label", None),
        bull_count=features.get("bull_count"),
        bear_count=features.get("bear_count"),
        total_signals=features.get("total_signals"),
        close=features.get("close"),
        price_change=features.get("price_change"),
        rsi=features.get("rsi"),
        macd=features.get("macd"),
        adx=features.get("adx"),
        atr=features.get("atr"),
        volume=features.get("volume"),
    )


# ---------------------------------------------------------------------------
# The service functions
# ---------------------------------------------------------------------------


async def analyze(
    symbol: str,
    period: str = DEFAULT_PERIOD,
    *,
    no_llm: bool = False,
) -> SignalOutput:
    """Full L1–L5 pipeline for one symbol. The canonical single-symbol path.

    This is the body of the old ``routes.get_signals`` with the ``HTTPException``
    layer removed — it raises domain exceptions instead, and persists the run
    exactly as before (fire-and-forget; a DB failure is logged, not raised).

    Args:
        symbol: Ticker symbol (case-insensitive).
        period: yfinance period string; validated against ``VALID_PERIODS``.
        no_llm: Skip LLM synthesis; return a rule-based signal. Free.

    Returns:
        A fully-populated :class:`SignalOutput`.

    Raises:
        InvalidPeriod: The period is not supported.
        SymbolNotFound: The provider returned no data for the symbol.
        InsufficientData: Fewer than 20 bars were available.
        UpstreamUnavailable: The data fetch or a pipeline layer errored.
    """
    symbol = _normalize_symbol(symbol)
    period = _normalize_period(period)
    settings = get_settings()

    logger.info("service.analyze symbol=%s period=%s no_llm=%s", symbol, period, no_llm)

    # The pipeline is synchronous (yfinance, pandas, and synthesize_single's
    # private event loop). Running it on the caller's loop blocked every other
    # request and made synthesize_single raise "Cannot run the event loop while
    # another loop is running", silently degrading every API/MCP call to rules.
    output = await asyncio.to_thread(_analyze_sync, symbol, period, no_llm, settings)
    primary_signal = output.signal

    # Persist (fire-and-forget — a DB failure must not fail the request path).
    # init_db() is idempotent; the API path already calls it in its lifespan,
    # but a CLI / MCP consumer has no lifespan, so ensure it here.
    try:
        from signals_app.db.session import init_db

        await init_db()
        await record_run(
            ticker=symbol,
            period=period,
            resolved_period=period,
            direction=(
                primary_signal.direction.value
                if primary_signal.direction is not None
                else None
            ),
            confidence=primary_signal.confidence,
            ai_degraded=primary_signal.ai_degraded,
            no_llm=no_llm,
            prompt_version=primary_signal.prompt_version,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("service.analyze: failed to record run ticker=%s: %s", symbol, exc)

    return output


def _analyze_sync(symbol: str, period: str, no_llm: bool, settings: Any) -> SignalOutput:
    """L1–L5 for one already-normalized symbol; blocking, run off the event loop."""
    # L1: fetch
    try:
        fetcher = DataFetcher(settings=settings)
        df_raw = fetcher.fetch(symbol, period).df
    except ValueError as exc:
        raise SymbolNotFound(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — provider errors are opaque; wrap uniformly
        logger.error("service.analyze: data fetch failed for %s: %s", symbol, exc, exc_info=True)
        raise UpstreamUnavailable(f"Data fetch error: {exc}") from exc

    if len(df_raw) < _MIN_ANALYZE_BARS:
        raise InsufficientData(
            f"Insufficient data for {symbol} period={period}: only {len(df_raw)} bars "
            f"(need {_MIN_ANALYZE_BARS})"
        )

    data_quality = score_data_quality(df_raw, period)

    # L2: indicators
    try:
        df = compute_indicators(df_raw)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "service.analyze: indicator compute failed for %s: %s", symbol, exc, exc_info=True
        )
        raise UpstreamUnavailable(f"Indicator compute error: {exc}") from exc

    # L3: detection
    try:
        signal_list = detect_all_signals(df)
    except Exception as exc:  # noqa: BLE001
        logger.error("service.analyze: detection failed for %s: %s", symbol, exc, exc_info=True)
        raise UpstreamUnavailable(f"Signal detection error: {exc}") from exc

    # L4: confluence scoring (calibrated when a table exists, safe default otherwise)
    try:
        strength_hit_rates = load_strength_hit_rates()
        ranker = build_production_ranker(RANKER_MODE, THRESHOLDS_FILE, EVIDENCE_FILE)
        confluence_result = ranker.rank_signals(
            list(signal_list), strength_hit_rates=strength_hit_rates, df=df
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("service.analyze: confluence failed for %s: %s", symbol, exc, exc_info=True)
        raise UpstreamUnavailable(f"Confluence error: {exc}") from exc

    timeframe_label = _PERIOD_TO_TIMEFRAME.get(period, "1D")

    # L5: synthesis
    features = _build_features(symbol, period, confluence_result, df)
    unavailable: list[str] = []
    if signal_list.degraded:
        unavailable.append("detection_degraded")
    if no_llm:
        from signals_app.synthesis.mtf_llm import _fallback_signal

        fallback_dict = _fallback_signal(timeframe_label, features)
        fallback_dict["timeframe"] = timeframe_label
        primary_signal = Signal.model_validate(fallback_dict)
        unavailable.append("synthesis_skipped")
    else:
        if not settings.llm_enabled:
            unavailable.append("llm_synthesis")

        try:
            primary_signal = synthesize_single(
                ticker=symbol,
                timeframe=timeframe_label,
                features=features,
                settings=settings,
            )
        except Exception as exc:  # noqa: BLE001 — degrade, never fail the whole analysis
            logger.error("service.analyze: synthesis failed for %s: %s", symbol, exc, exc_info=True)
            from signals_app.synthesis.mtf_llm import _fallback_signal

            fallback_dict = _fallback_signal(timeframe_label, features)
            fallback_dict["timeframe"] = timeframe_label
            primary_signal = Signal.model_validate(fallback_dict)
            unavailable.append("synthesis_error")

    return SignalOutput(
        ticker=symbol,
        signal=primary_signal,
        matrix=None,
        feature_unavailable=unavailable,
        schema_version="1.0",
        code_version=SIGNALS_APP_CODE_VERSION,
        data_quality_score=data_quality.score,
        data_quality_reasons=data_quality.reasons,
        state=_build_state(confluence_result, features, df),
    )


async def analyze_many(
    symbols: Sequence[str],
    period: str = DEFAULT_PERIOD,
    *,
    no_llm: bool = True,
    max_concurrent: int = _DEFAULT_BATCH_CONCURRENCY,
) -> BatchResult:
    """Concurrency-bounded fan-out over :func:`analyze`.

    Partial success is a first-class result: one bad symbol lands in
    ``BatchResult.failed`` — this never raises for a single symbol. It *does*
    raise :class:`InvalidPeriod` up front, since a bad period fails every symbol.

    Args:
        symbols: Tickers to analyze. De-duplicated, order not guaranteed.
        period: yfinance period string.
        no_llm: Default True — a batch with synthesis on costs money per symbol.
        max_concurrent: Bounded concurrency (yfinance throttles under load).

    Returns:
        A :class:`BatchResult` with ``ok`` and ``failed`` populated.
    """
    period = _normalize_period(period)
    unique = sorted({_normalize_symbol(s) for s in symbols})
    if not unique:
        return BatchResult()

    sem = asyncio.Semaphore(max(1, max_concurrent))

    async def _one(sym: str) -> tuple[str, SignalOutput | BatchFailure]:
        async with sem:
            try:
                return sym, await analyze(sym, period, no_llm=no_llm)
            except SignalsError as exc:
                return sym, BatchFailure(sym, type(exc).__name__, str(exc))
            except Exception as exc:  # noqa: BLE001 — batch must survive any single failure
                logger.warning("analyze_many: %s failed unexpectedly: %s", sym, exc)
                return sym, BatchFailure(sym, "UnexpectedError", str(exc))

    results = await asyncio.gather(*(_one(s) for s in unique))

    ok: list[SignalOutput] = []
    failed: list[BatchFailure] = []
    for _sym, outcome in results:
        if isinstance(outcome, BatchFailure):
            failed.append(outcome)
        else:
            ok.append(outcome)

    logger.info("analyze_many: %d ok, %d failed of %d", len(ok), len(failed), len(unique))
    return BatchResult(ok=ok, failed=failed)


async def backtest(
    symbol: str,
    period: str = "2y",
    horizon_days: int = BACKTEST_FORWARD_HORIZON_DAYS,
) -> BacktestResult:
    """Score every historical bar's signals against realized forward returns.

    This is ``routes.get_backtest`` minus the ``HTTPException`` layer.

    Args:
        symbol: Ticker symbol.
        period: yfinance period string; long enough to clear the indicator
            warmup plus a meaningful scan window (default ``2y``).
        horizon_days: Bars ahead used to measure the realized return.

    Returns:
        A :class:`BacktestResult`.

    Raises:
        InvalidPeriod, SymbolNotFound, InsufficientData, UpstreamUnavailable.
    """
    symbol = _normalize_symbol(symbol)
    period = _normalize_period(period)
    settings = get_settings()

    logger.info(
        "service.backtest symbol=%s period=%s horizon_days=%d", symbol, period, horizon_days
    )

    key = (symbol, period, horizon_days)
    cached = _backtest_cache.get(key)
    if cached is not None:
        return cached

    async with _backtest_inflight_lock:
        task = _backtest_inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(
                asyncio.to_thread(_backtest_sync, symbol, period, horizon_days, settings)
            )
            _backtest_inflight[key] = task

            def _on_done(t: asyncio.Task[BacktestResult], k: tuple[str, str, int] = key) -> None:
                _backtest_inflight.pop(k, None)
                if not t.cancelled() and t.exception() is None:
                    _backtest_cache.set(k, t.result())

            task.add_done_callback(_on_done)

    # Shielded: a caller's own cancellation (client timeout, MCP request
    # cancellation) must not cancel the shared task every other caller of
    # this key is also awaiting.
    return await asyncio.shield(task)


def _backtest_sync(symbol: str, period: str, horizon_days: int, settings: Any) -> BacktestResult:
    """Blocking backtest body for one normalized symbol; run off the event loop.

    Always daily bars: ``fetch`` maps 2y/5y to weekly bars, which left a 2y
    backtest ~105 bars short of the 200-bar warmup (so every default request
    failed) and would have measured ``horizon_days`` in weeks. Calibration
    (``scripts/calibrate.py``) already measures on daily bars; this matches it.
    """
    try:
        # Daily bars regardless of period: fetch() maps 2y/5y to *weekly*
        # bars, which both starves the 200-bar warmup and makes
        # horizon_days mean weeks.
        fetcher = DataFetcher(settings=settings)
        df_raw = fetcher.fetch_daily_history(symbol, period)
    except ValueError as exc:
        raise SymbolNotFound(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("service.backtest: data fetch failed for %s: %s", symbol, exc, exc_info=True)
        raise UpstreamUnavailable(f"Data fetch error: {exc}") from exc

    if len(df_raw) <= MIN_HISTORICAL_LOOKBACK + horizon_days:
        raise InsufficientData(
            f"Insufficient data for {symbol} period={period}: {len(df_raw)} bars, "
            f"need > {MIN_HISTORICAL_LOOKBACK + horizon_days} (warmup + horizon)"
        )

    try:
        df = compute_indicators(df_raw)
        bars = scan_historical(df)
        scored = score_historical_signals(df, bars, horizon_days=horizon_days)
    except Exception as exc:  # noqa: BLE001
        logger.error("service.backtest: failed for %s: %s", symbol, exc, exc_info=True)
        raise UpstreamUnavailable(f"Backtest error: {exc}") from exc

    baseline = next(
        (b for b in scored.get("baseline", []) if b.key == BASELINE_UP_KEY), None
    )
    return BacktestResult(
        symbol=symbol,
        period=period,
        horizon_days=horizon_days,
        bars_scanned=len(bars),
        by_category=list(scored["by_category"]),
        by_strength=list(scored["by_strength"]),
        by_signal=list(scored.get("by_signal", [])),
        up_bars=baseline.hits if baseline else 0,
        scored_bars=baseline.total if baseline else 0,
    )


async def backtest_many(
    symbols: Sequence[str],
    period: str = "2y",
    horizon_days: int = 20,
    *,
    max_concurrent: int = _DEFAULT_BATCH_CONCURRENCY,
) -> UniverseBacktestResult:
    """Backtest a basket and merge the results with the correct weighted merge.

    Uses ``backtests.engine.merge_hit_rate_buckets`` — sums hits and totals
    bucket-by-bucket, not a mean of per-symbol hit-rates.

    Args:
        symbols: Tickers to backtest.
        period: yfinance period string.
        horizon_days: Forward-return horizon in trading days.
        max_concurrent: Bounded concurrency.

    Returns:
        A :class:`UniverseBacktestResult` with merged category / strength buckets.

    Raises:
        InvalidPeriod: The period is not supported (fails every symbol).
    """
    period = _normalize_period(period)
    unique = sorted({_normalize_symbol(s) for s in symbols})
    if not unique:
        return UniverseBacktestResult([], [], horizon_days, [], [], period=period)

    sem = asyncio.Semaphore(max(1, max_concurrent))

    async def _one(sym: str) -> tuple[str, BacktestResult | BatchFailure]:
        async with sem:
            try:
                return sym, await backtest(sym, period, horizon_days)
            except SignalsError as exc:
                return sym, BatchFailure(sym, type(exc).__name__, str(exc))
            except Exception as exc:  # noqa: BLE001
                logger.warning("backtest_many: %s failed unexpectedly: %s", sym, exc)
                return sym, BatchFailure(sym, "UnexpectedError", str(exc))

    results = await asyncio.gather(*(_one(s) for s in unique))

    ok_syms: list[str] = []
    failed: list[BatchFailure] = []
    cat_lists: list[list[HitRateBucket]] = []
    strength_lists: list[list[HitRateBucket]] = []
    signal_lists: list[list[HitRateBucket]] = []
    up_bars = 0
    scored_bars = 0
    for sym, outcome in results:
        if isinstance(outcome, BatchFailure):
            failed.append(outcome)
        else:
            ok_syms.append(sym)
            cat_lists.append(outcome.by_category)
            strength_lists.append(outcome.by_strength)
            signal_lists.append(outcome.by_signal)
            up_bars += outcome.up_bars
            scored_bars += outcome.scored_bars

    merged_cat = merge_hit_rate_buckets(cat_lists) if cat_lists else []
    merged_strength = merge_hit_rate_buckets(strength_lists) if strength_lists else []
    merged_signal = merge_hit_rate_buckets(signal_lists) if signal_lists else []

    logger.info(
        "backtest_many: %d ok, %d failed of %d", len(ok_syms), len(failed), len(unique)
    )
    return UniverseBacktestResult(
        symbols_ok=ok_syms,
        symbols_failed=failed,
        horizon_days=horizon_days,
        by_category=merged_cat,
        by_strength=merged_strength,
        by_signal=merged_signal,
        up_bars=up_bars,
        scored_bars=scored_bars,
        period=period,
    )


@dataclass(frozen=True)
class BacktestSuggestions:
    """Hypotheses the engine proposes for a basket, from its live signals."""

    hypotheses: list[BacktestHypothesis]
    symbols_ok: list[str]
    symbols_failed: list[BatchFailure]
    # Ticker -> directional signal names live on its latest bar (for display).
    live_signals: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class HypothesisRun:
    """A backtest run for an explicit spec, plus its verdict when focused."""

    result: UniverseBacktestResult
    verdict: HypothesisVerdict | None


async def _latest_signals(symbol: str, period: str) -> list[MutableSignal]:
    """Detectors' output on ``symbol``'s most recent bar. No LLM, no DB."""
    settings = get_settings()

    def _detect() -> list[MutableSignal]:
        df_raw = DataFetcher(settings=settings).fetch(symbol, period).df
        if len(df_raw) < _MIN_ANALYZE_BARS:
            raise InsufficientData(f"{symbol}: only {len(df_raw)} bars for period={period}")
        return list(detect_all_signals(compute_indicators(df_raw)))

    try:
        return await asyncio.to_thread(_detect)
    except SignalsError:
        raise
    except ValueError as exc:
        raise SymbolNotFound(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise UpstreamUnavailable(f"Detection error for {symbol}: {exc}") from exc


async def suggest_backtests(
    symbols: Sequence[str],
    *,
    backtest_period: str = "2y",
    horizon_days: int | None = None,
    max_suggestions: int = 8,
    max_concurrent: int = _DEFAULT_BATCH_CONCURRENCY,
) -> BacktestSuggestions:
    """Propose backtests that test the claims this basket's live signals make.

    Detects each ticker's current signals (latest bar, rule-based only), then
    hands them to :func:`signals_app.hypotheses.suggest_hypotheses`.

    Args:
        symbols: The run's tickers.
        backtest_period: History window each suggested backtest should replay.
        horizon_days: Force one horizon; ``None`` picks per signal category.
        max_suggestions: Cap on returned hypotheses.
        max_concurrent: Bounded fetch/detect concurrency.

    Returns:
        A :class:`BacktestSuggestions`. Per-symbol failures never fail the call.

    Raises:
        InvalidPeriod: ``backtest_period`` is not supported.
    """
    backtest_period = _normalize_period(backtest_period)
    unique = sorted({_normalize_symbol(s) for s in symbols})
    sem = asyncio.Semaphore(max(1, max_concurrent))

    async def _one(sym: str) -> tuple[str, list[MutableSignal] | BatchFailure]:
        async with sem:
            try:
                return sym, await _latest_signals(sym, SUGGEST_DETECT_PERIOD)
            except SignalsError as exc:
                return sym, BatchFailure(sym, type(exc).__name__, str(exc))

    latest: dict[str, list[MutableSignal]] = {}
    failed: list[BatchFailure] = []
    for sym, outcome in await asyncio.gather(*(_one(s) for s in unique)):
        if isinstance(outcome, BatchFailure):
            failed.append(outcome)
        else:
            latest[sym] = outcome

    hypotheses = suggest_hypotheses(
        latest,
        period=backtest_period,
        horizon_days=horizon_days,
        max_suggestions=max_suggestions,
        max_symbols=MAX_MANUAL_BACKTEST_SYMBOLS,
    )
    live = {
        t: sorted({s.signal for s in sigs if "BULLISH" in s.strength or "BEARISH" in s.strength})
        for t, sigs in latest.items()
    }
    logger.info(
        "suggest_backtests: %d hypotheses from %d ok / %d failed symbols",
        len(hypotheses), len(latest), len(failed),
    )
    return BacktestSuggestions(hypotheses, sorted(latest), failed, live)


async def run_hypothesis(
    symbols: Sequence[str],
    period: str,
    horizon_days: int,
    focus: Sequence[HypothesisFocus] = (),
) -> HypothesisRun:
    """Backtest a basket and, when ``focus`` is given, judge the hypothesis.

    Args:
        symbols: Tickers to replay.
        period: History window.
        horizon_days: Forward horizon in bars.
        focus: Buckets the hypothesis is about; empty = plain backtest.

    Returns:
        A :class:`HypothesisRun`.

    Raises:
        InvalidPeriod: The period is not supported.
    """
    result = await backtest_many(symbols, period, horizon_days)
    if not focus:
        return HypothesisRun(result, None)
    buckets = {
        "signal": result.by_signal,
        "category": result.by_category,
        "strength": result.by_strength,
    }
    return HypothesisRun(result, evaluate_hypothesis(focus, buckets, result.up_rate))


# ---------------------------------------------------------------------------
# Grounding briefs + RAG documents — the LLM-consumer surface
# ---------------------------------------------------------------------------

_BRIEF_BACKTEST_PERIOD = "2y"
_BRIEF_MAX_EVIDENCE_LINES = 5
_BRIEF_MAX_HIT_RATE_LINES = 6
RagMetadataValue = str | int | float | bool


@dataclass(frozen=True)
class TickerBrief:
    """Everything an LLM needs to reason about one ticker, pre-joined.

    ``text`` is deterministic and number-first so it can be pasted straight
    into a prompt; the structured fields are there for code that wants to key
    on values (council seat slicing, graders) instead of parsing the text.
    """

    ticker: str
    period: str
    generated_at: str
    signal: SignalOutput
    backtest: BacktestResult | None
    omitted: list[str]
    text: str


@dataclass(frozen=True)
class RagDocument:
    """One vector-store-ready document: stable id, text, flat scalar metadata.

    Metadata values are str/int/float/bool only and never None — the lowest
    common denominator ChromaDB, pgvector and LightRAG all accept.
    """

    id: str
    text: str
    metadata: dict[str, RagMetadataValue]


def _fmt_num(value: float | None, digits: int = 2, signed: bool = False) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.{digits}f}" if signed else f"{value:.{digits}f}"


def _format_brief_text(
    signal: SignalOutput, backtest: BacktestResult | None, omitted: list[str]
) -> str:
    st = signal.state or SignalState()
    sig = signal.signal
    rule_based = sig.ai_degraded or "synthesis_skipped" in signal.feature_unavailable
    source = "rule-based" if rule_based else "LLM"
    lines = [
        f"TICKER {signal.ticker} — signals-app brief "
        f"(as of {st.as_of or 'n/a'}, code {signal.code_version or 'n/a'})",
        f"Signal: {sig.direction.value.upper()} (confidence {sig.confidence:.2f}, "
        f"timeframe {sig.timeframe.value}, {source})",
        (
            f"Confluence: {_fmt_num(st.confluence_score, signed=True)} {st.bias or 'n/a'} · "
            f"action {st.action or 'n/a'} · confidence {st.confidence_label or 'n/a'} · "
            f"{st.bull_count if st.bull_count is not None else '?'} bull / "
            f"{st.bear_count if st.bear_count is not None else '?'} bear of "
            f"{st.total_signals if st.total_signals is not None else '?'} signals"
        ),
        (
            f"Indicators: close {_fmt_num(st.close)} · "
            f"1-bar change {_fmt_num(st.price_change, signed=True)}% · "
            f"RSI {_fmt_num(st.rsi, 1)} · ADX {_fmt_num(st.adx, 1)} · ATR {_fmt_num(st.atr)} · "
            f"MACD {_fmt_num(st.macd, 3, signed=True)}"
        ),
    ]
    supporting = [e for e in sig.evidence.items if not e.is_counter]
    counter = [e for e in sig.evidence.items if e.is_counter]
    if supporting:
        lines.append("Evidence:")
        lines += [
            f"- [{e.source.value} {e.weight:.2f}] {e.summary}"
            for e in supporting[:_BRIEF_MAX_EVIDENCE_LINES]
        ]
    if counter:
        lines.append("Counter-evidence:")
        lines += [
            f"- [{e.source.value}] {e.summary}" for e in counter[:_BRIEF_MAX_EVIDENCE_LINES]
        ]
    if backtest is not None:
        buckets = [("strength", b) for b in backtest.by_strength] + [
            ("category", b) for b in backtest.by_category
        ]
        buckets = sorted((kb for kb in buckets if kb[1].total > 0), key=lambda kb: -kb[1].total)
        if buckets:
            lines.append(
                f"Historical hit-rates ({backtest.period}, {backtest.horizon_days}-bar horizon, "
                f"{backtest.bars_scanned} bars scanned):"
            )
            lines += [
                f"- {kind}/{b.key}: {round(b.hit_rate * 100)}% (n={b.total})"
                for kind, b in buckets[:_BRIEF_MAX_HIT_RATE_LINES]
            ]
    if signal.data_quality_score is not None:
        reasons = (
            f" ({', '.join(signal.data_quality_reasons)})" if signal.data_quality_reasons else ""
        )
        lines.append(f"Data quality: {signal.data_quality_score:.2f}{reasons}")
    if omitted:
        lines.append(f"Omitted: {'; '.join(omitted)}")
    return "\n".join(lines)


async def brief(
    symbol: str,
    period: str = DEFAULT_PERIOD,
    *,
    include_backtest: bool = True,
    no_llm: bool = True,
    horizon_days: int = BACKTEST_FORWARD_HORIZON_DAYS,
) -> TickerBrief:
    """Signal + state + historical hit-rates for one ticker, as one grounding brief.

    The backtest is best-effort: if it fails the brief still returns, with the
    reason in ``omitted``. A failure of the signal itself raises, like
    :func:`analyze`.

    Args:
        symbol: Ticker symbol.
        period: Analysis period for the live signal.
        include_backtest: Attach 2y hit-rates (cached; slow only when cold).
        no_llm: Default True — grounding wants numbers, not another model's prose.
        horizon_days: Forward-return horizon for the hit-rates.

    Returns:
        A :class:`TickerBrief`.
    """
    from datetime import UTC, datetime

    symbol = _normalize_symbol(symbol)
    period = _normalize_period(period)
    omitted: list[str] = []

    if include_backtest:
        signal_out, bt = await asyncio.gather(
            analyze(symbol, period, no_llm=no_llm),
            backtest(symbol, _BRIEF_BACKTEST_PERIOD, horizon_days),
            return_exceptions=True,
        )
        if isinstance(signal_out, BaseException):
            raise signal_out
        backtest_result: BacktestResult | None
        if isinstance(bt, BaseException):
            backtest_result = None
            omitted.append(f"backtest ({type(bt).__name__})")
        else:
            backtest_result = bt
    else:
        signal_out = await analyze(symbol, period, no_llm=no_llm)
        backtest_result = None
        omitted.append("backtest (not requested)")

    return TickerBrief(
        ticker=symbol,
        period=period,
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        signal=signal_out,
        backtest=backtest_result,
        omitted=omitted,
        text=_format_brief_text(signal_out, backtest_result, omitted),
    )


async def brief_many(
    symbols: Sequence[str],
    period: str = DEFAULT_PERIOD,
    *,
    include_backtest: bool = True,
    no_llm: bool = True,
    max_concurrent: int = _DEFAULT_BATCH_CONCURRENCY,
) -> tuple[list[TickerBrief], list[BatchFailure]]:
    """Fan :func:`brief` out over a basket; one bad symbol never fails the batch."""
    period = _normalize_period(period)
    unique = sorted({_normalize_symbol(s) for s in symbols})
    sem = asyncio.Semaphore(max(1, max_concurrent))

    async def _one(sym: str) -> TickerBrief | BatchFailure:
        async with sem:
            try:
                return await brief(sym, period, include_backtest=include_backtest, no_llm=no_llm)
            except SignalsError as exc:
                return BatchFailure(sym, type(exc).__name__, str(exc))
            except Exception as exc:  # noqa: BLE001 — batch must survive any single failure
                logger.warning("brief_many: %s failed unexpectedly: %s", sym, exc)
                return BatchFailure(sym, "UnexpectedError", str(exc))

    results = await asyncio.gather(*(_one(s) for s in unique))
    briefs = [r for r in results if isinstance(r, TickerBrief)]
    failed = [r for r in results if isinstance(r, BatchFailure)]
    return briefs, failed


def brief_to_rag_document(b: TickerBrief) -> RagDocument:
    """Convert a brief into one upsertable vector-store document.

    The id is stable per (ticker, bar date, period, code version), so re-ingesting
    the same day upserts instead of duplicating, and a new trading day or a
    new engine version produces a new document.
    """
    st = b.signal.state or SignalState()
    as_of = st.as_of or b.generated_at[:10]
    raw: dict[str, RagMetadataValue | None] = {
        "source": "signals-app",
        "doc_type": "signal_brief",
        "ticker": b.ticker,
        "as_of": as_of,
        "period": b.period,
        "direction": b.signal.signal.direction.value,
        "confidence": b.signal.signal.confidence,
        "confluence_score": st.confluence_score,
        "bias": st.bias,
        "action": st.action,
        "rsi": st.rsi,
        "adx": st.adx,
        "close": st.close,
        "has_backtest": b.backtest is not None,
        "code_version": b.signal.code_version,
        "generated_at": b.generated_at,
    }
    metadata = {k: v for k, v in raw.items() if v is not None}
    doc_id = f"signals-app:{b.ticker}:{as_of}:{b.period}:{b.signal.code_version or 'dev'}"
    return RagDocument(id=doc_id, text=b.text, metadata=metadata)


async def rag_documents(
    symbols: Sequence[str],
    period: str = DEFAULT_PERIOD,
    *,
    include_backtest: bool = True,
) -> tuple[list[RagDocument], list[BatchFailure]]:
    """Vector-store-ready documents for a basket (one per ticker)."""
    briefs, failed = await brief_many(symbols, period, include_backtest=include_backtest)
    return [brief_to_rag_document(b) for b in briefs], failed


async def history(symbol: str, *, limit: int = 50, offset: int = 0) -> list[RunRecord]:
    """Return persisted signal runs for a symbol, newest first.

    Args:
        symbol: Ticker symbol.
        limit: Max rows (1–200).
        offset: Pagination offset.

    Returns:
        A list of :class:`RunRecord`.

    Raises:
        UpstreamUnavailable: The history query failed (DB unreachable).
    """
    symbol = _normalize_symbol(symbol)
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    try:
        from signals_app.db.session import init_db

        await init_db()
        return await get_ticker_history(symbol, limit=limit, offset=offset)
    except Exception as exc:  # noqa: BLE001
        logger.error("service.history: query failed ticker=%s: %s", symbol, exc)
        raise UpstreamUnavailable("History query failed") from exc


async def detectors() -> list[DetectorInfo]:
    """Every registered detector: name, category, description, calibrated hit-rate.

    Self-documenting — powers ``signals detectors`` and the MCP ``list_detectors``
    tool. The calibrated hit-rate is per *strength* bucket in the calibration
    table (there is no per-detector table today), matched by the detector's own
    declared category where possible, else ``None``.
    """
    rates = load_strength_hit_rates() or {}
    infos: list[DetectorInfo] = []
    for det in get_default_detectors():
        name = det.__class__.__name__
        category = getattr(det, "category", "") or ""
        doc = (det.__class__.__doc__ or "").strip().splitlines()
        description = doc[0].strip() if doc else ""
        # No per-detector calibration exists yet; expose the table's rate for a
        # matching key if the detector advertises one, else None.
        calibrated = rates.get(category) if category in rates else None
        infos.append(
            DetectorInfo(
                name=name,
                category=category,
                description=description,
                calibrated_hit_rate=calibrated,
            )
        )
    return infos


async def health() -> HealthReport:
    """Reachability of yfinance + which LLM provider is configured.

    A cheap, well-known symbol is fetched with the shortest period to probe
    the data path. Supabase / LLM are reported by configuration presence only
    — this call must stay fast and side-effect-free.
    """
    settings = get_settings()
    detail: dict[str, str] = {}

    yfinance_ok = False
    try:
        fetcher = DataFetcher(settings=settings)
        df = fetcher.fetch("AAPL", "5d").df
        yfinance_ok = len(df) > 0
        detail["yfinance"] = f"ok ({len(df)} bars for AAPL/5d)"
    except Exception as exc:  # noqa: BLE001 — health probe reports failure, never raises
        detail["yfinance"] = f"unreachable: {exc}"

    from signals_app.config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL

    supabase_configured = bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)
    detail["supabase"] = "configured" if supabase_configured else "not configured"
    detail["llm"] = (
        f"{settings.llm_provider} (configured)"
        if settings.llm_enabled
        else "not configured (rule-based only)"
    )

    return HealthReport(
        yfinance_ok=yfinance_ok,
        llm_provider=settings.llm_provider,
        llm_configured=settings.llm_enabled,
        supabase_configured=supabase_configured,
        code_version=SIGNALS_APP_CODE_VERSION,
        detail=detail,
    )


# ---------------------------------------------------------------------------
# scan — the production universe scan (design doc §2.1, build step 4)
# ---------------------------------------------------------------------------


async def scan(
    symbols: Sequence[str] | None = None,
    *,
    seed: Path | str | None = None,
    period: str = DEFAULT_PERIOD,
    dry_run: bool = False,
    trigger: Literal["cron", "manual", "backfill"] = "manual",
    shard: tuple[int, int] | None = None,
    max_concurrent: int = 8,
    compute_matrix: bool = False,
    direction: Literal["bullish", "bearish"] | None = None,
    progress: Callable[[ScanProgress], None] | None = None,
) -> ScanResult:
    """Run the production scan over a universe and persist publishable signals.

    Wraps ``signals_app.scanner.scan_universe`` — the same code the GitHub
    Actions workflow runs via ``scripts/scan_universe.py`` — adding symbol
    resolution (direct list + ``seed`` CSV + ``shard``) and a typed result.
    The heavy work runs in a worker thread so this stays a normal coroutine.

    Args:
        symbols: Tickers to scan. Combined with ``seed`` if both are given.
        seed: Path to a CSV with a ``ticker`` column.
        period: yfinance period string; validated.
        dry_run: Gate + log only — no LLM calls, no writes. This is the
            ``--dry-run`` measurement ``docs/universe-scan-findings.md`` relies
            on.
        trigger: Recorded on the ``engine_runs`` row.
        shard: ``(index, total)`` — scan only every ``total``-th symbol from
            index, from the sorted list (what Actions does across 4 shards).
        max_concurrent: Bounded fetch concurrency.
        compute_matrix: Also build the 5-timeframe matrix for gated symbols.
        direction: Gate one side of the confluence band only.
        progress: Called with a :class:`ScanProgress` as each symbol finishes.

    Returns:
        A :class:`ScanResult`.

    Raises:
        InvalidPeriod: The period is not supported.
        SymbolNotFound: No symbols were resolved from ``symbols`` + ``seed``.
        UpstreamUnavailable: Supabase writer construction failed for a live run.
    """
    period = _normalize_period(period)

    from signals_app import scanner

    resolved: list[str] = [_normalize_symbol(s) for s in (symbols or [])]
    if seed is not None:
        resolved.extend(scanner.load_symbols_from_csv(str(seed)))
    resolved = sorted(set(resolved))
    if not resolved:
        raise SymbolNotFound("no symbols resolved — pass symbols and/or a seed CSV")

    if shard is not None:
        index, total = shard
        resolved = scanner.apply_shard(resolved, index, total)

    writer = None
    if not dry_run:
        try:
            from signals_app.db.supabase import SupabaseWriter

            writer = SupabaseWriter()
        except Exception as exc:  # noqa: BLE001 — a live scan with no writer is an upstream problem
            logger.error("service.scan: could not construct SupabaseWriter: %s", exc)
            raise UpstreamUnavailable(f"Supabase writer unavailable: {exc}") from exc

    def _on_symbol(done: int, total: int, result: object) -> None:
        if progress is None:
            return
        progress(
            ScanProgress(
                done=done,
                total=total,
                ticker=result.ticker,  # type: ignore[attr-defined]
                ok=result.ok,  # type: ignore[attr-defined]
                published=result.published,  # type: ignore[attr-defined]
                reason=result.reason,  # type: ignore[attr-defined]
            )
        )

    started = asyncio.get_event_loop().time()
    try:
        results = await asyncio.to_thread(
            scanner.scan_universe,
            resolved,
            period=period,
            writer=writer,
            trigger=trigger,
            dry_run=dry_run,
            max_concurrent=max_concurrent,
            compute_matrix=compute_matrix,
            direction=direction,
            progress=_on_symbol,
        )
    finally:
        if writer is not None:
            writer.close()
    elapsed = asyncio.get_event_loop().time() - started

    ok = sum(1 for r in results if r.ok)
    published = sum(1 for r in results if r.published)
    return ScanResult(
        symbols_total=len(results),
        symbols_ok=ok,
        symbols_failed=len(results) - ok,
        symbols_published=published,
        dry_run=dry_run,
        trigger=trigger,
        elapsed_seconds=round(elapsed, 2),
        outcomes=[
            ScanSymbolOutcome(ticker=r.ticker, ok=r.ok, published=r.published, reason=r.reason)
            for r in results
        ],
    )
