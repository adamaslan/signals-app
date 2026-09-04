"""Tests for the support/resistance detector and its pivot helpers.

`indicators/pivots.py` shipped in an earlier PR but was unused until
`detection/support_resistance.py` landed (docs/TODO.md P3 #9a) — this covers
both the low-level pivot math and the detector that consumes it.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from signals_app.config import SignalCategory, SignalStrength
from signals_app.detection.support_resistance import SupportResistanceDetector
from signals_app.indicators.pivots import get_nearest_levels, precompute_pivots


def _make_triangle_wave_ohlcv(
    cycles: int = 10, low: float = 90.0, high: float = 110.0
) -> pd.DataFrame:
    """Deterministic triangle wave — repeating, exact-value pivots.

    Each cycle rises `low -> high` then falls back to just above `low`
    (excluding the shared endpoints), so every cycle's trough is exactly
    `low` and every peak exactly `high` — no float noise to fight in
    assertions. A final bar pinned to `low` puts the latest close exactly
    on top of the prior cycles' confirmed support level.
    """
    up = np.linspace(low, high, 11)  # 11 points: low..high
    down = np.linspace(high, low, 11)[1:-1]  # interior only, avoids duplicate endpoints
    one_cycle = np.concatenate([up, down])  # rises to `high`, falls to just above `low`

    close = np.concatenate([np.tile(one_cycle, cycles), [low]])
    n = len(close)
    dates = pd.date_range(start=date.today() - timedelta(days=n), periods=n, freq="B")

    return pd.DataFrame(
        {
            "Open": close,
            "High": close + 0.05,
            "Low": close - 0.05,
            "Close": close,
            "Volume": np.full(n, 1_000_000.0),
        },
        index=dates,
    )


@pytest.fixture
def df_triangle() -> pd.DataFrame:
    return _make_triangle_wave_ohlcv()


class TestPivots:
    def test_precompute_pivots_finds_repeating_extremes(self, df_triangle: pd.DataFrame) -> None:
        levels = precompute_pivots(df_triangle, window=3)
        assert levels, "expected pivot levels on a triangle wave"
        supports = [lvl for lvl in levels if lvl.kind == "support"]
        resistances = [lvl for lvl in levels if lvl.kind == "resistance"]
        assert supports and resistances
        # Pivots read off the Low/High columns (close -/+ 0.05), not Close itself.
        assert all(abs(lvl.price - 89.95) < 1e-6 for lvl in supports)
        assert all(abs(lvl.price - 110.05) < 1e-6 for lvl in resistances)

    def test_precompute_pivots_insufficient_bars_returns_empty(self) -> None:
        tiny = _make_triangle_wave_ohlcv(cycles=1).iloc[:5]
        assert precompute_pivots(tiny, window=10) == []

    def test_get_nearest_levels_filters_by_proximity(self, df_triangle: pd.DataFrame) -> None:
        levels = precompute_pivots(df_triangle, window=3)
        supports, resistances = get_nearest_levels(90.0, levels, proximity=0.01)
        assert supports and all(lvl.kind == "support" for lvl in supports)
        assert resistances == []  # 110 is >1% away from 90


class TestSupportResistanceDetector:
    def test_detects_near_support_on_triangle_wave(self, df_triangle: pd.DataFrame) -> None:
        signals = SupportResistanceDetector().detect(df_triangle)
        assert signals, "expected NEAR SUPPORT signals — final close sits exactly on a prior trough"

        supports = [s for s in signals if s.signal.startswith("NEAR SUPPORT")]
        assert supports
        for sig in supports:
            assert sig.category == SignalCategory.SUPPORT_RESISTANCE.value
            assert sig.strength == SignalStrength.BULLISH.value

        resistances = [s for s in signals if s.signal.startswith("NEAR RESISTANCE")]
        for sig in resistances:
            assert sig.strength == SignalStrength.BEARISH.value

    def test_caps_at_max_levels_per_side_per_combo(self, df_triangle: pd.DataFrame) -> None:
        from signals_app.indicators.grids import (
            SR_MAX_LEVELS_PER_SIDE,
            SR_PIVOT_WINDOWS,
            SR_PROXIMITIES,
        )

        signals = SupportResistanceDetector().detect(df_triangle)
        for window in SR_PIVOT_WINDOWS:
            for prox in SR_PROXIMITIES:
                tag = f"(w={window}, prox={prox * 100:g}%)"
                for side in ("NEAR SUPPORT", "NEAR RESISTANCE"):
                    matching = [s for s in signals if s.signal == f"{side} {tag}"]
                    assert len(matching) <= SR_MAX_LEVELS_PER_SIDE

    def test_empty_on_too_few_bars(self) -> None:
        tiny = _make_triangle_wave_ohlcv(cycles=1).iloc[:3]
        assert SupportResistanceDetector().detect(tiny) == []

    def test_empty_without_ohlcv_columns(self) -> None:
        df = pd.DataFrame({"Close": [1.0, 2.0, 3.0]})
        assert SupportResistanceDetector().detect(df) == []
