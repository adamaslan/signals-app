"""Canonical Pydantic schemas for signal outputs.

Ported exactly from gcp3/backend/schemas/signal_output.py.
This is the crown jewel — the contract between every pipeline layer.

Evidence weights must sum to 1.0; counter-evidence is required when confidence > 0.6.
"""
from __future__ import annotations

import math
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class SignalDirection(str, Enum):
    """Directional signal classifications."""

    strong_buy = "strong_buy"
    buy = "buy"
    hold = "hold"
    sell = "sell"
    strong_sell = "strong_sell"


class Timeframe(str, Enum):
    """Supported analysis timeframes."""

    one_day = "1D"
    five_day = "5D"
    one_month = "1M"
    three_month = "3M"
    six_month = "6M"
    one_year = "1Y"


class EvidenceSource(str, Enum):
    """Source categories for evidence items."""

    technical = "technical"
    fundamental = "fundamental"
    macro = "macro"
    news_sentiment = "news_sentiment"
    options_flow = "options_flow"
    sector_relative = "sector_relative"
    cross_asset = "cross_asset"
    earnings = "earnings"
    rule_based = "rule_based"


class EvidenceItem(BaseModel):
    """A single piece of evidence supporting or countering a signal.

    Attributes:
        source: Category of evidence.
        weight: Fractional contribution to total evidence (0.0–1.0).
            Supporting weights must sum to 1.0 across all non-counter items.
        summary: One-sentence explanation of the evidence.
        is_counter: True if this is counter-evidence (bearish within a bullish signal).
    """

    source: EvidenceSource
    weight: float = Field(ge=0.0, le=1.0)
    summary: str
    is_counter: bool = False

    @field_validator("weight")
    @classmethod
    def weight_finite(cls, v: float) -> float:
        """Ensure weight is a finite number."""
        if not math.isfinite(v):
            raise ValueError("weight must be finite")
        return round(v, 4)


class Evidence(BaseModel):
    """Collection of evidence items for a signal.

    Invariant: supporting (non-counter) evidence weights must sum to 1.0 ± 0.01.
    """

    items: list[EvidenceItem]

    @model_validator(mode="after")
    def weights_sum_to_one(self) -> "Evidence":
        """Validate that supporting evidence weights sum to 1.0."""
        supporting = [e for e in self.items if not e.is_counter]
        total = sum(e.weight for e in supporting)
        if supporting and abs(total - 1.0) > 0.01:
            raise ValueError(
                f"Supporting evidence weights must sum to 1.0 ± 0.01, got {total:.4f}"
            )
        return self


class Signal(BaseModel):
    """Synthesized directional signal for a single timeframe.

    Attributes:
        direction: Directional call (strong_buy → strong_sell).
        confidence: Float strictly between 0 and 1 — never 0 or 1 exactly.
        timeframe: Analysis timeframe this signal covers.
        evidence: Supporting and counter evidence with weights summing to 1.0.
        ai_degraded: True when the LLM call failed and a fallback was used.
        prompt_version: Version tag for auditability across model iterations.
    """

    direction: SignalDirection
    confidence: float = Field(gt=0.0, lt=1.0, description="Must not be 0 or 1 exactly")
    timeframe: Timeframe
    evidence: Evidence
    ai_degraded: bool = False
    prompt_version: str = "unknown"

    @field_validator("confidence")
    @classmethod
    def confidence_not_extreme(cls, v: float) -> float:
        """Reject exactly 0.0 or 1.0 confidence values."""
        if v == 0.0 or v == 1.0:
            raise ValueError("confidence must be strictly between 0 and 1")
        return round(v, 4)

    @model_validator(mode="after")
    def hold_confidence_cap(self) -> "Signal":
        """HOLD signals must not exceed 0.75 confidence."""
        if self.direction == SignalDirection.hold and self.confidence > 0.75:
            raise ValueError("hold signals must have confidence ≤ 0.75")
        return self

    @model_validator(mode="after")
    def high_confidence_requires_counter(self) -> "Signal":
        """Confidence > 0.6 requires at least one counter-argument evidence item."""
        if self.confidence > 0.6:
            has_counter = any(e.is_counter for e in self.evidence.items)
            if not has_counter:
                raise ValueError(
                    "confidence > 0.6 requires at least one counter_argument evidence item"
                )
        return self


class DivergencePattern(str, Enum):
    """Cross-timeframe divergence classification."""

    aligned_bullish = "aligned_bullish"
    aligned_bearish = "aligned_bearish"
    short_bull_long_bear = "short_bull_long_bear"
    short_bear_long_bull = "short_bear_long_bull"
    mixed = "mixed"
    insufficient_data = "insufficient_data"


class TimeframeMatrix(BaseModel):
    """Multi-timeframe signal matrix for a single ticker.

    Attributes:
        ticker: Stock symbol.
        signals: Map of timeframe value string to Signal.
        alignment_score: Fraction of signals agreeing with majority direction (0–1).
        divergence_pattern: Classified cross-timeframe divergence pattern.
        divergence_interpretation: Human-readable interpretation of the pattern.
        computed_at: ISO timestamp of when this matrix was computed.
    """

    ticker: str
    signals: dict[str, Signal]
    alignment_score: float = Field(ge=0.0, le=1.0)
    divergence_pattern: DivergencePattern
    divergence_interpretation: str = ""
    computed_at: str = ""

    @model_validator(mode="after")
    def alignment_finite(self) -> "TimeframeMatrix":
        """Validate alignment_score is finite."""
        if not math.isfinite(self.alignment_score):
            raise ValueError("alignment_score must be finite")
        return self


class SignalOutput(BaseModel):
    """Top-level API response schema.

    Attributes:
        ticker: Stock symbol analyzed.
        signal: Primary synthesized signal.
        matrix: Optional multi-timeframe matrix.
        feature_unavailable: List of pipeline features that were unavailable.
        schema_version: Schema version for forward compatibility (wire format).
        code_version: Which detection/scoring code version produced this
            signal (provenance) — independent of schema_version.
        data_quality_score: 0.0-1.0 guard on the input OHLCV, None if not computed.
        data_quality_reasons: Which checks lowered data_quality_score, if any.
    """

    ticker: str
    signal: Signal
    matrix: TimeframeMatrix | None = None
    feature_unavailable: list[str] = Field(default_factory=list)
    schema_version: str = "1.0"
    code_version: str | None = None
    data_quality_score: float | None = Field(default=None, ge=0.0, le=1.0)
    data_quality_reasons: list[str] = Field(default_factory=list)


def alignment_score(signals: list[Signal]) -> float:
    """Compute alignment score: fraction of signals agreeing with majority direction.

    Args:
        signals: List of Signal objects to evaluate.

    Returns:
        Float in [0, 1] representing agreement fraction.
    """
    if not signals:
        return 0.0
    directions = [s.direction for s in signals]
    majority = max(set(directions), key=directions.count)
    agreeing = sum(1 for d in directions if d == majority)
    return round(agreeing / len(directions), 4)


def classify_divergence(signals: dict[str, Signal]) -> DivergencePattern:
    """Classify the divergence pattern from timeframe signals.

    Short timeframes: 1D, 5D.
    Long timeframes: 1M, 3M, 6M, 1Y.

    Args:
        signals: Map of timeframe string to Signal.

    Returns:
        DivergencePattern enum value.
    """
    short_tfs = {Timeframe.one_day.value, Timeframe.five_day.value}
    long_tfs = {
        Timeframe.one_month.value, Timeframe.three_month.value,
        Timeframe.six_month.value, Timeframe.one_year.value,
    }

    bull = {SignalDirection.buy, SignalDirection.strong_buy}
    bear = {SignalDirection.sell, SignalDirection.strong_sell}

    short_signals = [s for tf, s in signals.items() if tf in short_tfs]
    long_signals = [s for tf, s in signals.items() if tf in long_tfs]

    if not short_signals or not long_signals:
        return DivergencePattern.insufficient_data

    short_bull = sum(1 for s in short_signals if s.direction in bull)
    short_bear = sum(1 for s in short_signals if s.direction in bear)
    long_bull = sum(1 for s in long_signals if s.direction in bull)
    long_bear = sum(1 for s in long_signals if s.direction in bear)

    short_bias = "bull" if short_bull > short_bear else ("bear" if short_bear > short_bull else "neutral")
    long_bias = "bull" if long_bull > long_bear else ("bear" if long_bear > long_bull else "neutral")

    if short_bias == "bull" and long_bias == "bull":
        return DivergencePattern.aligned_bullish
    if short_bias == "bear" and long_bias == "bear":
        return DivergencePattern.aligned_bearish
    if short_bias == "bull" and long_bias == "bear":
        return DivergencePattern.short_bull_long_bear
    if short_bias == "bear" and long_bias == "bull":
        return DivergencePattern.short_bear_long_bull
    return DivergencePattern.mixed
