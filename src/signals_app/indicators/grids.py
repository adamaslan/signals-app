"""Parameter grids for indicator computation.

All parameter combinations used in the expanded signal detection.
Kept as module-level frozen tuples/lists — imported by compute.py and detectors.
"""
from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# Moving average cross pairs (fast, slow)
# ---------------------------------------------------------------------------

MA_CROSS_PAIRS: Final[tuple[tuple[int, int], ...]] = (
    (5, 10), (5, 20), (5, 50),
    (10, 20), (10, 50), (10, 100),
    (20, 50), (20, 100), (20, 200),
    (50, 100), (50, 200),
)

# All SMA periods needed (union of all fast/slow values + standard periods)
MA_PERIODS_EXTENDED: Final[tuple[int, ...]] = (
    5, 10, 20, 50, 100, 200
)

# ---------------------------------------------------------------------------
# RSI parameter grids
# ---------------------------------------------------------------------------

RSI_PERIODS: Final[tuple[int, ...]] = (5, 10, 14, 20, 30)

RSI_OS_OB_LEVELS: Final[tuple[tuple[float, float], ...]] = (
    (20.0, 80.0),
    (25.0, 75.0),
    (30.0, 70.0),
)

# ---------------------------------------------------------------------------
# MACD parameter sets (fast, slow, signal)
# ---------------------------------------------------------------------------

MACD_PARAMS: Final[tuple[tuple[int, int, int], ...]] = (
    (10, 20, 5),
    (12, 26, 9),   # standard
    (19, 39, 9),
    (20, 50, 10),
)

# ---------------------------------------------------------------------------
# Bollinger Band grids
# ---------------------------------------------------------------------------

BB_PERIODS: Final[tuple[int, ...]] = (10, 20, 30, 50)

BB_STD_DEVS: Final[tuple[float, ...]] = (1.5, 2.0, 2.5, 3.0)

# ---------------------------------------------------------------------------
# High/Low lookback periods and proximity thresholds
# ---------------------------------------------------------------------------

HL_LOOKBACKS: Final[tuple[int, ...]] = (20, 50, 100, 200, 252)

HL_PROXIMITIES: Final[tuple[float, ...]] = (0.01, 0.02, 0.05)

# ---------------------------------------------------------------------------
# MA distance thresholds
# ---------------------------------------------------------------------------

MA_DIST_PERIODS: Final[tuple[int, ...]] = (5, 10, 20, 50, 100, 200)

MA_DIST_THRESHOLDS: Final[tuple[float, ...]] = (5.0, 10.0, 15.0, 20.0)

# ---------------------------------------------------------------------------
# Volume MA periods
# ---------------------------------------------------------------------------

VOLUME_MA_PERIODS: Final[tuple[int, ...]] = (5, 10, 20, 50)

# ---------------------------------------------------------------------------
# S/R proximity thresholds
# ---------------------------------------------------------------------------

SR_PROXIMITIES: Final[tuple[float, ...]] = (0.005, 0.01, 0.02)
