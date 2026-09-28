"""ConfluenceRanker — aggregates raw detector signals into a scored confluence result.

Derived from the architecture in signals-app-architecture.md and the scoring
logic in gcp3/backend/technical_signals.py.

Each raw MutableSignal contributes a weighted vote based on its strength and
category. The net score is normalized to [-1, 1]. The result drives the
BUY/HOLD/SELL action recommendation before LLM synthesis.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
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
from signals_app.scoring.families import FAMILIES, family_of, is_bearish_extension_vote
from signals_app.scoring.regime import TREND_UP

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Strength → numeric vote mapping
# ---------------------------------------------------------------------------

# Bullish direction: positive votes
_STRENGTH_BULL_WEIGHT: Final[dict[str, float]] = {
    SignalStrength.EXTREME_BULLISH.value: 3.0,
    SignalStrength.STRONG_BULLISH.value: 2.0,
    SignalStrength.BULLISH.value: 1.0,
    # Direction-less strengths carry no side: counting them as bullish made a
    # capitulation volume spike or a "STRONG DOWNTREND" vote bullish.
    SignalStrength.VERY_SIGNIFICANT.value: 0.0,
    SignalStrength.SIGNIFICANT.value: 0.0,
    SignalStrength.TRENDING.value: 0.0,
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

# Pseudo-count added to the score denominator so two agreeing votes no longer
# saturate at exactly +/-1.0 (denominator used to count only fired votes).
SCORE_PSEUDO_COUNT: Final[float] = 4.0

# |score| required before a calibrated hit rate may promote a label to HIGH.
_HIGH_MIN_ABS_SCORE: Final[float] = 0.55

# SA4 (FIBONACCI.md §13.1/§11.13 change 2): the "structure" family
# (support/resistance, range, fibonacci — see scoring/families.py) measures
# one underlying fact (price reacting to a level), so several structure
# detectors firing the same bar in the same direction must count as one
# vote, not one-per-detector. Without this, a lone fib golden-pocket hold
# (or two/three structure signals agreeing) could multiply its own weight
# and turn a HOLD into a BUY on what is really a single observation.
_STRUCTURE_FAMILY: Final[str] = "structure"
# Small reward for genuine multi-signal structure agreement, capped so it
# can never approach what an uncapped per-signal vote would have added.
_STRUCTURE_AGREEMENT_BONUS_PER_EXTRA: Final[float] = 0.15
_STRUCTURE_AGREEMENT_BONUS_CAP: Final[float] = 0.5


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
    families: dict[str, float] = field(default_factory=dict)
    agreeing_families: int = 0
    regime: str | None = None

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
            "families": self.families,
            "agreeing_families": self.agreeing_families,
            "regime": self.regime,
        }


def _collapse_structure_votes(
    signals: list[MutableSignal],
) -> tuple[list[MutableSignal], float, float]:
    """SA4: collapse same-direction "structure" family signals to one vote.

    Keeps the single strongest bullish structure signal and the single
    strongest bearish structure signal (each family member measures the
    same underlying reaction, so extra agreeing detectors shouldn't multiply
    the vote), and returns a small, capped agreement bonus per side when
    more than one structure signal agreed.

    Args:
        signals: Raw signals for the bar.

    Returns:
        (collapsed_signals, bull_agreement_bonus, bear_agreement_bonus) —
        collapsed_signals has at most one bullish and one bearish structure
        signal; every non-structure signal passes through unchanged.
    """
    other: list[MutableSignal] = []
    bull_structure: list[MutableSignal] = []
    bear_structure: list[MutableSignal] = []

    for signal in signals:
        if family_of(signal) != _STRUCTURE_FAMILY:
            other.append(signal)
            continue
        base_vote = _STRENGTH_BULL_WEIGHT.get(signal.strength, 0.0)
        if base_vote > 0:
            bull_structure.append(signal)
        elif base_vote < 0:
            bear_structure.append(signal)
        else:
            other.append(signal)  # direction-less structure signal, nothing to collapse

    collapsed = list(other)
    bull_bonus = 0.0
    bear_bonus = 0.0
    for group, is_bull in ((bull_structure, True), (bear_structure, False)):
        if not group:
            continue
        strongest = max(group, key=lambda s: abs(_STRENGTH_BULL_WEIGHT.get(s.strength, 0.0)))
        collapsed.append(strongest)
        if len(group) > 1:
            bonus = min(
                _STRUCTURE_AGREEMENT_BONUS_CAP,
                _STRUCTURE_AGREEMENT_BONUS_PER_EXTRA * (len(group) - 1),
            )
            if is_bull:
                bull_bonus = bonus
            else:
                bear_bonus = bonus

    return collapsed, bull_bonus, bear_bonus


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
                Skipped entirely when bias is "neutral" — there is no winning
                side to calibrate against. Defaults to None, which preserves
                prior behavior exactly.

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
        # (category, strength) pairs — SA3 needs both to build the composite
        # "CATEGORY|STRENGTH" calibration key, not strength alone.
        bull_strengths: list[tuple[str, str]] = []
        bear_strengths: list[tuple[str, str]] = []

        # SA4: collapse same-direction structure-family signals to one vote
        # before scoring, so a fib hold agreeing with support/resistance
        # doesn't count twice. total_signals below still reports the raw,
        # pre-collapse count — it's a diagnostic of how many detectors
        # actually fired, not a scoring input.
        total_raw_signals = len(signals)
        signals, structure_bull_bonus, structure_bear_bonus = _collapse_structure_votes(signals)

        for signal in signals:
            base_vote = _STRENGTH_BULL_WEIGHT.get(signal.strength, 0.0)
            category_bonus = _CATEGORY_BONUS.get(signal.category, 0.0)

            if base_vote > 0:
                vote = base_vote + category_bonus
                weighted_bull += vote
                max_weight += vote
                bull_count += 1
                bull_strengths.append((signal.category, signal.strength))
            elif base_vote < 0:
                vote = abs(base_vote) + category_bonus
                weighted_bear += vote
                max_weight += vote
                bear_count += 1
                bear_strengths.append((signal.category, signal.strength))
            else:
                neutral_count += 1
                max_weight += 0.1  # neutral signals have minimal weight

        # SA4: apply the capped structure-agreement bonus once per side,
        # after the main loop — it rewards genuine multi-signal agreement
        # without letting the number of agreeing detectors multiply the vote.
        if structure_bull_bonus:
            weighted_bull += structure_bull_bonus
            max_weight += structure_bull_bonus
        if structure_bear_bonus:
            weighted_bear += structure_bear_bonus
            max_weight += structure_bear_bonus

        if max_weight > 0:
            raw_score = (weighted_bull - weighted_bear) / (max_weight + SCORE_PSEUDO_COUNT)
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
        if abs_score >= _HIGH_MIN_ABS_SCORE:
            confidence_label = "HIGH"
        elif abs_score >= 0.25:
            confidence_label = "MEDIUM"
        else:
            confidence_label = "LOW"

        if strength_hit_rates and bias != "neutral":
            winning_strengths = bull_strengths if bias == "bullish" else bear_strengths
            # SA3: look up the composite "CATEGORY|STRENGTH" key first (a
            # detector-specific calibration, e.g. "FIBONACCI|STRONG
            # BULLISH"); fall back to the plain-strength key when the
            # composite bucket doesn't exist (below CALIBRATION_MIN_BUCKET_SIZE
            # events, or not yet calibrated) — never drop the signal from
            # calibration just because its composite bucket is thin.
            known_rates = [
                strength_hit_rates[f"{cat}|{s}"]
                if f"{cat}|{s}" in strength_hit_rates
                else strength_hit_rates[s]
                for cat, s in winning_strengths
                if f"{cat}|{s}" in strength_hit_rates or s in strength_hit_rates
            ]
            if known_rates:
                avg_hit_rate = sum(known_rates) / len(known_rates)
                # A hit rate alone never promotes to HIGH: it is mostly market
                # beta, so magnitude must also clear the HIGH bar.
                if avg_hit_rate >= _HIT_RATE_HIGH_THRESHOLD:
                    if abs_score >= _HIGH_MIN_ABS_SCORE:
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
            total_signals=total_raw_signals,
            bull_weight=round(weighted_bull, 3),
            bear_weight=round(weighted_bear, 3),
            max_weight=round(max_weight, 3),
        )


# ---------------------------------------------------------------------------
# Family-based variant (docs/scoring-2x-plan.md P2)
# ---------------------------------------------------------------------------

# Per-family pseudo-count: one lone vote of weight 1 gives a family net of
# 1 / (1 + K) rather than saturating at +/-1.
FAMILY_PSEUDO_COUNT: Final[float] = 1.5

# |family net| a family must reach to count as taking a side.
FAMILY_SIDE_MIN_NET: Final[float] = 0.15

# BUY / SELL need at least this many families on the same side and no more
# than FAMILY_MAX_OPPOSING on the other (plan §0.4 item 4).
FAMILY_MIN_AGREEING: Final[int] = 3
FAMILY_MAX_OPPOSING: Final[int] = 1

# Thresholds on the mean family net. Starting values, NOT yet tuned on the
# evaluation harness (plan P2 ship criterion is beating the post-P-1 baseline).
FAMILY_BUY_THRESHOLD: Final[float] = 0.20
FAMILY_SELL_THRESHOLD: Final[float] = -0.20


class FamilyConfluenceRanker:
    """Confluence over independent signal *families*, with a regime gate.

    Each family's votes collapse into one net in (-1, 1); the score is the mean
    net over all families (a silent family counts as 0). BUY / SELL require
    agreement across several families, so a single fan-out detector can no
    longer carry a call by itself. In an uptrend, bearish "overbought /
    extended" votes are zeroed (they were anti-predictive there, plan §0.4).

    Same output type as ``ConfluenceRanker`` so the two are interchangeable.
    """

    def rank_signals(
        self,
        signals: list[MutableSignal],
        regime: str | None = None,
    ) -> ConfluenceResult:
        """Score signals by family.

        Args:
            signals: Detector output for one bar.
            regime: Market regime label (see ``scoring.regime``). ``trend_up``
                neutralises bearish extension votes; None disables the gate.

        Returns:
            ConfluenceResult with ``families`` (net per family) and
            ``agreeing_families`` (largest same-side family count) populated.
        """
        bull = {f: 0.0 for f in FAMILIES}
        bear = {f: 0.0 for f in FAMILIES}
        bull_count = bear_count = neutral_count = 0

        for signal in signals:
            base_vote = _STRENGTH_BULL_WEIGHT.get(signal.strength, 0.0)
            family = family_of(signal)
            gated = regime == TREND_UP and is_bearish_extension_vote(signal)
            if base_vote == 0.0 or family is None or gated:
                neutral_count += 1
                continue
            vote = abs(base_vote) + _CATEGORY_BONUS.get(signal.category, 0.0)
            if base_vote > 0:
                bull[family] += vote
                bull_count += 1
            else:
                bear[family] += vote
                bear_count += 1

        nets = {
            f: (bull[f] - bear[f]) / (bull[f] + bear[f] + FAMILY_PSEUDO_COUNT) for f in FAMILIES
        }
        score = round(sum(nets.values()) / len(FAMILIES), 4)
        bull_families = sum(1 for n in nets.values() if n >= FAMILY_SIDE_MIN_NET)
        bear_families = sum(1 for n in nets.values() if n <= -FAMILY_SIDE_MIN_NET)

        if score >= FAMILY_BUY_THRESHOLD and bull_families >= FAMILY_MIN_AGREEING and bear_families <= FAMILY_MAX_OPPOSING:
            action = "BUY"
        elif score <= FAMILY_SELL_THRESHOLD and bear_families >= FAMILY_MIN_AGREEING and bull_families <= FAMILY_MAX_OPPOSING:
            action = "SELL"
        else:
            action = "HOLD"

        bias = "bullish" if score >= 0.05 else "bearish" if score <= -0.05 else "neutral"
        abs_score = abs(score)
        confidence_label = "HIGH" if abs_score >= 0.35 else "MEDIUM" if abs_score >= 0.2 else "LOW"

        return ConfluenceResult(
            score=score,
            bias=bias,
            confidence_label=confidence_label,
            action=action,
            bull_count=bull_count,
            bear_count=bear_count,
            neutral_count=neutral_count,
            total_signals=len(signals),
            bull_weight=round(sum(bull.values()), 3),
            bear_weight=round(sum(bear.values()), 3),
            max_weight=round(sum(bull.values()) + sum(bear.values()), 3),
            families={f: round(n, 4) for f, n in nets.items()},
            agreeing_families=max(bull_families, bear_families),
            regime=regime,
        )
