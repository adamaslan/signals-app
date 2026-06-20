"""Trend signal detectors.

Covers: Moving average crossovers (simple + expanded grid), ADX trend strength,
Ichimoku Cloud, Bollinger Band breakouts, High/Low proximity, MA distance.

Ported from gcp-app-w-mcp1/mcp-finance1/src/technical_analysis_mcp/signals.py.
"""
from __future__ import annotations

import logging
import math

import pandas as pd

from signals_app.config import (
    ADX_TRENDING,
    LARGE_MOVE_PERCENT,
    SignalCategory,
    SignalStrength,
)
from signals_app.detection.base import MutableSignal
from signals_app.indicators.grids import (
    HL_LOOKBACKS,
    HL_PROXIMITIES,
    MA_CROSS_PAIRS,
    MA_DIST_PERIODS,
    MA_DIST_THRESHOLDS,
)

logger = logging.getLogger(__name__)


def _sf(val: object) -> float | None:
    """Safe float conversion — returns None on NaN/Inf/None."""
    try:
        v = float(val)  # type: ignore[arg-type]
        return None if (math.isnan(v) or math.isinf(v)) else v
    except Exception:
        return None


class MovingAverageSignalDetector:
    """Detects MA crossover and alignment signals (standard 50/200 golden/death cross)."""

    def detect(self, df: pd.DataFrame) -> list[MutableSignal]:
        """Detect standard MA signals.

        Args:
            df: Indicator DataFrame.

        Returns:
            List of MutableSignal objects.
        """
        if len(df) < 2:
            return []

        signals: list[MutableSignal] = []
        current = df.iloc[-1]
        prev = df.iloc[-2]

        signals.extend(self._detect_ma_crossovers(current, prev, df))
        signals.extend(self._detect_price_ma_crosses(current, prev))
        signals.extend(self._detect_ma_alignment(current))

        return signals

    def _detect_ma_crossovers(
        self, current: pd.Series, prev: pd.Series, df: pd.DataFrame
    ) -> list[MutableSignal]:
        """Detect golden cross and death cross."""
        signals: list[MutableSignal] = []

        if len(df) <= 200:
            return signals
        if "SMA_50" not in current.index or "SMA_200" not in current.index:
            return signals

        if prev["SMA_50"] <= prev["SMA_200"] and current["SMA_50"] > current["SMA_200"]:
            signals.append(MutableSignal(
                signal="GOLDEN CROSS",
                description="50 MA crossed above 200 MA",
                strength=SignalStrength.STRONG_BULLISH.value,
                category=SignalCategory.MA_CROSS.value,
            ))

        if prev["SMA_50"] >= prev["SMA_200"] and current["SMA_50"] < current["SMA_200"]:
            signals.append(MutableSignal(
                signal="DEATH CROSS",
                description="50 MA crossed below 200 MA",
                strength=SignalStrength.STRONG_BEARISH.value,
                category=SignalCategory.MA_CROSS.value,
            ))

        return signals

    def _detect_price_ma_crosses(
        self, current: pd.Series, prev: pd.Series
    ) -> list[MutableSignal]:
        """Detect price crossing above/below 20 SMA."""
        signals: list[MutableSignal] = []

        if "SMA_20" not in current.index:
            return signals

        if prev["Close"] <= prev["SMA_20"] and current["Close"] > current["SMA_20"]:
            signals.append(MutableSignal(
                signal="PRICE ABOVE 20 MA",
                description="Price crossed above 20-day MA",
                strength=SignalStrength.BULLISH.value,
                category=SignalCategory.MA_CROSS.value,
            ))

        if prev["Close"] >= prev["SMA_20"] and current["Close"] < current["SMA_20"]:
            signals.append(MutableSignal(
                signal="PRICE BELOW 20 MA",
                description="Price crossed below 20-day MA",
                strength=SignalStrength.BEARISH.value,
                category=SignalCategory.MA_CROSS.value,
            ))

        return signals

    def _detect_ma_alignment(self, current: pd.Series) -> list[MutableSignal]:
        """Detect bullish/bearish MA stack alignment."""
        signals: list[MutableSignal] = []

        required = ["SMA_10", "SMA_20", "SMA_50"]
        if not all(col in current.index for col in required):
            return signals

        if current["SMA_10"] > current["SMA_20"] > current["SMA_50"]:
            signals.append(MutableSignal(
                signal="MA ALIGNMENT BULLISH",
                description="10 > 20 > 50 SMA",
                strength=SignalStrength.STRONG_BULLISH.value,
                category=SignalCategory.MA_TREND.value,
            ))

        if current["SMA_10"] < current["SMA_20"] < current["SMA_50"]:
            signals.append(MutableSignal(
                signal="MA ALIGNMENT BEARISH",
                description="10 < 20 < 50 SMA",
                strength=SignalStrength.STRONG_BEARISH.value,
                category=SignalCategory.MA_TREND.value,
            ))

        return signals


class ExpandedMACrossDetector:
    """Detects crossovers across all 11 fast/slow SMA pairs from MA_CROSS_PAIRS."""

    def detect(self, df: pd.DataFrame) -> list[MutableSignal]:
        """Detect expanded MA cross signals.

        Args:
            df: Indicator DataFrame.

        Returns:
            List of MutableSignal objects.
        """
        if len(df) < 2:
            return []

        signals: list[MutableSignal] = []
        current = df.iloc[-1]
        prev = df.iloc[-2]

        for fast, slow in MA_CROSS_PAIRS:
            cf = f"SMA_{fast}"
            cs = f"SMA_{slow}"
            if cf not in current.index or cs not in current.index:
                continue

            cur_f = _sf(current[cf])
            cur_s = _sf(current[cs])
            pre_f = _sf(prev[cf])
            pre_s = _sf(prev[cs])

            if None in (cur_f, cur_s, pre_f, pre_s):
                continue

            if pre_f <= pre_s and cur_f > cur_s:  # type: ignore[operator]
                is_golden = (fast, slow) == (50, 200)
                label = "GOLDEN CROSS" if is_golden else f"{fast}/{slow} MA BULL CROSS"
                strength = SignalStrength.STRONG_BULLISH.value if is_golden else SignalStrength.BULLISH.value
                signals.append(MutableSignal(
                    signal=label,
                    description=f"{fast} SMA crossed above {slow} SMA",
                    strength=strength,
                    category=SignalCategory.MA_CROSS.value,
                ))
            elif pre_f >= pre_s and cur_f < cur_s:  # type: ignore[operator]
                is_death = (fast, slow) == (50, 200)
                label = "DEATH CROSS" if is_death else f"{fast}/{slow} MA BEAR CROSS"
                strength = SignalStrength.STRONG_BEARISH.value if is_death else SignalStrength.BEARISH.value
                signals.append(MutableSignal(
                    signal=label,
                    description=f"{fast} SMA crossed below {slow} SMA",
                    strength=strength,
                    category=SignalCategory.MA_CROSS.value,
                ))

        return signals


class TrendSignalDetector:
    """Detects ADX-based trend strength signals."""

    def detect(self, df: pd.DataFrame) -> list[MutableSignal]:
        """Detect trend strength signals.

        Args:
            df: Indicator DataFrame.

        Returns:
            List of MutableSignal objects.
        """
        if len(df) < 1:
            return []

        required = ["ADX", "Close", "SMA_50"]
        if not all(col in df.columns for col in required):
            return []

        signals: list[MutableSignal] = []
        current = df.iloc[-1]
        adx = _sf(current["ADX"])

        if adx is None:
            return signals

        if adx > ADX_TRENDING:
            trend = "UP" if _sf(current["Close"]) > _sf(current["SMA_50"]) else "DOWN"  # type: ignore[operator]
            signals.append(MutableSignal(
                signal=f"STRONG {trend}TREND",
                description=f"ADX: {adx:.1f}",
                strength=SignalStrength.TRENDING.value,
                category=SignalCategory.TREND.value,
            ))

        return signals


class IchimokuDetector:
    """Ichimoku Cloud signals: TK cross, price vs cloud, Kumo color."""

    def detect(self, df: pd.DataFrame) -> list[MutableSignal]:
        """Detect Ichimoku signals.

        Args:
            df: Indicator DataFrame.

        Returns:
            List of MutableSignal objects.
        """
        if len(df) < 2:
            return []

        required = ["Ichimoku_Tenkan", "Ichimoku_Kijun", "Ichimoku_SpanA", "Ichimoku_SpanB"]
        if not all(c in df.columns for c in required):
            return []

        signals: list[MutableSignal] = []
        current = df.iloc[-1]
        prev = df.iloc[-2]

        tenkan = _sf(current["Ichimoku_Tenkan"])
        kijun = _sf(current["Ichimoku_Kijun"])
        span_a = _sf(current["Ichimoku_SpanA"])
        span_b = _sf(current["Ichimoku_SpanB"])
        close = _sf(current["Close"])

        if None in (tenkan, kijun, span_a, span_b, close):
            return signals

        prev_tenkan = _sf(prev["Ichimoku_Tenkan"]) or tenkan
        prev_kijun = _sf(prev["Ichimoku_Kijun"]) or kijun
        cloud_top = max(span_a, span_b)  # type: ignore[type-var]
        cloud_bot = min(span_a, span_b)  # type: ignore[type-var]

        if prev_tenkan <= prev_kijun and tenkan > kijun:  # type: ignore[operator]
            signals.append(MutableSignal(
                signal="ICHIMOKU TK BULL CROSS",
                description=f"Tenkan ({tenkan:.2f}) crossed above Kijun ({kijun:.2f})",
                strength=SignalStrength.STRONG_BULLISH.value,
                category=SignalCategory.ICHIMOKU.value,
            ))
        elif prev_tenkan >= prev_kijun and tenkan < kijun:  # type: ignore[operator]
            signals.append(MutableSignal(
                signal="ICHIMOKU TK BEAR CROSS",
                description=f"Tenkan ({tenkan:.2f}) crossed below Kijun ({kijun:.2f})",
                strength=SignalStrength.STRONG_BEARISH.value,
                category=SignalCategory.ICHIMOKU.value,
            ))

        if close > cloud_top:
            signals.append(MutableSignal(
                signal="PRICE ABOVE KUMO",
                description=f"Close ${close:.2f} above cloud top ${cloud_top:.2f}",
                strength=SignalStrength.BULLISH.value,
                category=SignalCategory.ICHIMOKU.value,
            ))
        elif close < cloud_bot:
            signals.append(MutableSignal(
                signal="PRICE BELOW KUMO",
                description=f"Close ${close:.2f} below cloud bottom ${cloud_bot:.2f}",
                strength=SignalStrength.BEARISH.value,
                category=SignalCategory.ICHIMOKU.value,
            ))
        else:
            signals.append(MutableSignal(
                signal="PRICE INSIDE KUMO",
                description=f"Close ${close:.2f} inside cloud (indecision)",
                strength=SignalStrength.NEUTRAL.value,
                category=SignalCategory.ICHIMOKU.value,
            ))

        if span_a > span_b:
            signals.append(MutableSignal(
                signal="BULLISH KUMO",
                description=f"Green cloud: SpanA ({span_a:.2f}) > SpanB ({span_b:.2f})",
                strength=SignalStrength.BULLISH.value,
                category=SignalCategory.ICHIMOKU.value,
            ))
        else:
            signals.append(MutableSignal(
                signal="BEARISH KUMO",
                description=f"Red cloud: SpanA ({span_a:.2f}) < SpanB ({span_b:.2f})",
                strength=SignalStrength.BEARISH.value,
                category=SignalCategory.ICHIMOKU.value,
            ))

        return signals


class BollingerBandSignalDetector:
    """Detects Bollinger Band touch signals (standard 20, 2.0)."""

    def detect(self, df: pd.DataFrame) -> list[MutableSignal]:
        """Detect standard Bollinger Band signals.

        Args:
            df: Indicator DataFrame.

        Returns:
            List of MutableSignal objects.
        """
        if len(df) < 1:
            return []

        required = ["BB_Upper", "BB_Lower", "BB_Width"]
        if not all(col in df.columns for col in required):
            return []

        signals: list[MutableSignal] = []
        current = df.iloc[-1]
        close = _sf(current["Close"])
        upper = _sf(current["BB_Upper"])
        lower = _sf(current["BB_Lower"])

        if None in (close, upper, lower):
            return signals

        if close <= lower * 1.01:  # type: ignore[operator]
            signals.append(MutableSignal(
                signal="AT LOWER BB",
                description=f"Price at ${lower:.2f}",
                strength=SignalStrength.BULLISH.value,
                category=SignalCategory.BOLLINGER.value,
            ))

        if close >= upper * 0.99:  # type: ignore[operator]
            signals.append(MutableSignal(
                signal="AT UPPER BB",
                description=f"Price at ${upper:.2f}",
                strength=SignalStrength.BEARISH.value,
                category=SignalCategory.BOLLINGER.value,
            ))

        return signals


class BBExpansionDetector:
    """Bollinger Band breakout signals: price above/below band, %B extremes, band rides."""

    def detect(self, df: pd.DataFrame) -> list[MutableSignal]:
        """Detect expanded Bollinger Band signals.

        Args:
            df: Indicator DataFrame.

        Returns:
            List of MutableSignal objects.
        """
        if len(df) < 2:
            return []

        signals: list[MutableSignal] = []
        current = df.iloc[-1]
        prev = df.iloc[-2]
        close = _sf(current.get("Close"))
        prev_close = _sf(prev.get("Close"))

        if close is None:
            return signals

        for period in (10, 20, 30, 50):
            for sd in (1.5, 2.0, 2.5, 3.0):
                sd_tag = str(sd).replace(".", "_")
                upper_col = f"BB_{period}_{sd_tag}_Upper"
                lower_col = f"BB_{period}_{sd_tag}_Lower"
                pct_col = f"BB_{period}_{sd_tag}_Pct"

                if upper_col not in current.index or lower_col not in current.index:
                    continue

                upper = _sf(current[upper_col])
                lower = _sf(current[lower_col])

                if upper is None or lower is None:
                    continue

                label = f"BB({period},{sd})"

                if close > upper:
                    signals.append(MutableSignal(
                        signal=f"ABOVE UPPER {label}",
                        description=f"Price {close:.2f} above upper band {upper:.2f}",
                        strength=SignalStrength.EXTREME_BULLISH.value,
                        category=SignalCategory.BB_BREAKOUT.value,
                    ))
                elif close < lower:
                    signals.append(MutableSignal(
                        signal=f"BELOW LOWER {label}",
                        description=f"Price {close:.2f} below lower band {lower:.2f}",
                        strength=SignalStrength.EXTREME_BEARISH.value,
                        category=SignalCategory.BB_BREAKOUT.value,
                    ))

                if pct_col in current.index:
                    pct_b = _sf(current[pct_col])
                    if pct_b is not None:
                        if pct_b > 1.0:
                            signals.append(MutableSignal(
                                signal=f"{label} %B > 1",
                                description=f"%B={pct_b:.2f} (overbought)",
                                strength=SignalStrength.BEARISH.value,
                                category=SignalCategory.BB_BREAKOUT.value,
                            ))
                        elif pct_b < 0.0:
                            signals.append(MutableSignal(
                                signal=f"{label} %B < 0",
                                description=f"%B={pct_b:.2f} (oversold)",
                                strength=SignalStrength.BULLISH.value,
                                category=SignalCategory.BB_BREAKOUT.value,
                            ))

                if prev_close is not None and upper_col in prev.index:
                    prev_upper = _sf(prev[upper_col])
                    if prev_upper is not None and prev_close > prev_upper and close > upper:
                        signals.append(MutableSignal(
                            signal=f"{label} RIDING UPPER BAND",
                            description="2 consecutive closes above upper band",
                            strength=SignalStrength.STRONG_BULLISH.value,
                            category=SignalCategory.BB_BREAKOUT.value,
                        ))

        return signals


class PriceActionSignalDetector:
    """Detects large single-bar price move signals."""

    def detect(self, df: pd.DataFrame) -> list[MutableSignal]:
        """Detect price action signals.

        Args:
            df: Indicator DataFrame.

        Returns:
            List of MutableSignal objects.
        """
        if len(df) < 1 or "Price_Change" not in df.columns:
            return []

        signals: list[MutableSignal] = []
        current = df.iloc[-1]
        price_change = _sf(current["Price_Change"])

        if price_change is None:
            return signals

        if price_change > LARGE_MOVE_PERCENT:
            signals.append(MutableSignal(
                signal="LARGE GAIN",
                description=f"+{price_change:.1f}% today",
                strength=SignalStrength.STRONG_BULLISH.value,
                category=SignalCategory.PRICE_ACTION.value,
            ))

        if price_change < -LARGE_MOVE_PERCENT:
            signals.append(MutableSignal(
                signal="LARGE LOSS",
                description=f"{price_change:.1f}% today",
                strength=SignalStrength.STRONG_BEARISH.value,
                category=SignalCategory.PRICE_ACTION.value,
            ))

        return signals


class HLProximityDetector:
    """Signals when price is near multi-period highs or lows."""

    def detect(self, df: pd.DataFrame) -> list[MutableSignal]:
        """Detect high/low proximity signals.

        Args:
            df: Indicator DataFrame.

        Returns:
            List of MutableSignal objects.
        """
        if len(df) < 1:
            return []

        signals: list[MutableSignal] = []
        current = df.iloc[-1]
        close = _sf(current.get("Close"))

        if close is None:
            return signals

        for lb in HL_LOOKBACKS:
            high_col = f"High_{lb}b"
            low_col = f"Low_{lb}b"
            if high_col not in current.index or low_col not in current.index:
                continue

            high_val = _sf(current[high_col])
            low_val = _sf(current[low_col])

            if high_val is None or low_val is None:
                continue

            for prox in HL_PROXIMITIES:
                if high_val != 0 and close >= high_val * (1 - prox):
                    strength = (
                        SignalStrength.EXTREME_BULLISH.value
                        if prox <= 0.01
                        else SignalStrength.BULLISH.value
                    )
                    signals.append(MutableSignal(
                        signal=f"WITHIN {int(prox * 100)}% OF {lb}b HIGH",
                        description=f"Close {close:.2f} within {prox * 100:.0f}% of {lb}-bar high {high_val:.2f}",
                        strength=strength,
                        category=SignalCategory.RANGE.value,
                    ))
                if low_val != 0 and close <= low_val * (1 + prox):
                    strength = (
                        SignalStrength.EXTREME_BEARISH.value
                        if prox <= 0.01
                        else SignalStrength.BEARISH.value
                    )
                    signals.append(MutableSignal(
                        signal=f"WITHIN {int(prox * 100)}% OF {lb}b LOW",
                        description=f"Close {close:.2f} within {prox * 100:.0f}% of {lb}-bar low {low_val:.2f}",
                        strength=strength,
                        category=SignalCategory.RANGE.value,
                    ))

        return signals


class MADistanceExpandedDetector:
    """Signals when price is significantly extended from key SMAs."""

    def detect(self, df: pd.DataFrame) -> list[MutableSignal]:
        """Detect MA distance signals.

        Args:
            df: Indicator DataFrame.

        Returns:
            List of MutableSignal objects.
        """
        if len(df) < 1:
            return []

        signals: list[MutableSignal] = []
        current = df.iloc[-1]

        for period in MA_DIST_PERIODS:
            dist_col = f"Dist_SMA_{period}"
            if dist_col not in current.index:
                continue

            dist = _sf(current[dist_col])
            if dist is None:
                continue

            for thresh in MA_DIST_THRESHOLDS:
                if dist > thresh:
                    signals.append(MutableSignal(
                        signal=f">{thresh:.0f}% ABOVE {period}SMA",
                        description=f"{dist:.1f}% above {period}-period SMA",
                        strength=SignalStrength.BEARISH.value,
                        category=SignalCategory.MA_DISTANCE.value,
                    ))
                elif dist < -thresh:
                    signals.append(MutableSignal(
                        signal=f">{thresh:.0f}% BELOW {period}SMA",
                        description=f"{abs(dist):.1f}% below {period}-period SMA",
                        strength=SignalStrength.BULLISH.value,
                        category=SignalCategory.MA_DISTANCE.value,
                    ))

        return signals
