"""ConfluenceRanker — aggregates raw detector signals into a scored confluence result.

Derived from the architecture in signals-app-architecture.md and the scoring
logic in gcp3/backend/technical_signals.py.

Each raw MutableSignal contributes a weighted vote based on its strength and
category. The net score is normalized to [-1, 1]. The result drives the
BUY/HOLD/SELL action recommendation before LLM synthesis.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

from signals_app.config import (
    CONFLUENCE_BUY_MIN_SIGNALS,
    CONFLUENCE_BUY_THRESHOLD,
    CONFLUENCE_SELL_MIN_SIGNALS,
    CONFLUENCE_SELL_THRESHOLD,
    SignalCategory,
    SignalStrength,
)
from signals_app.detection.base import MutableSignal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Strength → numeric vote mapping
# ---------------------------------------------------------------------------

# Bullish direction: positive votes
_STRENGTH_BULL_WEIGHT: Final[dict[str, float]] = {
    SignalStrength.EXTREME_BULLISH.value: 3.0,
    SignalStrength.STRONG_BULLISH.value: 2.0,
    SignalStrength.BULLISH.value: 1.0,
    SignalStrength.VERY_SIGNIFICANT.value: 1.5,
    SignalStrength.SIGNIFICANT.value: 1.0,
    SignalStrength.TRENDING.value: 1.0,
    SignalStrength.NEUTRAL.value: 0.0,
    SignalStrength.BEARISH.value: -1.0,
    SignalStrength.STRONG_BEARISH.value: -2.0,
    SignalStrength.EXTREME_BEARISH.value: -3.0,
}

# Category bonus weights (added to abs(vote) for high-confidence categories)
_CATEGORY_BONUS: Final[dict[str, float]] = {
    SignalCategory.MA_CROSS.value: 0.5,
    SignalCategory.MACD.value: 0.5,
    SignalCategory.VOLUME.value: 0.5,
    SignalCategory.OBV_CMF.value: 0.3,
    SignalCategory.ICHIMOKU.value: 0.3,
}

# Hit-rate thresholds used to nudge the score-threshold confidence_label
# toward the measured backtest hit-rate when strength_hit_rates is supplied.
_HIT_RATE_HIGH_THRESHOLD: Final[float] = 0.60
_HIT_RATE_LOW_THRESHOLD: Final[float] = 0.50


@dataclass
class ConfluenceResult:
    """Result of ConfluenceRanker.rank_signals().

    Attributes:
        score: Net confluence score in [-1, 1]. Positive = bullish bias.
        bias: "bullish", "bearish", or "neutral".
        confidence_label: "HIGH", "MEDIUM", or "LOW".
        action: Recommended action — "BUY", "SELL", or "HOLD".
        bull_count: Number of bullish signals.
        bear_count: Number of bearish signals.
        neutral_count: Number of neutral signals.
        total_signals: Total signal count.
        bull_weight: Weighted sum of bullish votes.
        bear_weight: Weighted sum of bearish votes (magnitude).
        max_weight: Maximum possible weighted sum.
    """

    score: float
    bias: str
    confidence_label: str
    action: str
    bull_count: int
    bear_count: int
    neutral_count: int
    total_signals: int
    bull_weight: float
    bear_weight: float
    max_weight: float

    def to_dict(self) -> dict:
        """Serialize to dictionary.

        Returns:
            Plain dictionary representation.
        """
        return {
            "score": self.score,
            "bias": self.bias,
            "confidence_label": self.confidence_label,
            "action": self.action,
            "bull_count": self.bull_count,
            "bear_count": self.bear_count,
            "neutral_count": self.neutral_count,
            "total_signals": self.total_signals,
            "bull_weight": self.bull_weight,
            "bear_weight": self.bear_weight,
            "max_weight": self.max_weight,
        }


class ConfluenceRanker:
    """Ranks raw detection signals into a weighted net confluence score.

    Each signal's strength maps to a numeric vote. High-conviction categories
    receive a bonus. The net score is normalized to [-1, 1].

    The BUY/HOLD/SELL action is derived from:
    - Score >= CONFLUENCE_BUY_THRESHOLD AND bull_count >= CONFLUENCE_BUY_MIN_SIGNALS → BUY
    - Score <= CONFLUENCE_SELL_THRESHOLD AND bear_count >= CONFLUENCE_SELL_MIN_SIGNALS → SELL
    - Otherwise → HOLD

    Example:
        ranker = ConfluenceRanker()
        result = ranker.rank_signals(signal_list)
        print(result.action, result.score)
    """

    def rank_signals(
        self,
        signals: list[MutableSignal],
        strength_hit_rates: dict[str, float] | None = None,
    ) -> ConfluenceResult:
        """Compute weighted confluence score from raw detection signals.

        Args:
            signals: List of MutableSignal objects from detect_all_signals().
            strength_hit_rates: Optional map of SignalStrength value -> measured
                historical hit-rate (0.0-1.0), e.g. from
                backtests.engine.score_historical_signals()'s "by_strength"
                buckets. When provided, the confidence_label is calibrated
                against the average hit-rate of the strengths that contributed
                to the winning side (bull or bear) of this signal set: average
                hit-rate >= 0.60 pushes toward HIGH, < 0.50 pushes toward LOW,
                otherwise the existing score-threshold logic is unchanged.
                Defaults to None, which preserves prior behavior exactly.

        Returns:
            ConfluenceResult with score, bias, action, and signal counts.
        """
        if not signals:
            return ConfluenceResult(
                score=0.0,
                bias="neutral",
                confidence_label="LOW",
                action="HOLD",
                bull_count=0,
                bear_count=0,
                neutral_count=0,
                total_signals=0,
                bull_weight=0.0,
                bear_weight=0.0,
                max_weight=0.0,
            )

        weighted_bull = 0.0
        weighted_bear = 0.0
        max_weight = 0.0
        bull_count = 0
        bear_count = 0
        neutral_count = 0
        bull_strengths: list[str] = []
        bear_strengths: list[str] = []

        for signal in signals:
            base_vote = _STRENGTH_BULL_WEIGHT.get(signal.strength, 0.0)
            category_bonus = _CATEGORY_BONUS.get(signal.category, 0.0)

            if base_vote > 0:
                vote = base_vote + category_bonus
                weighted_bull += vote
                max_weight += vote
                bull_count += 1
                bull_strengths.append(signal.strength)
            elif base_vote < 0:
                vote = abs(base_vote) + category_bonus
                weighted_bear += vote
                max_weight += vote
                bear_count += 1
                bear_strengths.append(signal.strength)
            else:
                neutral_count += 1
                max_weight += 0.1  # neutral signals have minimal weight

        if max_weight > 0:
            raw_score = (weighted_bull - weighted_bear) / max_weight
        else:
            raw_score = 0.0

        score = round(raw_score, 4)

        # Bias classification
        if score >= 0.1:
            bias = "bullish"
        elif score <= -0.1:
            bias = "bearish"
        else:
            bias = "neutral"

        # Confidence label — raw-score-threshold guess by default, optionally
        # calibrated against measured backtest hit-rates (see docstring).
        abs_score = abs(score)
        if abs_score >= 0.55:
            confidence_label = "HIGH"
        elif abs_score >= 0.25:
            confidence_label = "MEDIUM"
        else:
            confidence_label = "LOW"

        if strength_hit_rates:
            winning_strengths = bull_strengths if bias == "bullish" else bear_strengths
            known_rates = [
                strength_hit_rates[s] for s in winning_strengths if s in strength_hit_rates
            ]
            if known_rates:
                avg_hit_rate = sum(known_rates) / len(known_rates)
                if avg_hit_rate >= _HIT_RATE_HIGH_THRESHOLD:
                    confidence_label = "HIGH"
                elif avg_hit_rate < _HIT_RATE_LOW_THRESHOLD:
                    confidence_label = "LOW"

        # Action recommendation
        if score >= CONFLUENCE_BUY_THRESHOLD and bull_count >= CONFLUENCE_BUY_MIN_SIGNALS:
            action = "BUY"
        elif score <= CONFLUENCE_SELL_THRESHOLD and bear_count >= CONFLUENCE_SELL_MIN_SIGNALS:
            action = "SELL"
        else:
            action = "HOLD"

        logger.debug(
            "ConfluenceRanker: score=%.3f bias=%s action=%s bull=%d bear=%d neutral=%d",
            score, bias, action, bull_count, bear_count, neutral_count,
        )

        return ConfluenceResult(
            score=score,
            bias=bias,
            confidence_label=confidence_label,
            action=action,
            bull_count=bull_count,
            bear_count=bear_count,
            neutral_count=neutral_count,
            total_signals=len(signals),
            bull_weight=round(weighted_bull, 3),
            bear_weight=round(weighted_bear, 3),
            max_weight=round(max_weight, 3),
        )
