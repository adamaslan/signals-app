"""FastAPI route definitions.

GET /signals/{symbol}   — full pipeline: fetch → indicators → detect → score → synthesize
GET /health             — liveness probe
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from signals_app.config import DEFAULT_PERIOD, VALID_PERIODS, get_settings
from signals_app.data.fetcher import DataFetcher
from signals_app.detection.orchestrator import detect_all_signals
from signals_app.indicators.compute import compute_indicators
from signals_app.schemas.signal_output import SignalOutput
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

    # L4: Confluence scoring
    try:
        ranker = ConfluenceRanker()
        confluence_result = ranker.rank_signals(list(signal_list))
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

    return SignalOutput(
        ticker=symbol,
        signal=primary_signal,
        matrix=None,
        feature_unavailable=unavailable,
        schema_version="1.0",
    )
