"""Market regime classification from SPY alone (docs/scoring-2x-plan.md §6).

Point-in-time: every quantity is a trailing rolling/expanding statistic, so a
label on date t uses nothing after t.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TREND_UP = "trend_up"
TREND_DOWN = "trend_down"
RANGE = "range"
HIGH_VOL = "high_vol"
REGIMES = (TREND_UP, TREND_DOWN, RANGE, HIGH_VOL)

TREND_MA_PERIOD = 200
SLOPE_LOOKBACK = 20
VOL_LOOKBACK = 20
VOL_PERCENTILE_MIN_HISTORY = 252
HIGH_VOL_PERCENTILE = 0.80
MIN_BARS_FOR_REGIME = TREND_MA_PERIOD + SLOPE_LOOKBACK


def regime_series(benchmark: pd.DataFrame) -> pd.Series:
    """Label every bar of a benchmark frame with a regime.

    Rules, in priority order: ``high_vol`` when 20d realized vol sits in its
    expanding top quintile; else ``trend_up`` when close is above a rising
    200d SMA; ``trend_down`` when below a falling one; otherwise ``range``.
    Bars without enough history are NaN.

    Args:
        benchmark: OHLCV frame with a ``Close`` column, oldest-first.

    Returns:
        Series of regime labels aligned to ``benchmark.index``.
    """
    close = benchmark["Close"].astype(float)
    sma = close.rolling(TREND_MA_PERIOD, min_periods=TREND_MA_PERIOD).mean()
    slope = sma - sma.shift(SLOPE_LOOKBACK)
    vol = close.pct_change().rolling(VOL_LOOKBACK, min_periods=VOL_LOOKBACK).std()
    vol_pct = vol.expanding(min_periods=VOL_PERCENTILE_MIN_HISTORY).apply(
        lambda w: (w[:-1] <= w[-1]).mean(), raw=True
    )

    labels = pd.Series(np.nan, index=close.index, dtype=object)
    known = sma.notna() & slope.notna()
    labels[known & (close > sma) & (slope > 0)] = TREND_UP
    labels[known & (close < sma) & (slope < 0)] = TREND_DOWN
    labels[known & labels.isna()] = RANGE
    labels[known & (vol_pct >= HIGH_VOL_PERCENTILE)] = HIGH_VOL
    return labels


def current_regime(benchmark: pd.DataFrame) -> str | None:
    """Regime on the last bar, or None when history is too short."""
    if len(benchmark) < MIN_BARS_FOR_REGIME:
        return None
    label = regime_series(benchmark).iloc[-1]
    return label if isinstance(label, str) else None
