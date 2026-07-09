"""FastAPI route definitions.

GET /signals/{symbol}   — full pipeline: fetch → indicators → detect → score → synthesize
GET /health             — liveness probe
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backtests.engine import score_historical_signals
from signals_app.config import (
    BACKTEST_FORWARD_HORIZON_DAYS,
    DEFAULT_PERIOD,
    MIN_HISTORICAL_LOOKBACK,
    SIGNALS_APP_CODE_VERSION,
    VALID_PERIODS,
    get_settings,
)
from signals_app.data.fetcher import DataFetcher
from signals_app.db.ops import get_ticker_history, record_run
from signals_app.detection.historical import scan_historical
from signals_app.detection.orchestrator import detect_all_signals
from signals_app.indicators.compute import compute_indicators
from signals_app.indicators.data_quality import score_data_quality
from signals_app.schemas.signal_output import SignalOutput
from signals_app.scoring.calibration import load_strength_hit_rates
from signals_app.scoring.confluence import ConfluenceRanker
from signals_app.scoring.mtf import compute_multi_timeframe
from signals_app.synthesis.mtf_llm import build_timeframe_matrix, synthesize_single

logger = logging.getLogger(__name__)

router = APIRouter()


def _build_features(symbol: str, period: str, confluence_result: Any, df: Any) -> dict[str, Any]:
    """Build a feature dict for LLM synthesis from pipeline results.

    Args:
        symbol: Ticker symbol.
        period: Period string.
        confluence_result: ConfluenceResult from scoring layer.
        df: Indicator DataFrame (most recent bar).

    Returns:
        Feature dict for the LLM prompt.
    """
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
                    import math
                    v = float(val)
                    features[col_key.lower()] = round(v, 4) if not (math.isnan(v) or math.isinf(v)) else None
            except Exception:
                pass

    return features


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
    period: str = Query(default=DEFAULT_PERIOD, description=f"Analysis period. One of: {', '.join(VALID_PERIODS)}"),
    no_llm: bool = Query(default=False, description="Skip LLM synthesis, return confluence-only signal"),
) -> SignalOutput:
    """Run the full signal pipeline for a symbol.

    Args:
        symbol: Stock ticker symbol (e.g., AAPL).
        period: yfinance period string (default: 3mo).
        no_llm: If True, skip LLM synthesis and return rule-based signal.

    Returns:
        SignalOutput with synthesized directional signal and evidence.

    Raises:
        HTTPException 400: If symbol or period is invalid.
        HTTPException 500: If the pipeline fails unexpectedly.
    """
    symbol = symbol.upper().strip()
    period = period.lower().strip()

    if period not in VALID_PERIODS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid period '{period}'. Valid periods: {list(VALID_PERIODS)}",
        )

    settings = get_settings()
    logger.info("GET /signals/%s period=%s no_llm=%s", symbol, period, no_llm)

    # L1: Fetch data
    try:
        fetcher = DataFetcher(settings=settings)
        ohlcv_result = fetcher.fetch(symbol, period)
        df_raw = ohlcv_result.df
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("Data fetch failed for %s: %s", symbol, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Data fetch error: {exc}")

    if len(df_raw) < 20:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient data for {symbol} period={period}: only {len(df_raw)} bars",
        )

    data_quality = score_data_quality(df_raw, period)

    # L2: Compute indicators
    try:
        df = compute_indicators(df_raw)
    except Exception as exc:
        logger.error("Indicator compute failed for %s: %s", symbol, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Indicator compute error: {exc}")

    # L3: Detect signals
    try:
        signal_list = detect_all_signals(df)
    except Exception as exc:
        logger.error("Signal detection failed for %s: %s", symbol, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Signal detection error: {exc}")

    # L4: Confluence scoring — calibrated against measured backtest hit-rates
    # when scripts/calibrate.py has produced a table; None otherwise (safe default).
    try:
        strength_hit_rates = load_strength_hit_rates()
        ranker = ConfluenceRanker()
        confluence_result = ranker.rank_signals(list(signal_list), strength_hit_rates=strength_hit_rates)
    except Exception as exc:
        logger.error("Confluence scoring failed for %s: %s", symbol, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Confluence error: {exc}")

    # Map period to timeframe label
    period_to_timeframe: dict[str, str] = {
        "1d": "1D", "5d": "5D", "1mo": "1M", "3mo": "3M", "6mo": "6M", "1y": "1Y",
    }
    timeframe_label = period_to_timeframe.get(period, "1D")

    # L5: LLM synthesis
    features = _build_features(symbol, period, confluence_result, df)
    unavailable: list[str] = []

    if signal_list.degraded:
        unavailable.append("detection_degraded")

    try:
        if no_llm or not settings.llm_enabled:
            if not no_llm:
                unavailable.append("llm_synthesis")
            primary_signal = synthesize_single(
                ticker=symbol,
                timeframe=timeframe_label,
                features=features,
                settings=settings,
            )
        else:
            # Build single-timeframe signal
            primary_signal = synthesize_single(
                ticker=symbol,
                timeframe=timeframe_label,
                features=features,
                settings=settings,
            )
    except Exception as exc:
        logger.error("Synthesis failed for %s: %s", symbol, exc, exc_info=True)
        # Return a degraded fallback rather than 500
        from signals_app.synthesis.mtf_llm import _fallback_signal
        from signals_app.schemas.signal_output import Signal
        fallback_dict = _fallback_signal(timeframe_label, features)
        fallback_dict["timeframe"] = timeframe_label
        primary_signal = Signal.model_validate(fallback_dict)
        unavailable.append("synthesis_error")

    # Persist the run to the SQL DB (fire-and-forget; errors are logged, not raised)
    try:
        await record_run(
            ticker=symbol,
            period=period,
            resolved_period=period,
            direction=primary_signal.direction.value if primary_signal.direction is not None else None,
            confidence=primary_signal.confidence,
            ai_degraded=primary_signal.ai_degraded,
            no_llm=no_llm,
            prompt_version=primary_signal.prompt_version,
        )
    except Exception as exc:
        logger.warning("db: failed to record run ticker=%s: %s", symbol, exc)

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
    symbol = symbol.upper().strip()
    try:
        rows = await get_ticker_history(symbol, limit=limit, offset=offset)
    except Exception as exc:
        logger.error("db: get_ticker_history failed ticker=%s: %s", symbol, exc)
        raise HTTPException(status_code=500, detail="History query failed")
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
    period: str = Query(default="2y", description=f"Analysis period. One of: {', '.join(VALID_PERIODS)}"),
    horizon_days: int = Query(
        default=BACKTEST_FORWARD_HORIZON_DAYS, ge=1, le=60, description="Forward-return horizon in trading days"
    ),
) -> dict[str, Any]:
    """Backtest a symbol's historical signals against realized forward returns.

    Args:
        symbol: Stock ticker symbol.
        period: yfinance period string — should be long enough to clear
            MIN_HISTORICAL_LOOKBACK plus a meaningful scan window (default: 2y).
        horizon_days: Bars ahead used to measure the realized return.

    Returns:
        Dict with bars_scanned and hit-rate buckets by category and strength.

    Raises:
        HTTPException 400: If symbol/period is invalid or there isn't enough
            history to clear the indicator warmup period.
    """
    symbol = symbol.upper().strip()
    period = period.lower().strip()

    if period not in VALID_PERIODS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid period '{period}'. Valid periods: {list(VALID_PERIODS)}",
        )

    settings = get_settings()
    logger.info("GET /backtest/%s period=%s horizon_days=%d", symbol, period, horizon_days)

    try:
        fetcher = DataFetcher(settings=settings)
        df_raw = fetcher.fetch(symbol, period).df
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("Data fetch failed for %s: %s", symbol, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Data fetch error: {exc}")

    if len(df_raw) <= MIN_HISTORICAL_LOOKBACK + horizon_days:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Insufficient data for {symbol} period={period}: {len(df_raw)} bars, "
                f"need > {MIN_HISTORICAL_LOOKBACK + horizon_days} (warmup + horizon)"
            ),
        )

    try:
        df = compute_indicators(df_raw)
        bars = scan_historical(df)
        result = score_historical_signals(df, bars, horizon_days=horizon_days)
    except Exception as exc:
        logger.error("Backtest failed for %s: %s", symbol, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Backtest error: {exc}")

    return {
        "symbol": symbol,
        "period": period,
        "horizon_days": horizon_days,
        "bars_scanned": len(bars),
        "by_category": [
            {"key": b.key, "hits": b.hits, "total": b.total, "hit_rate": round(b.hit_rate, 4)}
            for b in result["by_category"]
        ],
        "by_strength": [
            {"key": b.key, "hits": b.hits, "total": b.total, "hit_rate": round(b.hit_rate, 4)}
            for b in result["by_strength"]
        ],
    }
