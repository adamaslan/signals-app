"""Volume signal detectors.

Covers: Volume spikes (multi-MA), OBV divergence and EMA cross, CMF.

Ported from gcp-app-w-mcp1/mcp-finance1/src/technical_analysis_mcp/signals.py.
"""
from __future__ import annotations

import logging
import math

import pandas as pd

from signals_app.config import (
    VOLUME_SPIKE_2X,
    VOLUME_SPIKE_3X,
    SignalCategory,
    SignalStrength,
)
from signals_app.detection.base import MutableSignal
from signals_app.indicators.grids import VOLUME_MA_PERIODS

logger = logging.getLogger(__name__)


def _sf(val: object) -> float | None:
    """Safe float conversion — returns None on NaN/Inf/None."""
    try:
        v = float(val)  # type: ignore[arg-type]
        return None if (math.isnan(v) or math.isinf(v)) else v
    except Exception:
        return None


class VolumeSignalDetector:
    """Detects volume spike signals relative to 20-bar average."""

    def detect(self, df: pd.DataFrame) -> list[MutableSignal]:
        """Detect volume spike signals.

        Args:
            df: Indicator DataFrame.

        Returns:
            List of MutableSignal objects.
        """
        if len(df) < 1:
            return []

        required = ["Volume", "Volume_MA_20"]
        if not all(col in df.columns for col in required):
            return []

        signals: list[MutableSignal] = []
        current = df.iloc[-1]

        vol = _sf(current["Volume"])
        vol_ma = _sf(current["Volume_MA_20"])

        if vol is None or vol_ma is None or vol_ma == 0:
            return signals

        ratio = vol / vol_ma

        if ratio > VOLUME_SPIKE_3X:
            signals.append(MutableSignal(
                signal="EXTREME VOLUME 3X",
                description=f"Vol: {vol:,.0f}",
                strength=SignalStrength.VERY_SIGNIFICANT.value,
                category=SignalCategory.VOLUME.value,
            ))
        elif ratio > VOLUME_SPIKE_2X:
            signals.append(MutableSignal(
                signal="VOLUME SPIKE 2X",
                description=f"Vol: {vol:,.0f}",
                strength=SignalStrength.SIGNIFICANT.value,
                category=SignalCategory.VOLUME.value,
            ))

        return signals


class VolumeDivergenceDetector:
    """Volume divergence (price vs volume over 10 bars) and multi-MA spike signals."""

    def detect(self, df: pd.DataFrame) -> list[MutableSignal]:
        """Detect volume divergence and multi-MA spike signals.

        Args:
            df: Indicator DataFrame.

        Returns:
            List of MutableSignal objects.
        """
        if len(df) < 10:
            return []

        signals: list[MutableSignal] = []
        current = df.iloc[-1]
        vol = _sf(current.get("Volume"))

        if vol is None:
            return signals

        # Multi-MA spike signals
        for vm_period in VOLUME_MA_PERIODS:
            col = f"Volume_MA_{vm_period}"
            if col not in current.index:
                continue
            vol_ma = _sf(current[col])
            if vol_ma is None or vol_ma == 0:
                continue
            ratio = vol / vol_ma

            if ratio > 3.0:
                signals.append(MutableSignal(
                    signal=f"VOLUME SPIKE >3x (MA{vm_period})",
                    description=f"Volume {ratio:.1f}x the {vm_period}-bar average",
                    strength=SignalStrength.VERY_SIGNIFICANT.value,
                    category=SignalCategory.VOLUME.value,
                ))
            elif ratio > 2.0:
                signals.append(MutableSignal(
                    signal=f"VOLUME SPIKE >2x (MA{vm_period})",
                    description=f"Volume {ratio:.1f}x the {vm_period}-bar average",
                    strength=SignalStrength.SIGNIFICANT.value,
                    category=SignalCategory.VOLUME.value,
                ))
            elif ratio > 1.5:
                signals.append(MutableSignal(
                    signal=f"VOLUME SPIKE >1.5x (MA{vm_period})",
                    description=f"Volume {ratio:.1f}x the {vm_period}-bar average",
                    strength=SignalStrength.SIGNIFICANT.value,
                    category=SignalCategory.VOLUME.value,
                ))

        # 10-bar volume divergence
        price_chg = (_sf(df["Close"].iloc[-1]) or 0.0) - (_sf(df["Close"].iloc[-10]) or 0.0)
        vol_chg = (_sf(df["Volume"].iloc[-1]) or 0.0) - (_sf(df["Volume"].iloc[-10]) or 0.0)

        if price_chg > 0 and vol_chg < 0:
            signals.append(MutableSignal(
                signal="VOLUME BEARISH DIVERGENCE (10b)",
                description="Price rising but volume falling over 10 bars",
                strength=SignalStrength.BEARISH.value,
                category=SignalCategory.VOLUME.value,
            ))
        elif price_chg < 0 and vol_chg > 0:
            signals.append(MutableSignal(
                signal="VOLUME BULLISH DIVERGENCE (10b)",
                description="Price falling but volume rising over 10 bars",
                strength=SignalStrength.BULLISH.value,
                category=SignalCategory.VOLUME.value,
            ))

        return signals


class OBVCMFDetector:
    """OBV divergence, OBV/EMA cross, and CMF signals."""

    def detect(self, df: pd.DataFrame) -> list[MutableSignal]:
        """Detect OBV and CMF signals.

        Args:
            df: Indicator DataFrame.

        Returns:
            List of MutableSignal objects.
        """
        if len(df) < 20:
            return []

        signals: list[MutableSignal] = []
        current = df.iloc[-1]
        prev = df.iloc[-2]

        # OBV divergence over 20 bars
        if "OBV" in df.columns:
            recent = df.tail(20)
            price_start = _sf(recent["Close"].iloc[0]) or 0.0
            price_end = _sf(recent["Close"].iloc[-1]) or price_start
            obv_start = _sf(recent["OBV"].iloc[0]) or 0.0
            obv_end = _sf(recent["OBV"].iloc[-1]) or obv_start

            price_delta = price_end - price_start
            obv_delta = obv_end - obv_start

            if price_delta > 0 and obv_delta < 0:
                signals.append(MutableSignal(
                    signal="OBV BEARISH DIVERGENCE",
                    description="Price rising but OBV falling",
                    strength=SignalStrength.BEARISH.value,
                    category=SignalCategory.OBV_CMF.value,
                ))
            elif price_delta < 0 and obv_delta > 0:
                signals.append(MutableSignal(
                    signal="OBV BULLISH DIVERGENCE",
                    description="Price falling but OBV rising (accumulation)",
                    strength=SignalStrength.STRONG_BULLISH.value,
                    category=SignalCategory.OBV_CMF.value,
                ))

        # OBV / EMA cross
        if "OBV" in df.columns and "OBV_EMA" in df.columns:
            obv = _sf(current.get("OBV"))
            obv_ema = _sf(current.get("OBV_EMA"))
            p_obv = _sf(prev.get("OBV", obv))
            p_ema = _sf(prev.get("OBV_EMA", obv_ema))

            if None not in (obv, obv_ema, p_obv, p_ema):
                if p_obv <= p_ema and obv > obv_ema:  # type: ignore[operator]
                    signals.append(MutableSignal(
                        signal="OBV BULL CROSS EMA",
                        description="OBV crossed above its 20-period EMA",
                        strength=SignalStrength.BULLISH.value,
                        category=SignalCategory.OBV_CMF.value,
                    ))
                elif p_obv >= p_ema and obv < obv_ema:  # type: ignore[operator]
                    signals.append(MutableSignal(
                        signal="OBV BEAR CROSS EMA",
                        description="OBV crossed below its 20-period EMA",
                        strength=SignalStrength.BEARISH.value,
                        category=SignalCategory.OBV_CMF.value,
                    ))

        # CMF
        if "CMF" in df.columns:
            cmf = _sf(current.get("CMF"))
            prev_cmf = _sf(prev.get("CMF", cmf))

            if cmf is not None:
                if cmf > 0.1:
                    signals.append(MutableSignal(
                        signal="CMF STRONG BUYING",
                        description=f"Chaikin Money Flow: {cmf:.3f} (accumulation)",
                        strength=SignalStrength.BULLISH.value,
                        category=SignalCategory.OBV_CMF.value,
                    ))
                elif cmf < -0.1:
                    signals.append(MutableSignal(
                        signal="CMF STRONG SELLING",
                        description=f"Chaikin Money Flow: {cmf:.3f} (distribution)",
                        strength=SignalStrength.BEARISH.value,
                        category=SignalCategory.OBV_CMF.value,
                    ))

                if prev_cmf is not None:
                    if prev_cmf <= 0 < cmf:
                        signals.append(MutableSignal(
                            signal="CMF CROSSED POSITIVE",
                            description=f"CMF turned positive: {cmf:.3f}",
                            strength=SignalStrength.BULLISH.value,
                            category=SignalCategory.OBV_CMF.value,
                        ))
                    elif prev_cmf >= 0 > cmf:
                        signals.append(MutableSignal(
                            signal="CMF CROSSED NEGATIVE",
                            description=f"CMF turned negative: {cmf:.3f}",
                            strength=SignalStrength.BEARISH.value,
                            category=SignalCategory.OBV_CMF.value,
                        ))

        return signals
