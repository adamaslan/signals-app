"""Signal families — the unit of independent evidence for confluence.

Detectors within a family measure the same underlying fact (three momentum
oscillators agreeing is one observation, not three), so confluence counts
agreeing *families*, not fired signals. See docs/scoring-2x-plan.md §0.4 item 4.
"""
from __future__ import annotations

from typing import Final

from signals_app.config import SignalCategory
from signals_app.detection.base import MutableSignal

FAMILIES: Final[tuple[str, ...]] = (
    "trend",
    "momentum",
    "mean_reversion",
    "volume_flow",
    "structure",
)

FAMILY_BY_CATEGORY: Final[dict[str, str]] = {
    SignalCategory.MA_CROSS.value: "trend",
    SignalCategory.MA_TREND.value: "trend",
    SignalCategory.TREND.value: "trend",
    SignalCategory.ADX.value: "trend",
    SignalCategory.ICHIMOKU.value: "trend",
    SignalCategory.RSI.value: "momentum",
    SignalCategory.MACD.value: "momentum",
    SignalCategory.STOCHASTIC.value: "momentum",
    SignalCategory.PRICE_ACTION.value: "momentum",
    SignalCategory.MA_DISTANCE.value: "mean_reversion",
    SignalCategory.BOLLINGER.value: "mean_reversion",
    SignalCategory.BB_BREAKOUT.value: "mean_reversion",
    SignalCategory.VOLUME.value: "volume_flow",
    SignalCategory.OBV_CMF.value: "volume_flow",
    SignalCategory.SUPPORT_RESISTANCE.value: "structure",
    SignalCategory.RANGE.value: "structure",
}

# Categories whose bearish votes are "extension" calls (price stretched above
# its averages/bands). In an uptrend these were anti-predictive (plan §0.4).
_EXTENSION_CATEGORIES: Final[frozenset[str]] = frozenset(
    {
        SignalCategory.MA_DISTANCE.value,
        SignalCategory.BOLLINGER.value,
        SignalCategory.BB_BREAKOUT.value,
    }
)
_OVERBOUGHT_CATEGORIES: Final[frozenset[str]] = frozenset(
    {SignalCategory.RSI.value, SignalCategory.STOCHASTIC.value}
)


def family_of(signal: MutableSignal) -> str | None:
    """Family a signal belongs to, or None for an unmapped category."""
    return FAMILY_BY_CATEGORY.get(signal.category)


def is_bearish_extension_vote(signal: MutableSignal) -> bool:
    """True for a bearish "overbought / extended" vote.

    These are the votes regime-gated to neutral in an uptrend: bearish
    MA-distance, Bollinger / band-breakout, and overbought RSI / Stochastic.
    """
    if "BEARISH" not in signal.strength:
        return False
    if signal.category in _EXTENSION_CATEGORIES:
        return True
    if signal.category in _OVERBOUGHT_CATEGORIES:
        return "OVERBOUGHT" in signal.signal.upper() or "OVERBOUGHT" in signal.description.upper()
    return False
