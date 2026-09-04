"""Support/resistance proximity detector.

Closes the gap documented in docs/TODO.md P3 #9a: the single biggest signal-
coverage gap vs. `boll-4-april-500.py` (~38% of that reference run's signals).
Reuses `indicators/pivots.py` (`precompute_pivots`, `get_nearest_levels`) —
same O(1)-lookup pivot design `boll-4-april-500.py` independently converged
on — rather than reimplementing pivot detection here.
"""
from __future__ import annotations

import logging
import math

import pandas as pd

from signals_app.config import SignalCategory, SignalStrength
from signals_app.detection.base import MutableSignal
from signals_app.indicators.grids import (
    SR_MAX_LEVELS_PER_SIDE,
    SR_PIVOT_WINDOWS,
    SR_PROXIMITIES,
)
from signals_app.indicators.pivots import get_nearest_levels, precompute_pivots

logger = logging.getLogger(__name__)


def _sf(val: object) -> float | None:
    """Safe float conversion — returns None on NaN/Inf/None."""
    try:
        v = float(val)  # type: ignore[arg-type]
        return None if (math.isnan(v) or math.isinf(v)) else v
    except Exception:
        return None


class SupportResistanceDetector:
    """Fires NEAR SUPPORT / NEAR RESISTANCE signals from swing pivot levels.

    For each of `SR_PIVOT_WINDOWS`, recomputes pivots at that confirmation
    width, then for each of `SR_PROXIMITIES` checks how close the current
    close is to the nearest pivots on each side — the same
    windows x proximities x levels grid `boll-4-april-500.py` uses.
    """

    def detect(self, df: pd.DataFrame) -> list[MutableSignal]:
        """Detect support/resistance proximity signals.

        Args:
            df: Indicator DataFrame (High/Low/Close from the original OHLCV
                are still present — compute_indicators only adds columns).

        Returns:
            List of MutableSignal objects.
        """
        if len(df) < 1 or not all(c in df.columns for c in ("High", "Low", "Close")):
            return []

        close = _sf(df["Close"].iloc[-1])
        if close is None or close == 0:
            return []

        signals: list[MutableSignal] = []

        for window in SR_PIVOT_WINDOWS:
            levels = precompute_pivots(df, window=window)
            if not levels:
                continue

            for prox in SR_PROXIMITIES:
                supports, resistances = get_nearest_levels(close, levels, proximity=prox)
                prox_label = f"{prox * 100:g}%"

                nearest_supports = sorted(supports, key=lambda lvl: abs(lvl.price - close))
                for level in nearest_supports[:SR_MAX_LEVELS_PER_SIDE]:
                    dist_pct = abs(level.price - close) / close * 100
                    signals.append(MutableSignal(
                        signal=f"NEAR SUPPORT (w={window}, prox={prox_label})",
                        description=(
                            f"Close {close:.2f} within {dist_pct:.2f}% of support "
                            f"{level.price:.2f} (pivot window={window})"
                        ),
                        strength=SignalStrength.BULLISH.value,
                        category=SignalCategory.SUPPORT_RESISTANCE.value,
                    ))

                nearest_resistances = sorted(resistances, key=lambda lvl: abs(lvl.price - close))
                for level in nearest_resistances[:SR_MAX_LEVELS_PER_SIDE]:
                    dist_pct = abs(level.price - close) / close * 100
                    signals.append(MutableSignal(
                        signal=f"NEAR RESISTANCE (w={window}, prox={prox_label})",
                        description=(
                            f"Close {close:.2f} within {dist_pct:.2f}% of resistance "
                            f"{level.price:.2f} (pivot window={window})"
                        ),
                        strength=SignalStrength.BEARISH.value,
                        category=SignalCategory.SUPPORT_RESISTANCE.value,
                    ))

        return signals
