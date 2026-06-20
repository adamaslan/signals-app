"""O(N) pivot support/resistance precomputation.

Computes local swing highs and lows in a single pass over the price series,
producing a list of S/R levels that detectors can use for proximity checks.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Minimum number of bars on each side of a pivot point
PIVOT_WINDOW: int = 3

# Maximum number of pivot levels to retain (most recent)
MAX_PIVOT_LEVELS: int = 20


@dataclass
class PivotLevel:
    """A single support or resistance level.

    Attributes:
        price: Price level of the pivot.
        bar_index: Integer position in the DataFrame where the pivot occurred.
        kind: "support" or "resistance".
        strength: Number of bars confirming the pivot (always >= PIVOT_WINDOW).
    """

    price: float
    bar_index: int
    kind: str  # "support" | "resistance"
    strength: int


def precompute_pivots(
    df: pd.DataFrame,
    window: int = PIVOT_WINDOW,
    max_levels: int = MAX_PIVOT_LEVELS,
) -> list[PivotLevel]:
    """Compute swing pivot levels in O(N) time.

    Uses a sliding window comparison to identify local extrema.
    Each candidate bar i is a pivot high if High[i] is strictly greater than
    all bars in [i-window, i+window]; pivot low if Low[i] is strictly less.

    Args:
        df: OHLCV DataFrame sorted oldest-first.
        window: Number of bars on each side required to confirm a pivot.
        max_levels: Maximum number of levels to return (most recent).

    Returns:
        List of PivotLevel objects, sorted by bar index ascending.
    """
    if len(df) < window * 2 + 1:
        logger.debug("precompute_pivots: insufficient bars (%d), returning empty", len(df))
        return []

    highs = df["High"].values
    lows = df["Low"].values
    n = len(highs)

    levels: list[PivotLevel] = []

    for i in range(window, n - window):
        segment_high = highs[i - window : i + window + 1]
        segment_low = lows[i - window : i + window + 1]

        if highs[i] == np.max(segment_high) and highs[i] > np.min(segment_high):
            levels.append(
                PivotLevel(
                    price=float(highs[i]),
                    bar_index=i,
                    kind="resistance",
                    strength=window,
                )
            )

        if lows[i] == np.min(segment_low) and lows[i] < np.max(segment_low):
            levels.append(
                PivotLevel(
                    price=float(lows[i]),
                    bar_index=i,
                    kind="support",
                    strength=window,
                )
            )

    levels.sort(key=lambda lvl: lvl.bar_index)
    levels = levels[-max_levels:]

    logger.debug(
        "precompute_pivots: found %d levels (%d supports, %d resistances)",
        len(levels),
        sum(1 for l in levels if l.kind == "support"),
        sum(1 for l in levels if l.kind == "resistance"),
    )
    return levels


def get_nearest_levels(
    price: float,
    levels: list[PivotLevel],
    proximity: float = 0.02,
) -> tuple[list[PivotLevel], list[PivotLevel]]:
    """Return pivot levels within proximity % of the current price.

    Args:
        price: Current price to check.
        levels: List of PivotLevel objects from precompute_pivots.
        proximity: Fractional distance threshold (0.02 = 2%).

    Returns:
        Tuple of (nearby_supports, nearby_resistances).
    """
    supports: list[PivotLevel] = []
    resistances: list[PivotLevel] = []

    for level in levels:
        if price == 0.0:
            continue
        dist = abs(level.price - price) / price
        if dist <= proximity:
            if level.kind == "support":
                supports.append(level)
            else:
                resistances.append(level)

    return supports, resistances
