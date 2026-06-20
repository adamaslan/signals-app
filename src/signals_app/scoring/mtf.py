"""Multi-timeframe weighted composite scoring.

Runs the full indicator → detection → confluence pipeline across multiple
timeframes and produces a weighted composite result.

Timeframe weights: shorter timeframes (1D/5D) are less weighted in the composite
because they are noisier; longer timeframes carry more structural conviction.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Final

import pandas as pd

from signals_app.config import TIMEFRAME_CACHE_TTL_SECONDS
from signals_app.detection.orchestrator import detect_all_signals
from signals_app.indicators.compute import compute_indicators
from signals_app.scoring.confluence import ConfluenceRanker, ConfluenceResult

logger = logging.getLogger(__name__)

# Relative weights for each timeframe in the composite score
TIMEFRAME_WEIGHTS: Final[dict[str, float]] = {
    "1D": 0.10,
    "5D": 0.15,
    "1M": 0.25,
    "3M": 0.30,
    "6M": 0.20,
}

SUPPORTED_TIMEFRAMES: Final[tuple[str, ...]] = ("1D", "5D", "1M", "3M", "6M")


@dataclass
class TimeframeScore:
    """Single-timeframe confluence result with metadata.

    Attributes:
        timeframe: Timeframe label.
        result: ConfluenceResult for this timeframe.
        bar_count: Number of OHLCV bars used.
        degraded: True if the detection was degraded.
    """

    timeframe: str
    result: ConfluenceResult
    bar_count: int
    degraded: bool


@dataclass
class MultiTimeframeResult:
    """Composite multi-timeframe scoring result.

    Attributes:
        symbol: Ticker symbol.
        composite_score: Weighted average confluence score in [-1, 1].
        dominant_action: Plurality action across all timeframes.
        timeframe_scores: Per-timeframe results.
        timeframes_available: Which timeframes had sufficient data.
        any_degraded: True if any timeframe detection was degraded.
    """

    symbol: str
    composite_score: float
    dominant_action: str
    timeframe_scores: dict[str, TimeframeScore]
    timeframes_available: list[str]
    any_degraded: bool

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary.

        Returns:
            Plain dictionary representation.
        """
        return {
            "symbol": self.symbol,
            "composite_score": self.composite_score,
            "dominant_action": self.dominant_action,
            "timeframes_available": self.timeframes_available,
            "any_degraded": self.any_degraded,
            "timeframe_scores": {
                tf: {
                    "score": ts.result.score,
                    "action": ts.result.action,
                    "bias": ts.result.bias,
                    "confidence_label": ts.result.confidence_label,
                    "bull_count": ts.result.bull_count,
                    "bear_count": ts.result.bear_count,
                    "bar_count": ts.bar_count,
                    "degraded": ts.degraded,
                }
                for tf, ts in self.timeframe_scores.items()
            },
        }


def _dominant_action(scores: dict[str, TimeframeScore]) -> str:
    """Determine the plurality action across timeframes, weighted by timeframe weight.

    Args:
        scores: Dict of timeframe → TimeframeScore.

    Returns:
        "BUY", "SELL", or "HOLD".
    """
    buy_weight = 0.0
    sell_weight = 0.0
    hold_weight = 0.0

    for tf, ts in scores.items():
        w = TIMEFRAME_WEIGHTS.get(tf, 0.1)
        if ts.result.action == "BUY":
            buy_weight += w
        elif ts.result.action == "SELL":
            sell_weight += w
        else:
            hold_weight += w

    if buy_weight > sell_weight and buy_weight > hold_weight:
        return "BUY"
    if sell_weight > buy_weight and sell_weight > hold_weight:
        return "SELL"
    return "HOLD"


def score_single_timeframe(
    df: pd.DataFrame,
    timeframe: str,
    symbol: str,
) -> TimeframeScore | None:
    """Run the full indicator → detection → confluence pipeline for one timeframe.

    Args:
        df: Raw OHLCV DataFrame for this timeframe.
        timeframe: Timeframe label.
        symbol: Ticker symbol (used in logging).

    Returns:
        TimeframeScore or None if the DataFrame is too small.
    """
    if len(df) < 20:
        logger.warning(
            "mtf: %s %s — insufficient bars (%d), skipping", symbol, timeframe, len(df)
        )
        return None

    try:
        df_with_indicators = compute_indicators(df)
        signal_list = detect_all_signals(df_with_indicators)
        ranker = ConfluenceRanker()
        result = ranker.rank_signals(list(signal_list))

        return TimeframeScore(
            timeframe=timeframe,
            result=result,
            bar_count=len(df),
            degraded=signal_list.degraded,
        )
    except Exception as exc:
        logger.error(
            "mtf: %s %s pipeline error: %s", symbol, timeframe, exc, exc_info=True
        )
        return None


def compute_multi_timeframe(
    symbol: str,
    dfs_by_timeframe: dict[str, pd.DataFrame],
) -> MultiTimeframeResult:
    """Compute multi-timeframe weighted composite confluence score.

    Args:
        symbol: Ticker symbol.
        dfs_by_timeframe: Map of timeframe string to OHLCV DataFrame.
            Each DataFrame must have Open, High, Low, Close, Volume columns.

    Returns:
        MultiTimeframeResult with composite score and per-timeframe breakdowns.
    """
    timeframe_scores: dict[str, TimeframeScore] = {}
    available: list[str] = []

    for tf in SUPPORTED_TIMEFRAMES:
        df = dfs_by_timeframe.get(tf)
        if df is None:
            logger.debug("mtf: %s %s — no data provided, skipping", symbol, tf)
            continue

        ts = score_single_timeframe(df, tf, symbol)
        if ts is not None:
            timeframe_scores[tf] = ts
            available.append(tf)

    if not timeframe_scores:
        logger.warning("mtf: %s — no timeframes scored, returning zero composite", symbol)
        return MultiTimeframeResult(
            symbol=symbol,
            composite_score=0.0,
            dominant_action="HOLD",
            timeframe_scores={},
            timeframes_available=[],
            any_degraded=False,
        )

    # Weighted composite score
    total_weight = sum(TIMEFRAME_WEIGHTS.get(tf, 0.1) for tf in available)
    if total_weight > 0:
        composite = sum(
            timeframe_scores[tf].result.score * TIMEFRAME_WEIGHTS.get(tf, 0.1)
            for tf in available
        ) / total_weight
    else:
        composite = 0.0

    any_degraded = any(ts.degraded for ts in timeframe_scores.values())
    dominant = _dominant_action(timeframe_scores)

    logger.info(
        "mtf: %s composite_score=%.3f dominant=%s timeframes=%s degraded=%s",
        symbol, composite, dominant, available, any_degraded,
    )

    return MultiTimeframeResult(
        symbol=symbol,
        composite_score=round(composite, 4),
        dominant_action=dominant,
        timeframe_scores=timeframe_scores,
        timeframes_available=available,
        any_degraded=any_degraded,
    )
