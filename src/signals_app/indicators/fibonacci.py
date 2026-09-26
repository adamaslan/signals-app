"""Point-in-time Fibonacci legs, levels, and confluence zones from confirmed pivots.

Anchors are confirmed swing pivots (see ``pivots.py``), never rolling window
extremes, and retracements are measured from the leg's far end so up-legs and
down-legs are direction-aware. Pure math only: no signals are built here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import pandas as pd

from signals_app.indicators.pivots import PivotLevel, precompute_pivots

RETRACEMENTS: Final[tuple[float, ...]] = (0.382, 0.5, 0.618, 0.65, 0.786)
EXTENSIONS: Final[tuple[float, ...]] = (1.272, 1.618)
GOLDEN_POCKET: Final[tuple[float, float]] = (0.618, 0.65)
BREAK_RATIO: Final[float] = 0.786
TARGET_RATIO: Final[float] = 1.618
MIN_LEG_ATR: Final[float] = 3.0
ZONE_ATR: Final[float] = 0.5
MAX_LEGS: Final[int] = 3
_PIVOT_POOL: Final[int] = 60


@dataclass(frozen=True)
class FibLeg:
    """A swing leg between two alternating confirmed pivots."""

    low: float
    high: float
    is_up: bool  # True: the low pivot precedes the high pivot
    end_index: int  # bar index of the leg's later pivot

    @property
    def range(self) -> float:
        return self.high - self.low

    def retracement(self, ratio: float) -> float:
        """Price at ``ratio`` retracement, measured back from the leg's end."""
        return self.high - ratio * self.range if self.is_up else self.low + ratio * self.range

    def extension(self, ratio: float) -> float:
        """Price at ``ratio`` extension of the leg, in the leg's direction."""
        return self.low + ratio * self.range if self.is_up else self.high - ratio * self.range


def _alternating_pivots(pivots: list[PivotLevel]) -> list[PivotLevel]:
    """Collapse runs of same-kind pivots, keeping the most extreme of each run."""
    merged: list[PivotLevel] = []
    for pivot in pivots:
        if merged and merged[-1].kind == pivot.kind:
            prev = merged[-1]
            more_extreme = (
                pivot.price > prev.price if pivot.kind == "resistance" else pivot.price < prev.price
            )
            if more_extreme:
                merged[-1] = pivot
            continue
        merged.append(pivot)
    return merged


def recent_legs(df: pd.DataFrame, atr: float, max_legs: int = MAX_LEGS) -> list[FibLeg]:
    """Most-recent-first legs between alternating confirmed pivots, filtered by size.

    Legs smaller than ``MIN_LEG_ATR * atr`` are skipped: tiny legs put many
    levels within a few cents of each other and price touches all of them.
    """
    if atr <= 0:
        return []
    pivots = _alternating_pivots(precompute_pivots(df, max_levels=_PIVOT_POOL))
    legs: list[FibLeg] = []
    for earlier, later in zip(reversed(pivots[:-1]), reversed(pivots[1:])):
        is_up = later.kind == "resistance"
        low, high = (earlier.price, later.price) if is_up else (later.price, earlier.price)
        if high - low < MIN_LEG_ATR * atr:
            continue
        legs.append(FibLeg(low=low, high=high, is_up=is_up, end_index=later.bar_index))
        if len(legs) == max_legs:
            break
    return legs


def confluence_zones(legs: list[FibLeg], atr: float) -> list[tuple[float, int]]:
    """``(zone_price, n_legs_agreeing)`` for retracements within ``ZONE_ATR * atr``.

    Each leg contributes at most once per zone, so the count is independent legs.
    """
    if atr <= 0:
        return []
    points = sorted(
        (leg.retracement(r), i) for i, leg in enumerate(legs) for r in RETRACEMENTS
    )
    zones: list[tuple[float, int]] = []
    cluster: list[tuple[float, int]] = []
    for point in points:
        if cluster and point[0] - cluster[-1][0] > ZONE_ATR * atr:
            zones.append(_summarise(cluster))
            cluster = []
        cluster.append(point)
    if cluster:
        zones.append(_summarise(cluster))
    return zones


def _summarise(cluster: list[tuple[float, int]]) -> tuple[float, int]:
    price = sum(p for p, _ in cluster) / len(cluster)
    return price, len({leg_index for _, leg_index in cluster})
