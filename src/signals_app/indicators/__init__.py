"""Indicators package — L2 of the pipeline.

Vectorized pandas computation of all technical indicators in one pass.
"""

from .compute import compute_indicators
from .grids import (
    BB_PERIODS,
    BB_STD_DEVS,
    HL_LOOKBACKS,
    HL_PROXIMITIES,
    MA_CROSS_PAIRS,
    MA_DIST_PERIODS,
    MA_DIST_THRESHOLDS,
    MA_PERIODS_EXTENDED,
    MACD_PARAMS,
    RSI_OS_OB_LEVELS,
    RSI_PERIODS,
    VOLUME_MA_PERIODS,
)
from .pivots import precompute_pivots

__all__ = [
    "compute_indicators",
    "precompute_pivots",
    "BB_PERIODS",
    "BB_STD_DEVS",
    "HL_LOOKBACKS",
    "HL_PROXIMITIES",
    "MA_CROSS_PAIRS",
    "MA_DIST_PERIODS",
    "MA_DIST_THRESHOLDS",
    "MA_PERIODS_EXTENDED",
    "MACD_PARAMS",
    "RSI_OS_OB_LEVELS",
    "RSI_PERIODS",
    "VOLUME_MA_PERIODS",
]
