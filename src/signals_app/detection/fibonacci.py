"""Fibonacci signal detector.

Fires only on a confirmed reaction on the last bar (a hold, a break, or a
target hit), never on mere proximity to a level. Anchors are confirmed pivots,
so results are point-in-time: extra future bars cannot change the output at
a given bar.
"""
from __future__ import annotations

import logging
import math

import pandas as pd

from signals_app.config import SignalCategory, SignalStrength
from signals_app.detection.base import MutableSignal
from signals_app.indicators.fibonacci import (
    BREAK_RATIO,
    GOLDEN_POCKET,
    TARGET_RATIO,
    FibLeg,
    confluence_zones,
    recent_legs,
)

logger = logging.getLogger(__name__)

MIN_BARS = 30
TOLERANCE_ATR = 0.25
MIN_CONFLUENCE_LEGS = 2
MAX_SIGNALS_PER_BAR = 2
_REQUIRED = ("High", "Low", "Open", "Close", "ATR", "Volume", "Volume_MA_20")


def _sf(val: object) -> float | None:
    """Safe float conversion — returns None on NaN/Inf/None."""
    try:
        v = float(val)  # type: ignore[arg-type]
        return None if (math.isnan(v) or math.isinf(v)) else v
    except Exception:
        return None


def _signal(label: str, description: str, strength: SignalStrength) -> MutableSignal:
    return MutableSignal(
        signal=label,
        description=description,
        strength=strength.value,
        category=SignalCategory.FIBONACCI.value,
    )


class FibonacciDetector:
    """Golden-pocket holds (and, opt-in, confluence holds, breaks, targets) on confirmed legs.

    By default only the signal with a measured edge is emitted: a bullish
    0.618-0.65 hold on above-average volume. On 197 tickers x 5 years of daily
    bars it beat the 21-day baseline hit rate by about +5.6pp, the same in both
    independent ticker halves. Non-Fibonacci control zones showed +1 to +4pp, so
    the edge is not proven to be specific to the Fibonacci ratios (see
    docs/fibonacci-signal-evaluation-2026-09-26.md). Every other signal here
    was at or below baseline and is emitted only with ``experimental=True``.
    """

    def __init__(self, experimental: bool = False) -> None:
        self._experimental = experimental

    def detect(self, df: pd.DataFrame) -> list[MutableSignal]:
        """Detect Fibonacci reaction events on the last bar.

        Args:
            df: Indicator DataFrame (output of compute_indicators), oldest first.

        Returns:
            At most ``MAX_SIGNALS_PER_BAR`` signals; empty on insufficient data.
        """
        if len(df) < MIN_BARS or any(col not in df.columns for col in _REQUIRED):
            return []
        bar, prev = df.iloc[-1], df.iloc[-2]
        atr = _sf(bar["ATR"])
        values = [_sf(bar[c]) for c in ("High", "Low", "Open", "Close")]
        prev_close, prev_high, prev_low = _sf(prev["Close"]), _sf(prev["High"]), _sf(prev["Low"])
        if not atr or None in values or None in (prev_close, prev_high, prev_low):
            return []
        high, low, open_, close = values  # type: ignore[misc]
        legs = recent_legs(df, atr)
        if not legs:
            return []

        leg = legs[0]
        tol = TOLERANCE_ATR * atr
        above_avg_volume = self._above_average_volume(bar)
        if not self._experimental:
            hold = self._golden_pocket_hold(leg, tol, low, high, open_, close, True)
            return [hold] if hold and leg.is_up and above_avg_volume else []

        signals: list[MutableSignal] = []
        hold = self._confluence_hold(legs, leg, atr, tol, low, high, open_, close, above_avg_volume)
        hold = hold or self._golden_pocket_hold(leg, tol, low, high, open_, close, above_avg_volume)
        if hold:
            signals.append(hold)
        break_signal = self._break(leg, close, prev_close)
        if break_signal:
            signals.append(break_signal)
        target = self._target(leg, high, low, prev_high, prev_low)
        if target:
            signals.append(target)
        return signals[:MAX_SIGNALS_PER_BAR]

    @staticmethod
    def _above_average_volume(bar: pd.Series) -> bool:
        volume, average = _sf(bar["Volume"]), _sf(bar["Volume_MA_20"])
        return volume is not None and average is not None and volume > average

    @staticmethod
    def _reacted(leg: FibLeg, zone_lo: float, zone_hi: float, tol: float,
                 low: float, high: float, open_: float, close: float) -> bool:
        """Touched the zone this bar and closed back out of it in the leg's direction."""
        if leg.is_up:
            return low <= zone_hi + tol and close > zone_hi and close > open_
        return high >= zone_lo - tol and close < zone_lo and close < open_

    @staticmethod
    def _graded(leg: FibLeg, rank: int) -> SignalStrength:
        """rank 0..2 -> BULLISH..EXTREME (mirrored for down-legs)."""
        bull = (SignalStrength.BULLISH, SignalStrength.STRONG_BULLISH, SignalStrength.EXTREME_BULLISH)
        bear = (SignalStrength.BEARISH, SignalStrength.STRONG_BEARISH, SignalStrength.EXTREME_BEARISH)
        return (bull if leg.is_up else bear)[rank]

    def _golden_pocket_hold(self, leg: FibLeg, tol: float, low: float, high: float,
                            open_: float, close: float, heavy: bool) -> MutableSignal | None:
        prices = [leg.retracement(r) for r in GOLDEN_POCKET]
        zone_lo, zone_hi = min(prices), max(prices)
        if not self._reacted(leg, zone_lo, zone_hi, tol, low, high, open_, close):
            return None
        side = "support" if leg.is_up else "resistance"
        return _signal(
            "FIB GOLDEN POCKET HOLD",
            f"Reacted at 0.618-0.65 {side} {zone_lo:.2f}-{zone_hi:.2f}",
            self._graded(leg, 1 if heavy else 0),
        )

    def _confluence_hold(self, legs: list[FibLeg], leg: FibLeg, atr: float, tol: float,
                         low: float, high: float, open_: float, close: float,
                         heavy: bool) -> MutableSignal | None:
        for price, n_legs in confluence_zones(legs, atr):
            if n_legs < MIN_CONFLUENCE_LEGS:
                continue
            if self._reacted(leg, price, price, tol, low, high, open_, close):
                return _signal(
                    "FIB CONFLUENCE HOLD",
                    f"Reacted at {n_legs}-leg Fibonacci confluence near {price:.2f}",
                    self._graded(leg, 2 if heavy else 1),
                )
        return None

    @staticmethod
    def _break(leg: FibLeg, close: float, prev_close: float) -> MutableSignal | None:
        level = leg.retracement(BREAK_RATIO)
        if leg.is_up and close < level <= prev_close:
            return _signal("FIB 0.786 BREAK", f"Closed below 0.786 retracement {level:.2f}",
                           SignalStrength.BEARISH)
        if not leg.is_up and close > level >= prev_close:
            return _signal("FIB 0.786 BREAK", f"Closed above 0.786 retracement {level:.2f}",
                           SignalStrength.BULLISH)
        return None

    @staticmethod
    def _target(leg: FibLeg, high: float, low: float,
                prev_high: float, prev_low: float) -> MutableSignal | None:
        level = leg.extension(TARGET_RATIO)
        reached = high >= level > prev_high if leg.is_up else low <= level < prev_low
        if not reached:
            return None
        # SIGNIFICANT, not BEARISH: fib categories are not in the up-trend
        # extension gate (scoring/families.py), so a bearish tag would vote
        # against uptrends ungated.
        return _signal("FIB 1.618 TARGET", f"Reached 1.618 extension {level:.2f}",
                       SignalStrength.SIGNIFICANT)
