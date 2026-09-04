"""RSI and MACD divergence detection.

Ported from gcp3/backend/features_rsi.py and features_macd.py.
Provides:
  - Wilder's RSI computation with swing-based regular/hidden divergence
  - MACD state with histogram direction, acceleration, and cross classification
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# RSI divergence
# ---------------------------------------------------------------------------


@dataclass
class RsiFeature:
    """RSI computation result with divergence metadata.

    Attributes:
        timeframe: Timeframe label (e.g. "1D").
        period: RSI period used.
        current_rsi: Most recent RSI value.
        rsi_7d_trend: Trend over the past 7 bars.
        overbought: True if RSI > 70.
        oversold: True if RSI < 30.
        divergence: Type of divergence detected.
        divergence_strength: Divergence strength in [0, 1].
        bars_since_last_divergence: Bars since most recent divergence.
        midline_cross_days: Bars since last 50-midline cross.
    """

    timeframe: str
    period: int
    current_rsi: float
    rsi_7d_trend: Literal["rising", "falling", "flat"]
    overbought: bool
    oversold: bool
    divergence: Literal[
        "bullish_regular",
        "bearish_regular",
        "bullish_hidden",
        "bearish_hidden",
        "none",
    ]
    divergence_strength: float
    bars_since_last_divergence: int
    midline_cross_days: int


def _wilders_rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    """Compute RSI using Wilder's exponential smoothing.

    Args:
        closes: Time-ordered closing prices (oldest first).
        period: RSI period.

    Returns:
        RSI series aligned with input index.
    """
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, float("inf"))
    return 100.0 - (100.0 / (1.0 + rs))


def _find_swings(series: pd.Series, window: int = 3) -> tuple[list[int], list[int]]:
    """Find local peaks and troughs using a 3-bar swing window.

    Args:
        series: Numeric series to scan.
        window: Bars on each side of the candidate bar.

    Returns:
        Tuple of (peak_indices, trough_indices).
    """
    peaks: list[int] = []
    troughs: list[int] = []
    vals = series.values
    n = len(vals)

    for i in range(window, n - window):
        segment = vals[i - window : i + window + 1]
        if vals[i] == max(segment):
            peaks.append(i)
        elif vals[i] == min(segment):
            troughs.append(i)

    return peaks, troughs


def _detect_rsi_divergence(
    closes: pd.Series,
    rsi: pd.Series,
    lookback: int = 30,
) -> tuple[str, float, int]:
    """Detect regular bullish/bearish divergence between price and RSI.

    Args:
        closes: Closing price series.
        rsi: RSI series.
        lookback: Number of recent bars to scan.

    Returns:
        Tuple of (pattern, strength, bars_since_last_divergence).
    """
    if len(closes) < lookback:
        return "none", 0.0, 999

    c = closes.tail(lookback).reset_index(drop=True)
    r = rsi.tail(lookback).reset_index(drop=True)

    price_peaks, price_troughs = _find_swings(c)
    rsi_peaks, rsi_troughs = _find_swings(r)

    best_div = "none"
    best_strength = 0.0

    # Bearish regular: price HH, RSI LH
    for i in range(1, len(price_peaks)):
        p1, p2 = price_peaks[i - 1], price_peaks[i]
        if c[p2] > c[p1]:
            matching_rsi = [j for j in rsi_peaks if abs(j - p2) <= 2]
            if matching_rsi:
                r2 = max(matching_rsi, key=lambda j: abs(j - p2))
                earlier_rsi = [j for j in rsi_peaks if abs(j - p1) <= 2]
                if earlier_rsi:
                    r1 = max(earlier_rsi, key=lambda j: abs(j - p1))
                    if r[r2] < r[r1]:
                        price_delta = abs(c[p2] - c[p1]) / max(c[p1], 1e-9)
                        rsi_delta = abs(r[r2] - r[r1]) / 100.0
                        strength = min(1.0, rsi_delta / max(price_delta, 1e-9) * 0.5)
                        if strength > best_strength:
                            best_div = "bearish_regular"
                            best_strength = strength

    # Bullish regular: price LL, RSI HL
    for i in range(1, len(price_troughs)):
        p1, p2 = price_troughs[i - 1], price_troughs[i]
        if c[p2] < c[p1]:
            matching_rsi = [j for j in rsi_troughs if abs(j - p2) <= 2]
            if matching_rsi:
                r2 = max(matching_rsi, key=lambda j: abs(j - p2))
                earlier_rsi = [j for j in rsi_troughs if abs(j - p1) <= 2]
                if earlier_rsi:
                    r1 = max(earlier_rsi, key=lambda j: abs(j - p1))
                    if r[r2] > r[r1]:
                        price_delta = abs(c[p2] - c[p1]) / max(c[p1], 1e-9)
                        rsi_delta = abs(r[r2] - r[r1]) / 100.0
                        strength = min(1.0, rsi_delta / max(price_delta, 1e-9) * 0.5)
                        if strength > best_strength:
                            best_div = "bullish_regular"
                            best_strength = strength

    bars_since = 0 if best_div != "none" else 999
    return best_div, round(best_strength, 3), bars_since


def compute_rsi_feature(
    closes: pd.Series,
    timeframe: str = "1D",
    period: int = 14,
) -> RsiFeature | None:
    """Compute RSI with divergence detection.

    Args:
        closes: Time-ordered closing prices (oldest first).
        timeframe: Timeframe label.
        period: RSI period (default 14).

    Returns:
        RsiFeature or None if insufficient history.
    """
    min_bars = period + 20
    if len(closes) < min_bars:
        logger.warning(
            "rsi_feature: insufficient bars (need %d, got %d)", min_bars, len(closes)
        )
        return None

    rsi = _wilders_rsi(closes, period)
    current = float(rsi.iloc[-1])

    # 7-bar trend
    if len(rsi) >= 7:
        delta = float(rsi.iloc[-1]) - float(rsi.iloc[-7])
        trend: str = "rising" if delta > 1 else ("falling" if delta < -1 else "flat")
    else:
        trend = "flat"

    # Midline cross (50)
    midline_cross_days = 0
    for i in range(len(rsi) - 2, -1, -1):
        v1 = float(rsi.iloc[i])
        v2 = float(rsi.iloc[i + 1])
        if (v1 < 50 and v2 >= 50) or (v1 > 50 and v2 <= 50):
            midline_cross_days = len(rsi) - 1 - i
            break

    divergence, div_strength, bars_since = _detect_rsi_divergence(closes, rsi)

    return RsiFeature(
        timeframe=timeframe,
        period=period,
        current_rsi=round(current, 2),
        rsi_7d_trend=trend,  # type: ignore[arg-type]
        overbought=current > 70,
        oversold=current < 30,
        divergence=divergence,  # type: ignore[arg-type]
        divergence_strength=div_strength,
        bars_since_last_divergence=bars_since,
        midline_cross_days=midline_cross_days,
    )


def format_rsi_for_prompt(rsi: RsiFeature) -> str:
    """Format RsiFeature as a compact string for LLM prompts.

    Args:
        rsi: Computed RSI feature.

    Returns:
        Formatted XML-like string.
    """
    div_str = (
        f" divergence={rsi.divergence} strength={rsi.divergence_strength}"
        if rsi.divergence != "none"
        else " divergence=none"
    )
    tf = rsi.timeframe.lower()
    return f"<rsi_{tf}>val={rsi.current_rsi:.0f} trend={rsi.rsi_7d_trend}{div_str}</rsi_{tf}>"


# ---------------------------------------------------------------------------
# MACD state
# ---------------------------------------------------------------------------


@dataclass
class MacdState:
    """MACD computation result with histogram dynamics.

    Attributes:
        timeframe: Timeframe label.
        fast_period: Fast EMA period.
        slow_period: Slow EMA period.
        signal_period: Signal line EMA period.
        macd_value: Current MACD line value.
        signal_value: Current signal line value.
        histogram: Current histogram value.
        above_signal: True if MACD > signal.
        days_since_cross: Bars since last MACD/signal crossover.
        cross_type: Type of most recent crossover.
        histogram_direction: Whether histogram is expanding or contracting.
        histogram_acceleration: Whether the histogram change is accelerating.
        zero_line_position: Whether MACD is above or below zero.
        zero_line_cross_days: Bars since last zero-line cross.
        divergence: Divergence type (currently always "none" for MACD).
    """

    timeframe: str
    fast_period: int
    slow_period: int
    signal_period: int
    macd_value: float
    signal_value: float
    histogram: float
    above_signal: bool
    days_since_cross: int
    cross_type: Literal["bullish_cross", "bearish_cross", "no_recent_cross"]
    histogram_direction: Literal[
        "expanding_positive",
        "contracting_positive",
        "contracting_negative",
        "expanding_negative",
    ]
    histogram_acceleration: Literal["accelerating", "decelerating", "steady"]
    zero_line_position: Literal["above", "below"]
    zero_line_cross_days: int
    divergence: Literal[
        "bullish_regular",
        "bearish_regular",
        "bullish_hidden",
        "bearish_hidden",
        "none",
    ]


def _classify_histogram_direction(hist_series: pd.Series) -> str:
    """Classify histogram as expanding or contracting positive/negative.

    Args:
        hist_series: MACD histogram series.

    Returns:
        One of four histogram direction strings.
    """
    if len(hist_series) < 2:
        return "expanding_positive" if float(hist_series.iloc[-1]) > 0 else "expanding_negative"
    last = float(hist_series.iloc[-1])
    prev = float(hist_series.iloc[-2])
    if last >= 0:
        return "expanding_positive" if last > prev else "contracting_positive"
    return "expanding_negative" if last < prev else "contracting_negative"


def _classify_acceleration(hist_series: pd.Series) -> str:
    """Classify histogram change as accelerating, decelerating, or steady.

    Args:
        hist_series: MACD histogram series.

    Returns:
        One of "accelerating", "decelerating", "steady".
    """
    if len(hist_series) < 3:
        return "steady"
    last3 = hist_series.tail(3).values
    d1 = last3[1] - last3[0]
    d2 = last3[2] - last3[1]
    if d1 > 0 and d2 > d1:
        return "accelerating"
    if d2 < d1:
        return "decelerating"
    return "steady"


def _days_since_cross(macd: pd.Series, signal: pd.Series) -> tuple[int, str]:
    """Count bars since last MACD/signal line crossover.

    Args:
        macd: MACD line series.
        signal: Signal line series.

    Returns:
        Tuple of (bars_since_cross, cross_type).
    """
    above = macd > signal
    for i in range(len(above) - 1, 0, -1):
        if above.iloc[i] != above.iloc[i - 1]:
            days = len(above) - 1 - i
            ct = "bullish_cross" if above.iloc[i] else "bearish_cross"
            return days, ct
    return 999, "no_recent_cross"


def _days_since_zero_cross(macd: pd.Series) -> int:
    """Count bars since MACD last crossed the zero line.

    Args:
        macd: MACD line series.

    Returns:
        Number of bars since zero-line cross.
    """
    for i in range(len(macd) - 1, 0, -1):
        if (macd.iloc[i] >= 0) != (macd.iloc[i - 1] >= 0):
            return len(macd) - 1 - i
    return 999


def compute_macd_state(
    closes: pd.Series,
    timeframe: str = "1D",
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> MacdState | None:
    """Compute MACD state with histogram dynamics.

    Args:
        closes: Time-ordered closing prices (oldest first).
        timeframe: Timeframe label.
        fast: Fast EMA period.
        slow: Slow EMA period.
        signal_period: Signal line EMA period.

    Returns:
        MacdState or None if insufficient history.
    """
    min_bars = slow + signal_period + 5
    if len(closes) < min_bars:
        logger.warning(
            "macd_state: insufficient bars (need %d, got %d)", min_bars, len(closes)
        )
        return None

    ema_fast = closes.ewm(span=fast, adjust=False).mean()
    ema_slow = closes.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    signal_line = macd.ewm(span=signal_period, adjust=False).mean()
    hist = macd - signal_line

    macd_val = float(macd.iloc[-1])
    signal_val = float(signal_line.iloc[-1])
    hist_val = float(hist.iloc[-1])

    days_cross, cross_type = _days_since_cross(macd, signal_line)
    zero_cross_days = _days_since_zero_cross(macd)

    return MacdState(
        timeframe=timeframe,
        fast_period=fast,
        slow_period=slow,
        signal_period=signal_period,
        macd_value=round(macd_val, 4),
        signal_value=round(signal_val, 4),
        histogram=round(hist_val, 4),
        above_signal=macd_val > signal_val,
        days_since_cross=days_cross,
        cross_type=cross_type,  # type: ignore[arg-type]
        histogram_direction=_classify_histogram_direction(hist),  # type: ignore[arg-type]
        histogram_acceleration=_classify_acceleration(hist),  # type: ignore[arg-type]
        zero_line_position="above" if macd_val >= 0 else "below",
        zero_line_cross_days=zero_cross_days,
        divergence="none",
    )


def format_macd_for_prompt(m: MacdState) -> str:
    """Format MacdState as a compact string for LLM prompts.

    Args:
        m: Computed MACD state.

    Returns:
        Formatted XML-like string.
    """
    tf = m.timeframe.lower()
    return (
        f"<macd_{tf}>above_sig={str(m.above_signal).lower()} "
        f"days_since={m.days_since_cross} hist={m.histogram_direction} "
        f"accel={m.histogram_acceleration}</macd_{tf}>"
    )
