"""Event detectors added for the graded confluence ranker (spec §8.3 P4).

Each fires only on a change on the latest bar (a cross, a fresh breach, a
swing-confirmed divergence), never on a standing condition. None of them is in
``get_default_detectors()``: the production rankers vote on any directional
signal, so these live in ``get_experimental_detectors()`` and are consumed only
by the graded ranker, where an unearned label has evidence E = 0 and so scores 0.

All checks are causal (read bars <= t only) and the Q4 invariant holds: a
constant-trend series emits no events after warm-up.
"""
from __future__ import annotations

import math

import pandas as pd

from signals_app.config import (
    ICHIMOKU_KIJUN,
    ICHIMOKU_SENKOU_B,
    RSI_OVERBOUGHT,
    RSI_OVERSOLD,
    SignalCategory,
    SignalStrength,
)
from signals_app.detection.base import MutableSignal
from signals_app.indicators.divergence import _detect_rsi_divergence
from signals_app.indicators.pivots import precompute_pivots

RANGE_BREAKOUT_LOOKBACK = 50
PIVOT_TOLERANCE_ATR = 0.25
PIVOT_MIN_BARS = 30
RSI_DIVERGENCE_MIN_BARS = 60


def _sf(val: object) -> float | None:
    """Safe float conversion; None on NaN/Inf/None."""
    try:
        v = float(val)  # type: ignore[arg-type]
        return None if (math.isnan(v) or math.isinf(v)) else v
    except (TypeError, ValueError):
        return None


def _signal(label: str, description: str, bullish: bool, category: SignalCategory) -> MutableSignal:
    strength = SignalStrength.BULLISH if bullish else SignalStrength.BEARISH
    return MutableSignal(
        signal=label, description=description, strength=strength.value, category=category.value
    )


def _has(df: pd.DataFrame, *columns: str) -> bool:
    return len(df) >= 2 and all(c in df.columns for c in columns)


class KumoEventDetector:
    """Cloud breakout/breakdown (X), cloud twist (T) and Chikou cross (T)."""

    def detect(self, df: pd.DataFrame) -> list[MutableSignal]:
        """Detect Ichimoku events on the last bar.

        Args:
            df: Indicator DataFrame, oldest first.

        Returns:
            Zero or more event signals.
        """
        if not _has(df, "Close", "Ichimoku_SpanA", "Ichimoku_SpanB"):
            return []
        signals: list[MutableSignal] = []
        signals += self._cloud_break(df)
        signals += self._twist(df)
        signals += self._chikou_cross(df)
        return signals

    @staticmethod
    def _cloud_break(df: pd.DataFrame) -> list[MutableSignal]:
        cur, prev = df.iloc[-1], df.iloc[-2]
        values = [_sf(r[c]) for r in (cur, prev) for c in ("Close", "Ichimoku_SpanA", "Ichimoku_SpanB")]
        if None in values:
            return []
        close, a, b, p_close, p_a, p_b = values  # type: ignore[misc]
        top, bottom, p_top, p_bottom = max(a, b), min(a, b), max(p_a, p_b), min(p_a, p_b)
        if close > top and p_close <= p_top:
            return [_signal("KUMO BREAKOUT", f"Close {close:.2f} broke above cloud top {top:.2f}",
                            True, SignalCategory.ICHIMOKU)]
        if close < bottom and p_close >= p_bottom:
            return [_signal("KUMO BREAKDOWN", f"Close {close:.2f} broke below cloud bottom {bottom:.2f}",
                            False, SignalCategory.ICHIMOKU)]
        return []

    @staticmethod
    def _leading_spans(df: pd.DataFrame, offset: int) -> tuple[float, float] | None:
        """Unshifted (leading) span A/B as of ``len(df) - offset`` bars."""
        window = df.iloc[: len(df) - offset]
        if len(window) < ICHIMOKU_SENKOU_B or not _has(window, "High", "Low", "Ichimoku_Tenkan",
                                                       "Ichimoku_Kijun"):
            return None
        tail = window.tail(ICHIMOKU_SENKOU_B)
        tenkan, kijun = _sf(window["Ichimoku_Tenkan"].iloc[-1]), _sf(window["Ichimoku_Kijun"].iloc[-1])
        if tenkan is None or kijun is None:
            return None
        return (tenkan + kijun) / 2.0, (tail["High"].max() + tail["Low"].min()) / 2.0

    def _twist(self, df: pd.DataFrame) -> list[MutableSignal]:
        now, before = self._leading_spans(df, 0), self._leading_spans(df, 1)
        if now is None or before is None:
            return []
        was, is_ = before[0] - before[1], now[0] - now[1]
        if was <= 0 < is_:
            return [_signal("KUMO TWIST BULL", "Leading cloud turned green (span A crossed above B)",
                            True, SignalCategory.ICHIMOKU)]
        if was >= 0 > is_:
            return [_signal("KUMO TWIST BEAR", "Leading cloud turned red (span A crossed below B)",
                            False, SignalCategory.ICHIMOKU)]
        return []

    @staticmethod
    def _chikou_cross(df: pd.DataFrame) -> list[MutableSignal]:
        lag = ICHIMOKU_KIJUN
        if len(df) < lag + 2:
            return []
        close = df["Close"]
        now = _sf(close.iloc[-1] - close.iloc[-1 - lag])
        before = _sf(close.iloc[-2] - close.iloc[-2 - lag])
        if now is None or before is None:
            return []
        if before <= 0 < now:
            return [_signal("CHIKOU CROSS BULL", f"Close crossed above the close {lag} bars ago",
                            True, SignalCategory.ICHIMOKU)]
        if before >= 0 > now:
            return [_signal("CHIKOU CROSS BEAR", f"Close crossed below the close {lag} bars ago",
                            False, SignalCategory.ICHIMOKU)]
        return []


class RangeBreakoutDetector:
    """Close beyond the prior N-bar extreme on above-average volume (T)."""

    def detect(self, df: pd.DataFrame) -> list[MutableSignal]:
        """Detect a fresh range breakout / breakdown on the last bar."""
        lb = RANGE_BREAKOUT_LOOKBACK
        if len(df) < 3 or not _has(df, "Close", f"High_{lb}b", f"Low_{lb}b", "Volume", "Volume_MA_20"):
            return []
        cur, prev, prev2 = df.iloc[-1], df.iloc[-2], df.iloc[-3]
        close, p_close = _sf(cur["Close"]), _sf(prev["Close"])
        # The level to clear is the extreme *before* this bar; "fresh" means the
        # prior bar had not already cleared the extreme before *it*, otherwise a
        # steady trend would fire on every bar.
        p_high, p_low = _sf(prev[f"High_{lb}b"]), _sf(prev[f"Low_{lb}b"])
        pp_high, pp_low = _sf(prev2[f"High_{lb}b"]), _sf(prev2[f"Low_{lb}b"])
        volume, avg_volume = _sf(cur["Volume"]), _sf(cur["Volume_MA_20"])
        needed = (close, p_close, p_high, p_low, pp_high, pp_low, volume, avg_volume)
        if None in needed or volume <= avg_volume:  # type: ignore[operator]
            return []
        if close > p_high and p_close <= pp_high:  # type: ignore[operator]
            return [_signal(f"RANGE BREAKOUT {lb}b", f"Close {close:.2f} cleared the {lb}-bar high "
                            f"{p_high:.2f} on above-average volume", True, SignalCategory.RANGE)]
        if close < p_low and p_close >= pp_low:  # type: ignore[operator]
            return [_signal(f"RANGE BREAKDOWN {lb}b", f"Close {close:.2f} lost the {lb}-bar low "
                            f"{p_low:.2f} on above-average volume", False, SignalCategory.RANGE)]
        return []


class RSIZoneExitDetector:
    """RSI leaving an extreme zone: the event form of the in-zone state (X)."""

    def detect(self, df: pd.DataFrame) -> list[MutableSignal]:
        """Detect RSI crossing back above 30 or back below 70 on the last bar."""
        if not _has(df, "RSI"):
            return []
        rsi, prev = _sf(df["RSI"].iloc[-1]), _sf(df["RSI"].iloc[-2])
        if rsi is None or prev is None:
            return []
        if prev < RSI_OVERSOLD <= rsi:
            return [_signal("RSI14 EXIT OVERSOLD", f"RSI left oversold: {prev:.1f} -> {rsi:.1f}",
                            True, SignalCategory.RSI)]
        if prev > RSI_OVERBOUGHT >= rsi:
            return [_signal("RSI14 EXIT OVERBOUGHT", f"RSI left overbought: {prev:.1f} -> {rsi:.1f}",
                            False, SignalCategory.RSI)]
        return []


class RSIDivergenceDetector:
    """Regular RSI/price divergence that *newly* appears on the last bar (T).

    Swing confirmation lags by the swing window, so a divergence is reported
    on the bar its second swing is confirmed, not on every bar it stays true.
    """

    def detect(self, df: pd.DataFrame) -> list[MutableSignal]:
        """Detect a newly confirmed regular divergence."""
        if len(df) < RSI_DIVERGENCE_MIN_BARS or not _has(df, "Close", "RSI"):
            return []
        now, _, _ = _detect_rsi_divergence(df["Close"], df["RSI"])
        before, _, _ = _detect_rsi_divergence(df["Close"].iloc[:-1], df["RSI"].iloc[:-1])
        if now == before:
            return []
        if now == "bullish_regular":
            return [_signal("RSI BULLISH DIVERGENCE", "Price made a lower low while RSI made a higher low",
                            True, SignalCategory.RSI)]
        if now == "bearish_regular":
            return [_signal("RSI BEARISH DIVERGENCE", "Price made a higher high while RSI made a lower high",
                            False, SignalCategory.RSI)]
        return []


class MACDHistTurnDetector:
    """MACD histogram inflection before the signal-line cross (T).

    A histogram *sign* flip is the signal-line cross that ``MACDSignalDetector``
    already reports, so it would be the same event twice. The turn is the bar
    where the histogram stops falling (or rising) while still on the same side
    of zero, which leads that cross.
    """

    def detect(self, df: pd.DataFrame) -> list[MutableSignal]:
        """Detect a histogram turn on the last bar."""
        if len(df) < 3 or "MACD_Hist" not in df.columns:
            return []
        h2, h1, h0 = (_sf(v) for v in df["MACD_Hist"].iloc[-3:])
        if None in (h2, h1, h0):
            return []
        if h0 < 0 and h2 > h1 <= h0 and h0 > h1:  # type: ignore[operator]
            return [_signal("MACD HIST TURN UP", "Histogram stopped falling below zero",
                            True, SignalCategory.MACD)]
        if h0 > 0 and h2 < h1 >= h0 and h0 < h1:  # type: ignore[operator]
            return [_signal("MACD HIST TURN DOWN", "Histogram stopped rising above zero",
                            False, SignalCategory.MACD)]
        return []


class PivotReactionDetector:
    """Price reacts at a confirmed swing level: touch, then close back out (X).

    Reuses the Fibonacci reaction rule on pivot support/resistance: the bar's
    extreme tags the level within tolerance without trading through it, and the
    bar closes back away from the level in the bounce direction. Pivots are
    confirmed ``PIVOT_WINDOW`` bars late, so only confirmed levels are used.
    """

    def detect(self, df: pd.DataFrame) -> list[MutableSignal]:
        """Detect a bounce off support or a rejection at resistance."""
        if len(df) < PIVOT_MIN_BARS or not _has(df, "High", "Low", "Open", "Close", "ATR"):
            return []
        bar = df.iloc[-1]
        high, low, open_, close, atr = (_sf(bar[c]) for c in ("High", "Low", "Open", "Close", "ATR"))
        if None in (high, low, open_, close) or not atr:
            return []
        tol = PIVOT_TOLERANCE_ATR * atr
        # Exclude the current bar so a level can never be created by the bar it is tested on.
        for level in reversed(precompute_pivots(df.iloc[:-1])):
            if level.kind == "support":
                held = level.price - tol <= low <= level.price + tol and close > level.price and close > open_  # type: ignore[operator]
                if held:
                    return [_signal("PIVOT SUPPORT HOLD", f"Bounced off pivot support {level.price:.2f}",
                                    True, SignalCategory.SUPPORT_RESISTANCE)]
            else:
                rejected = level.price - tol <= high <= level.price + tol and close < level.price and close < open_  # type: ignore[operator]
                if rejected:
                    return [_signal("PIVOT RESISTANCE REJECT", f"Rejected at pivot resistance {level.price:.2f}",
                                    False, SignalCategory.SUPPORT_RESISTANCE)]
        return []
