"""FastAPI route definitions — thin adapter over ``signals_app.service``.

Every route here does exactly three things: parse the request, call one
``service`` function, and translate ``SignalsError`` subclasses into
``HTTPException``. No pipeline logic lives in this module any more — see
``docs/signals-app-docs/signals-as-api-cli-mcp.md`` §2.

GET /signals/{symbol}   — full L1–L5 pipeline for one symbol
GET /history/{symbol}    — persisted run history for a ticker
GET /backtest/{symbol}   — historical hit-rate backtest
GET /health              — liveness probe
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from signals_app import service
from signals_app.config import (
    BACKTEST_FORWARD_HORIZON_DAYS,
    DEFAULT_PERIOD,
    VALID_PERIODS,
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
