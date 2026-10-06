"""Signal kind and concept classification for the graded confluence ranker.

``kind_of`` says what sort of reading a signal is (event, state, proximity,
context); ``concept_of`` says which single fact it measures, so several
signals reporting the same fact collapse to one vote. Both resolve an explicit
field on the signal first, then the ordered pattern table below. An
unclassified label returns None; it is never guessed. The exhaustiveness test
fails CI when a detector emits a label this table does not cover.

Spec: docs/states-and-near-a-level-as-signals-2026-10-06.md §6.4 and §8.3 P1.
"""
from __future__ import annotations

import re
from typing import Final, NamedTuple

from signals_app.config import SignalKind
from signals_app.detection.base import MutableSignal

_X = SignalKind.EVENT_CROSS.value
_T = SignalKind.EVENT_THRESHOLD.value
_S = SignalKind.STATE.value
_P = SignalKind.PROXIMITY.value
_C = SignalKind.CONTEXT.value


class KindRule(NamedTuple):
    """One row of the classification table."""

    pattern: re.Pattern[str]
    kind: str
    concept: str


def _rule(regex: str, kind: str, concept: str) -> KindRule:
    return KindRule(re.compile(regex), kind, concept)


# Order matters: first match wins, so specific rows sit above general ones.
KIND_BY_PATTERN: Final[tuple[KindRule, ...]] = (
    # --- trend ---
    _rule(r"^(GOLDEN|DEATH) CROSS$", _X, "ma_cross"),
    _rule(r"^\d+/\d+ MA (BULL|BEAR) CROSS$", _X, "ma_cross"),
    _rule(r"^PRICE (ABOVE|BELOW) 20 MA$", _X, "ma_cross"),
    _rule(r"^MA ALIGNMENT (BULLISH|BEARISH)$", _S, "ma_stack"),
    _rule(r"^STRONG (UP|DOWN)TREND$", _S, "adx_trend"),
    _rule(r"^ICHIMOKU TK (BULL|BEAR) CROSS$", _X, "ichimoku_tk"),
    _rule(r"^PRICE (ABOVE|BELOW|INSIDE) KUMO$", _S, "ichimoku_cloud_pos"),
    _rule(r"^(BULLISH|BEARISH) KUMO$", _S, "ichimoku_cloud_color"),
    # --- mean reversion ---
    _rule(r"^AT (LOWER|UPPER) BB$", _P, "bb_edge"),
    _rule(r"^(ABOVE UPPER|BELOW LOWER) BB\(", _T, "bb_breach"),
    _rule(r"^BB\(.*\) RIDING UPPER BAND$", _S, "bb_ride"),
    _rule(r"^>\d+% (ABOVE|BELOW) \d+SMA$", _P, "ma_distance"),
    # --- momentum ---
    _rule(r"^LARGE (GAIN|LOSS)$", _T, "big_move"),
    _rule(r"^WITHIN \d+% OF \d+b (HIGH|LOW)$", _P, "range_edge"),
    _rule(r"^RSI EXTREME OVERSOLD$", _S, "rsi_zone"),
    _rule(r"^RSI (OVERSOLD|OVERBOUGHT)$", _S, "rsi_zone"),
    _rule(r"^RSI\d+ (OVERSOLD|OVERBOUGHT) \(", _S, "rsi_zone"),
    _rule(r"^RSI\d+ CROSSED 50 (BULL|BEAR)$", _X, "rsi_mid_cross"),
    _rule(r"^MACD ZERO CROSS (UP|DOWN)$", _X, "macd_zero"),
    _rule(r"^MACD\(.*\) ZERO (BULL|BEAR)$", _X, "macd_zero"),
    _rule(r"^MACD (BULL|BEAR) CROSS$", _X, "macd_cross"),
    _rule(r"^MACD\(.*\) (BULL|BEAR) CROSS$", _X, "macd_cross"),
    _rule(r"^STOCHASTIC (OVERSOLD|OVERBOUGHT)$", _S, "stoch_zone"),
    _rule(r"^STOCH (BULL|BEAR) CROSS ", _X, "stoch_cross"),
    # --- volume / flow ---
    _rule(r"^(EXTREME VOLUME 3X|VOLUME SPIKE)", _C, "vol_spike"),
    _rule(r"^VOLUME (BULLISH|BEARISH) DIVERGENCE", _S, "vol_divergence"),
    _rule(r"^OBV (BULLISH|BEARISH) DIVERGENCE$", _S, "obv_divergence"),
    _rule(r"^OBV (BULL|BEAR) CROSS EMA$", _X, "obv_cross"),
    _rule(r"^CMF STRONG (BUYING|SELLING)$", _S, "cmf_level"),
    _rule(r"^CMF CROSSED (POSITIVE|NEGATIVE)$", _X, "cmf_cross"),
    # --- structure ---
    _rule(r"^FIB (GOLDEN POCKET|CONFLUENCE) HOLD$", _X, "fib_hold"),
    _rule(r"^FIB 0\.786 BREAK$", _X, "fib_break"),
    _rule(r"^FIB 1\.618 TARGET$", _T, "fib_target"),
)


def _match(signal: MutableSignal) -> KindRule | None:
    for rule in KIND_BY_PATTERN:
        if rule.pattern.search(signal.signal):
            return rule
    return None


def kind_of(signal: MutableSignal) -> str | None:
    """SignalKind value for a signal, or None when unclassified."""
    if signal.kind is not None:
        return signal.kind
    rule = _match(signal)
    return rule.kind if rule else None


def concept_of(signal: MutableSignal) -> str | None:
    """Concept key for a signal, or None when unclassified."""
    if signal.concept is not None:
        return signal.concept
    rule = _match(signal)
    return rule.concept if rule else None


def stamp_kinds(signals: list[MutableSignal]) -> None:
    """Fill ``kind`` / ``concept`` on every signal that lacks them (in place)."""
    for signal in signals:
        if signal.kind is None:
            signal.kind = kind_of(signal)
        if signal.concept is None:
            signal.concept = concept_of(signal)
