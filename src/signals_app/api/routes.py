"""FastAPI route definitions — thin adapter over ``signals_app.service``.

Every route here does exactly three things: parse the request, call one
``service`` function, and translate ``SignalsError`` subclasses into
``HTTPException``. No pipeline logic lives in this module any more — see
``docs/signals-app-docs/signals-as-api-cli-mcp.md`` §2.

GET /signals/{symbol}   — full L1–L5 pipeline for one symbol
GET /history/{symbol}    — persisted run history for a ticker
GET /backtest/{symbol}   — historical hit-rate backtest
GET /health              — liveness probe
POST /scan               — real universe scan, publishes to Supabase
POST /backtest/run       — basket backtest, optionally judged against a hypothesis
POST /backtest/suggest   — engine-proposed backtests from a basket's live signals
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from signals_app import service
from signals_app.config import (
    BACKTEST_FORWARD_HORIZON_DAYS,
    DEFAULT_PERIOD,
    MAX_MANUAL_BACKTEST_SYMBOLS,
    MAX_MANUAL_SCAN_SYMBOLS,
    MAX_SUGGEST_BACKTEST_SYMBOLS,
    VALID_PERIODS,
)
from backtests.engine import HitRateBucket, bucket_baseline
from signals_app.hypotheses import (
    BacktestHypothesis,
    FocusVerdict,
    HypothesisFocus,
    HypothesisVerdict,
    wilson_interval,
)
from signals_app.schemas.signal_output import SignalOutput
from signals_app.service import (
    InsufficientData,
    InvalidPeriod,
    SymbolNotFound,
    UpstreamUnavailable,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _raise_http(exc: Exception) -> HTTPException:
    """Translate a service domain exception into the matching HTTPException.

    Mapping (mirrors the CLI exit-code contract in the design doc §3.3):
        InvalidPeriod       -> 400
        SymbolNotFound      -> 404
        InsufficientData    -> 400
        UpstreamUnavailable -> 503
        anything else       -> 500
    """
    if isinstance(exc, InvalidPeriod):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, SymbolNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, InsufficientData):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, UpstreamUnavailable):
        return HTTPException(status_code=503, detail=str(exc))
    logger.error("unhandled error in route: %s", exc, exc_info=True)
    return HTTPException(status_code=500, detail=str(exc))


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe.

    Returns:
        Status OK response.
    """
    return {"status": "ok"}


@router.get(
    "/signals/{symbol}",
    response_model=SignalOutput,
    summary="Full pipeline signal analysis",
    description=(
        "Runs the complete pipeline for a symbol: "
        "fetch OHLCV → compute indicators → detect signals → "
        "confluence rank → multi-timeframe score → LLM synthesis. "
        "Returns a structured Signal with evidence."
    ),
)
async def get_signals(
    symbol: str,
    period: str = Query(
        default=DEFAULT_PERIOD,
        description=f"Analysis period. One of: {', '.join(VALID_PERIODS)}",
    ),
    no_llm: bool = Query(default=False, description="Skip LLM synthesis, return rule-based signal"),
) -> SignalOutput:
    """Run the full signal pipeline for a symbol.

    Args:
        symbol: Stock ticker symbol (e.g., AAPL).
        period: yfinance period string (default: 3mo).
        no_llm: If True, skip LLM synthesis and return a rule-based signal.

    Returns:
        SignalOutput with synthesized directional signal and evidence.

    Raises:
        HTTPException: 400 invalid period / insufficient data, 404 unknown
            symbol, 503 upstream unavailable, 500 otherwise.
    """
    try:
        return await service.analyze(symbol, period, no_llm=no_llm)
    except Exception as exc:  # noqa: BLE001 — translated to HTTP below
        raise _raise_http(exc) from exc


@router.get(
    "/history/{symbol}",
    summary="Signal run history for a ticker",
    description="Returns recent analysis runs for a ticker from the SQL DB, newest first.",
)
async def get_history(
    symbol: str,
    limit: int = Query(default=50, ge=1, le=200, description="Max rows to return"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
) -> list[dict]:
    """Return persisted signal runs for a symbol.

    Args:
        symbol: Stock ticker symbol.
        limit: Maximum rows (1–200, default 50).
        offset: Pagination offset.

    Returns:
        List of run dicts, newest first. Each matches the frontend HistoryEntry shape.
    """
    try:
        rows = await service.history(symbol, limit=limit, offset=offset)
    except Exception as exc:  # noqa: BLE001
        raise _raise_http(exc) from exc
    return [r.to_dict() for r in rows]


@router.get(
    "/backtest/{symbol}",
    summary="Historical hit-rate backtest",
    description=(
        "Runs every detector against every historical bar (not just the latest) "
        "and scores each directional signal against its realized forward return. "
        "Answers: does a HIGH confidence label actually mean a higher hit-rate?"
    ),
)
async def get_backtest(
    symbol: str,
    period: str = Query(
        default="2y", description=f"Analysis period. One of: {', '.join(VALID_PERIODS)}"
    ),
    horizon_days: int = Query(
        default=BACKTEST_FORWARD_HORIZON_DAYS,
        ge=1,
        le=60,
        description="Forward-return horizon in trading days",
    ),
) -> dict[str, Any]:
    """Backtest a symbol's historical signals against realized forward returns.

    Args:
        symbol: Stock ticker symbol.
        period: yfinance period string — long enough to clear the indicator
            warmup plus a meaningful scan window (default: 2y).
        horizon_days: Bars ahead used to measure the realized return.

    Returns:
        Dict with bars_scanned and hit-rate buckets by category and strength.

    Raises:
        HTTPException: 400 invalid period / insufficient history, 404 unknown
            symbol, 503 upstream unavailable, 500 otherwise.
    """
    try:
        result = await service.backtest(symbol, period, horizon_days)
    except Exception as exc:  # noqa: BLE001
        raise _raise_http(exc) from exc

    return {
        "symbol": result.symbol,
        "period": result.period,
        "horizon_days": result.horizon_days,
        "bars_scanned": result.bars_scanned,
        "by_category": [
            {"key": b.key, "hits": b.hits, "total": b.total, "hit_rate": round(b.hit_rate, 4)}
            for b in result.by_category
        ],
        "by_strength": [
            {"key": b.key, "hits": b.hits, "total": b.total, "hit_rate": round(b.hit_rate, 4)}
            for b in result.by_strength
        ],
    }


class ScanRequest(BaseModel):
    """Body for ``POST /scan`` — a manually-triggered real scan of a basket."""

    symbols: list[str] = Field(
        min_length=1,
        max_length=MAX_MANUAL_SCAN_SYMBOLS,
        description=f"Tickers to scan (1–{MAX_MANUAL_SCAN_SYMBOLS}).",
    )
    period: str = DEFAULT_PERIOD
    dry_run: bool = False
    compute_matrix: bool = False


@router.post(
    "/scan",
    summary="Trigger a real scan for a set of tickers",
    description=(
        "Runs the same production scan the GitHub Actions workflow runs "
        "(fetch → detect → confluence gate → LLM synthesis → publish), but "
        "synchronously over an explicit ticker list, `trigger='manual'`. "
        "Writes publishable signals straight to Supabase — the frontend's "
        "existing 'run basket' read then sees fresh rows. Requires "
        "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY on the server unless "
        "dry_run is set."
    ),
)
async def post_scan(body: ScanRequest) -> dict[str, Any]:
    """Run a real, synchronous scan over ``body.symbols`` and publish results.

    Args:
        body: Tickers plus scan options. Capped at
            ``MAX_MANUAL_SCAN_SYMBOLS`` — a manual trigger is meant for a
            basket, not the full seed universe (use the scheduled/sharded
            GitHub Actions workflow for that).

    Returns:
        A dict mirroring :class:`signals_app.service.ScanResult`, plus a
        per-symbol ``outcomes`` list (ticker, ok, published, reason).

    Raises:
        HTTPException: 400 invalid period, 503 Supabase writer unavailable,
            500 otherwise.
    """
    try:
        result = await service.scan(
            symbols=body.symbols,
            period=body.period,
            dry_run=body.dry_run,
            trigger="manual",
            compute_matrix=body.compute_matrix,
        )
    except Exception as exc:  # noqa: BLE001
        raise _raise_http(exc) from exc

    return {
        "symbols_total": result.symbols_total,
        "symbols_ok": result.symbols_ok,
        "symbols_failed": result.symbols_failed,
        "symbols_published": result.symbols_published,
        "dry_run": result.dry_run,
        "trigger": result.trigger,
        "elapsed_seconds": round(result.elapsed_seconds, 2),
        "outcomes": [
            {
                "ticker": o.ticker,
                "ok": o.ok,
                "published": o.published,
                "reason": o.reason,
            }
            for o in result.outcomes
        ],
    }


# ---------------------------------------------------------------------------
# Frontend-driven backtests (/backtest/run, /backtest/suggest)
# ---------------------------------------------------------------------------
class FocusModel(BaseModel):
    """One bucket a hypothesis is about — e.g. ``{"group": "signal", "key": "GOLDEN CROSS"}``."""

    group: Literal["signal", "category", "strength"]
    key: str = Field(min_length=1, max_length=120)


class BacktestRunRequest(BaseModel):
    """Body for ``POST /backtest/run``."""

    symbols: list[str] = Field(min_length=1, max_length=MAX_MANUAL_BACKTEST_SYMBOLS)
    period: str = "2y"
    horizon_days: int = Field(default=20, ge=1, le=60)
    focus: list[FocusModel] = Field(default_factory=list, max_length=4)


class BacktestSuggestRequest(BaseModel):
    """Body for ``POST /backtest/suggest``."""

    symbols: list[str] = Field(min_length=1, max_length=MAX_SUGGEST_BACKTEST_SYMBOLS)
    period: str = "2y"
    horizon_days: int | None = Field(default=None, ge=1, le=60)
    max_suggestions: int = Field(default=8, ge=1, le=20)


def _bucket_json(b: HitRateBucket, up_rate: float | None) -> dict[str, Any]:
    lower, upper = wilson_interval(b.hits, b.total)
    return {
        "key": b.key,
        "hits": b.hits,
        "total": b.total,
        "bullish": b.bullish,
        "hit_rate": round(b.hit_rate, 4),
        "lower": round(lower, 4),
        "upper": round(upper, 4),
        "baseline": round(bucket_baseline(b, up_rate), 4) if up_rate is not None else None,
    }


def _focus_verdict_json(v: FocusVerdict) -> dict[str, Any]:
    def r(x: float | None) -> float | None:
        return round(x, 4) if x is not None else None

    return {
        "group": v.focus.group,
        "key": v.focus.key,
        "status": v.status,
        "hits": v.hits,
        "total": v.total,
        "hit_rate": r(v.hit_rate),
        "lower": r(v.lower),
        "upper": r(v.upper),
        "baseline": r(v.baseline),
        "message": v.message,
    }


def _verdict_json(v: HypothesisVerdict | None) -> dict[str, Any] | None:
    if v is None:
        return None
    return {
        "status": v.status,
        "message": v.message,
        "focuses": [_focus_verdict_json(f) for f in v.focuses],
    }


def _hypothesis_json(h: BacktestHypothesis) -> dict[str, Any]:
    return {
        "id": h.id,
        "kind": h.kind,
        "title": h.title,
        "rationale": h.rationale,
        "symbols": list(h.symbols),
        "period": h.period,
        "horizon_days": h.horizon_days,
        "focus": [{"group": f.group, "key": f.key} for f in h.focus],
        "priority": round(h.priority, 3),
    }


@router.post(
    "/backtest/run",
    summary="Backtest a basket, optionally judging a hypothesis",
    description=(
        "Replays every detector over every historical bar for each ticker "
        "and scores each directional call against its realized forward return. "
        "Buckets carry Wilson 95% bounds and a mix-weighted chance baseline. "
        "Pass `focus` (e.g. a hypothesis from /backtest/suggest) to get a "
        "supported / contradicted / inconclusive verdict. Read-only — writes "
        f"nothing. Capped at {MAX_MANUAL_BACKTEST_SYMBOLS} tickers."
    ),
)
async def post_backtest_run(body: BacktestRunRequest) -> dict[str, Any]:
    """Run a basket backtest and serialize buckets plus an optional verdict.

    Raises:
        HTTPException: 400 invalid period, 500 otherwise.
    """
    try:
        run = await service.run_hypothesis(
            body.symbols,
            body.period,
            body.horizon_days,
            [HypothesisFocus(f.group, f.key) for f in body.focus],
        )
    except Exception as exc:  # noqa: BLE001
        raise _raise_http(exc) from exc

    res = run.result
    up = res.up_rate
    return {
        "symbols_ok": res.symbols_ok,
        "symbols_failed": [
            {"symbol": f.symbol, "error_type": f.error_type, "message": f.message}
            for f in res.symbols_failed
        ],
        "period": res.period,
        "horizon_days": res.horizon_days,
        "up_rate": round(up, 4) if up is not None else None,
        "scored_bars": res.scored_bars,
        "by_signal": [_bucket_json(b, up) for b in res.by_signal],
        "by_category": [_bucket_json(b, up) for b in res.by_category],
        "by_strength": [_bucket_json(b, up) for b in res.by_strength],
        "verdict": _verdict_json(run.verdict),
    }


@router.post(
    "/backtest/suggest",
    summary="Engine-suggested backtests for a basket",
    description=(
        "Detects each ticker's live signals on its latest bar (rule-based, no "
        "LLM) and proposes backtests that test the claims those signals make: "
        "clusters firing across the basket, per-ticker strongest calls, "
        "bull/bear conflicts, the dominant category, and whether STRONG labels "
        "are earned. Each suggestion is a ready-to-POST /backtest/run spec."
    ),
)
async def post_backtest_suggest(body: BacktestSuggestRequest) -> dict[str, Any]:
    """Return hypothesis specs for ``body.symbols``.

    Raises:
        HTTPException: 400 invalid period, 500 otherwise.
    """
    try:
        sug = await service.suggest_backtests(
            body.symbols,
            backtest_period=body.period,
            horizon_days=body.horizon_days,
            max_suggestions=body.max_suggestions,
        )
    except Exception as exc:  # noqa: BLE001
        raise _raise_http(exc) from exc

    return {
        "hypotheses": [_hypothesis_json(h) for h in sug.hypotheses],
        "symbols_ok": sug.symbols_ok,
        "symbols_failed": [
            {"symbol": f.symbol, "error_type": f.error_type, "message": f.message}
            for f in sug.symbols_failed
        ],
        "live_signals": sug.live_signals,
        "max_backtest_symbols": MAX_MANUAL_BACKTEST_SYMBOLS,
    }
