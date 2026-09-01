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
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from backtests.engine import (
    HitRateBucket,
    merge_hit_rate_buckets,
    score_historical_signals,
)
from signals_app.config import (
    BACKTEST_FORWARD_HORIZON_DAYS,
    DEFAULT_PERIOD,
    MIN_HISTORICAL_LOOKBACK,
    SIGNALS_APP_CODE_VERSION,
    VALID_PERIODS,
    get_settings,
)
from signals_app.data.fetcher import DataFetcher
from signals_app.db.ops import RunRecord, get_ticker_history, record_run
from signals_app.detection.historical import scan_historical
from signals_app.detection.orchestrator import detect_all_signals, get_default_detectors
from signals_app.indicators.compute import compute_indicators
from signals_app.indicators.data_quality import score_data_quality
from signals_app.schemas.signal_output import Signal, SignalOutput
from signals_app.scoring.calibration import load_strength_hit_rates
from signals_app.scoring.confluence import ConfluenceRanker
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
        ranker = ConfluenceRanker()
        confluence_result = ranker.rank_signals(
            list(signal_list), strength_hit_rates=strength_hit_rates
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

    return SignalOutput(
        ticker=symbol,
        signal=primary_signal,
        matrix=None,
        feature_unavailable=unavailable,
        schema_version="1.0",
        code_version=SIGNALS_APP_CODE_VERSION,
        data_quality_score=data_quality.score,
        data_quality_reasons=data_quality.reasons,
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

    try:
        fetcher = DataFetcher(settings=settings)
        df_raw = fetcher.fetch(symbol, period).df
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

    return BacktestResult(
        symbol=symbol,
        period=period,
        horizon_days=horizon_days,
        bars_scanned=len(bars),
        by_category=list(scored["by_category"]),
        by_strength=list(scored["by_strength"]),
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
        return UniverseBacktestResult([], [], horizon_days, [], [])

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
    for sym, outcome in results:
        if isinstance(outcome, BatchFailure):
            failed.append(outcome)
        else:
            ok_syms.append(sym)
            cat_lists.append(outcome.by_category)
            strength_lists.append(outcome.by_strength)

    merged_cat = merge_hit_rate_buckets(cat_lists) if cat_lists else []
    merged_strength = merge_hit_rate_buckets(strength_lists) if strength_lists else []

    logger.info(
        "backtest_many: %d ok, %d failed of %d", len(ok_syms), len(failed), len(unique)
    )
    return UniverseBacktestResult(
        symbols_ok=ok_syms,
        symbols_failed=failed,
        horizon_days=horizon_days,
        by_category=merged_cat,
        by_strength=merged_strength,
    )


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
