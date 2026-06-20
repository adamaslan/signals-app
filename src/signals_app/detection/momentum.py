"""Momentum signal detectors.

Covers: RSI (simple + multi-period), MACD (standard + multi-param),
Stochastic (simple + cross).

Ported from gcp-app-w-mcp1/mcp-finance1/src/technical_analysis_mcp/signals.py.
"""
from __future__ import annotations

import logging
import math

import pandas as pd

from signals_app.config import (
    RSI_EXTREME_OVERSOLD,
    RSI_OVERBOUGHT,
    RSI_OVERSOLD,
    STOCH_OVERBOUGHT,
    STOCH_OVERSOLD,
    SignalCategory,
    SignalStrength,
)
from signals_app.detection.base import MutableSignal
from signals_app.indicators.grids import MACD_PARAMS, RSI_OS_OB_LEVELS, RSI_PERIODS

logger = logging.getLogger(__name__)


def _sf(val: object) -> float | None:
    """Safe float conversion — returns None on NaN/Inf/None.

    Args:
        val: Value to convert.

    Returns:
        Float or None.
    """
    try:
        v = float(val)  # type: ignore[arg-type]
        return None if (math.isnan(v) or math.isinf(v)) else v
    except Exception:
        return None


class RSISignalDetector:
    """Detects standard RSI overbought/oversold signals (period=14)."""

    def detect(self, df: pd.DataFrame) -> list[MutableSignal]:
        """Detect RSI signals.

        Args:
            df: Indicator DataFrame.

        Returns:
            List of MutableSignal objects.
        """
        if len(df) < 1 or "RSI" not in df.columns:
            return []

        signals: list[MutableSignal] = []
        current = df.iloc[-1]
        rsi = _sf(current["RSI"])

        if rsi is None:
            return signals

        if rsi < RSI_EXTREME_OVERSOLD:
            signals.append(MutableSignal(
                signal="RSI EXTREME OVERSOLD",
                description=f"RSI at {rsi:.1f}",
                strength=SignalStrength.STRONG_BULLISH.value,
                category=SignalCategory.RSI.value,
            ))
        elif rsi < RSI_OVERSOLD:
            signals.append(MutableSignal(
                signal="RSI OVERSOLD",
                description=f"RSI at {rsi:.1f}",
                strength=SignalStrength.BULLISH.value,
                category=SignalCategory.RSI.value,
            ))

        if rsi > RSI_OVERBOUGHT:
            signals.append(MutableSignal(
                signal="RSI OVERBOUGHT",
                description=f"RSI at {rsi:.1f}",
                strength=SignalStrength.BEARISH.value,
                category=SignalCategory.RSI.value,
            ))

        return signals


class MultiRSIDetector:
    """RSI signals across multiple periods and OS/OB threshold pairs."""

    def detect(self, df: pd.DataFrame) -> list[MutableSignal]:
        """Detect multi-period RSI signals.

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

        for period in RSI_PERIODS:
            col = f"RSI_{period}" if period != 14 else "RSI"
            if col not in current.index:
                continue

            rsi = _sf(current[col])
            prev_rsi = _sf(prev[col])
            if rsi is None:
                continue

            for os_lvl, ob_lvl in RSI_OS_OB_LEVELS:
                if rsi < os_lvl:
                    signals.append(MutableSignal(
                        signal=f"RSI{period} OVERSOLD (<{os_lvl:.0f})",
                        description=f"RSI({period}): {rsi:.1f}",
                        strength=SignalStrength.BULLISH.value,
                        category=SignalCategory.RSI.value,
                    ))
                elif rsi > ob_lvl:
                    signals.append(MutableSignal(
                        signal=f"RSI{period} OVERBOUGHT (>{ob_lvl:.0f})",
                        description=f"RSI({period}): {rsi:.1f}",
                        strength=SignalStrength.BEARISH.value,
                        category=SignalCategory.RSI.value,
                    ))

            if prev_rsi is not None:
                if prev_rsi < 50 <= rsi:
                    signals.append(MutableSignal(
                        signal=f"RSI{period} CROSSED 50 BULL",
                        description=f"RSI({period}) crossed above 50: {rsi:.1f}",
                        strength=SignalStrength.BULLISH.value,
                        category=SignalCategory.RSI.value,
                    ))
                elif prev_rsi > 50 >= rsi:
                    signals.append(MutableSignal(
                        signal=f"RSI{period} CROSSED 50 BEAR",
                        description=f"RSI({period}) crossed below 50: {rsi:.1f}",
                        strength=SignalStrength.BEARISH.value,
                        category=SignalCategory.RSI.value,
                    ))

        return signals


class MACDSignalDetector:
    """Detects standard MACD crossover and zero-line signals."""

    def detect(self, df: pd.DataFrame) -> list[MutableSignal]:
        """Detect MACD signals.

        Args:
            df: Indicator DataFrame.

        Returns:
            List of MutableSignal objects.
        """
        if len(df) < 2:
            return []

        required = ["MACD", "MACD_Signal"]
        if not all(col in df.columns for col in required):
            return []

        signals: list[MutableSignal] = []
        current = df.iloc[-1]
        prev = df.iloc[-2]

        macd = _sf(current["MACD"])
        macd_sig = _sf(current["MACD_Signal"])
        prev_macd = _sf(prev["MACD"])
        prev_sig = _sf(prev["MACD_Signal"])

        if None in (macd, macd_sig, prev_macd, prev_sig):
            return signals

        if prev_macd <= prev_sig and macd > macd_sig:  # type: ignore[operator]
            signals.append(MutableSignal(
                signal="MACD BULL CROSS",
                description="MACD crossed above signal",
                strength=SignalStrength.BULLISH.value,
                category=SignalCategory.MACD.value,
            ))

        if prev_macd >= prev_sig and macd < macd_sig:  # type: ignore[operator]
            signals.append(MutableSignal(
                signal="MACD BEAR CROSS",
                description="MACD crossed below signal",
                strength=SignalStrength.BEARISH.value,
                category=SignalCategory.MACD.value,
            ))

        if prev_macd <= 0 < macd:  # type: ignore[operator]
            signals.append(MutableSignal(
                signal="MACD ZERO CROSS UP",
                description="MACD crossed above zero",
                strength=SignalStrength.BULLISH.value,
                category=SignalCategory.MACD.value,
            ))

        if prev_macd >= 0 > macd:  # type: ignore[operator]
            signals.append(MutableSignal(
                signal="MACD ZERO CROSS DOWN",
                description="MACD crossed below zero",
                strength=SignalStrength.BEARISH.value,
                category=SignalCategory.MACD.value,
            ))

        return signals


class MultiMACDDetector:
    """MACD signals across additional parameter sets beyond the standard (12, 26, 9)."""

    def detect(self, df: pd.DataFrame) -> list[MutableSignal]:
        """Detect multi-param MACD signals.

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

        # Skip the standard set — handled by MACDSignalDetector
        non_standard = [(f, s, sig) for f, s, sig in MACD_PARAMS if not (f == 12 and s == 26 and sig == 9)]

        for fast, slow, sig in non_standard:
            tag = f"_{fast}_{slow}_{sig}"
            macd_col = f"MACD{tag}"
            sig_col = f"MACD_Signal{tag}"
            hist_col = f"MACD_Hist{tag}"

            if macd_col not in current.index or sig_col not in current.index:
                continue

            macd = _sf(current[macd_col])
            macd_sig = _sf(current[sig_col])
            prev_macd = _sf(prev[macd_col])
            prev_sig = _sf(prev[sig_col])

            if None in (macd, macd_sig, prev_macd, prev_sig):
                continue

            label = f"MACD({fast},{slow},{sig})"

            if prev_macd <= prev_sig and macd > macd_sig:  # type: ignore[operator]
                signals.append(MutableSignal(
                    signal=f"{label} BULL CROSS",
                    description="MACD crossed above signal line",
                    strength=SignalStrength.STRONG_BULLISH.value,
                    category=SignalCategory.MACD.value,
                ))
            elif prev_macd >= prev_sig and macd < macd_sig:  # type: ignore[operator]
                signals.append(MutableSignal(
                    signal=f"{label} BEAR CROSS",
                    description="MACD crossed below signal line",
                    strength=SignalStrength.STRONG_BEARISH.value,
                    category=SignalCategory.MACD.value,
                ))

            if prev_macd <= 0 < macd:  # type: ignore[operator]
                signals.append(MutableSignal(
                    signal=f"{label} ZERO BULL",
                    description="MACD crossed above zero",
                    strength=SignalStrength.BULLISH.value,
                    category=SignalCategory.MACD.value,
                ))
            elif prev_macd >= 0 > macd:  # type: ignore[operator]
                signals.append(MutableSignal(
                    signal=f"{label} ZERO BEAR",
                    description="MACD crossed below zero",
                    strength=SignalStrength.BEARISH.value,
                    category=SignalCategory.MACD.value,
                ))

            if hist_col in current.index:
                hist = _sf(current[hist_col])
                prev_hist = _sf(prev[hist_col])
                if hist is not None and prev_hist is not None:
                    if prev_hist <= 0 < hist:
                        signals.append(MutableSignal(
                            signal=f"{label} HIST BULL",
                            description="MACD histogram turned positive",
                            strength=SignalStrength.BULLISH.value,
                            category=SignalCategory.MACD.value,
                        ))
                    elif prev_hist >= 0 > hist:
                        signals.append(MutableSignal(
                            signal=f"{label} HIST BEAR",
                            description="MACD histogram turned negative",
                            strength=SignalStrength.BEARISH.value,
                            category=SignalCategory.MACD.value,
                        ))

        return signals


class StochasticSignalDetector:
    """Detects Stochastic Oscillator overbought/oversold signals."""

    def detect(self, df: pd.DataFrame) -> list[MutableSignal]:
        """Detect Stochastic signals.

        Args:
            df: Indicator DataFrame.

        Returns:
            List of MutableSignal objects.
        """
        if len(df) < 1 or "Stoch_K" not in df.columns:
            return []

        signals: list[MutableSignal] = []
        current = df.iloc[-1]
        stoch_k = _sf(current["Stoch_K"])

        if stoch_k is None:
            return signals

        if stoch_k < STOCH_OVERSOLD:
            signals.append(MutableSignal(
                signal="STOCHASTIC OVERSOLD",
                description=f"K at {stoch_k:.1f}",
                strength=SignalStrength.BULLISH.value,
                category=SignalCategory.STOCHASTIC.value,
            ))

        if stoch_k > STOCH_OVERBOUGHT:
            signals.append(MutableSignal(
                signal="STOCHASTIC OVERBOUGHT",
                description=f"K at {stoch_k:.1f}",
                strength=SignalStrength.BEARISH.value,
                category=SignalCategory.STOCHASTIC.value,
            ))

        return signals


class StochasticCrossDetector:
    """Stochastic %K/%D cross signals in oversold/overbought zones."""

    def detect(self, df: pd.DataFrame) -> list[MutableSignal]:
        """Detect Stochastic cross signals.

        Args:
            df: Indicator DataFrame.

        Returns:
            List of MutableSignal objects.
        """
        if len(df) < 2 or "Stoch_K" not in df.columns:
            return []

        signals: list[MutableSignal] = []
        current = df.iloc[-1]
        prev = df.iloc[-2]

        k = _sf(current.get("Stoch_K"))
        d = _sf(current.get("Stoch_D", k))
        pk = _sf(prev.get("Stoch_K", k))
        pd_ = _sf(prev.get("Stoch_D", d))

        if None in (k, d, pk, pd_):
            return signals

        if pk <= pd_ and k > d and k < 30:  # type: ignore[operator]
            signals.append(MutableSignal(
                signal="STOCH BULL CROSS (OVERSOLD)",
                description=f"%K ({k:.1f}) crossed above %D in oversold zone",
                strength=SignalStrength.STRONG_BULLISH.value,
                category=SignalCategory.STOCHASTIC.value,
            ))
        elif pk >= pd_ and k < d and k > 70:  # type: ignore[operator]
            signals.append(MutableSignal(
                signal="STOCH BEAR CROSS (OVERBOUGHT)",
                description=f"%K ({k:.1f}) crossed below %D in overbought zone",
                strength=SignalStrength.STRONG_BEARISH.value,
                category=SignalCategory.STOCHASTIC.value,
            ))

        return signals
