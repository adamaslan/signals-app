"""Tests for the Fibonacci leg math and FibonacciDetector."""
from __future__ import annotations

import time

import pandas as pd
import pytest

from signals_app.config import DETECTOR_TIMEOUT_MS, SignalCategory
from signals_app.detection.fibonacci import FibonacciDetector
from signals_app.indicators.fibonacci import FibLeg, confluence_zones, recent_legs
from signals_app.indicators.pivots import PIVOT_WINDOW
from signals_app.scoring.families import FAMILY_BY_CATEGORY

ATR = 2.0


def _path() -> list[float]:
    """Down to 100 (bar 10), up to 200 (bar 30), then back down to 140 (bar 50)."""
    closes = [120 - 2 * i for i in range(11)]
    closes += [100 + 5 * i for i in range(1, 21)]
    closes += [200 - 3 * i for i in range(1, 21)]
    return closes


def _frame(closes: list[float], last: dict[str, float] | None = None,
           volume: float = 1000.0) -> pd.DataFrame:
    df = pd.DataFrame({
        "Open": closes, "High": [c + 1 for c in closes], "Low": [c - 1 for c in closes],
        "Close": closes, "ATR": ATR, "Volume": volume, "Volume_MA_20": 500.0,
    })
    if last:
        for col, value in last.items():
            df.loc[df.index[-1], col] = value
    return df


def _with_last_bar(**bar: float) -> pd.DataFrame:
    df = _frame(_path() + [140.0])
    for col, value in bar.items():
        df.loc[df.index[-1], col.capitalize()] = value
    return df


class TestLegMath:
    def test_up_leg_retracement_measured_down_from_high(self) -> None:
        leg = FibLeg(low=100.0, high=200.0, is_up=True, end_index=30)
        assert leg.retracement(0.618) == pytest.approx(138.2)
        assert leg.extension(1.618) == pytest.approx(261.8)

    def test_down_leg_retracement_measured_up_from_low(self) -> None:
        leg = FibLeg(low=100.0, high=200.0, is_up=False, end_index=30)
        assert leg.retracement(0.618) == pytest.approx(161.8)
        assert leg.extension(1.618) == pytest.approx(38.2)

    def test_recent_legs_finds_up_leg_from_confirmed_pivots(self) -> None:
        legs = recent_legs(_frame(_path()), ATR)
        assert len(legs) == 1
        assert legs[0].is_up and legs[0].low == 99.0 and legs[0].high == 201.0

    def test_small_legs_are_filtered(self) -> None:
        assert recent_legs(_frame(_path()), atr=50.0) == []

    def test_confluence_counts_independent_legs(self) -> None:
        legs = [FibLeg(100, 200, True, 30), FibLeg(90, 200, True, 60)]
        zones = confluence_zones(legs, ATR)
        assert max(n for _, n in zones) == 1  # 0.5 ATR apart, distinct prices
        near = [FibLeg(100, 200, True, 30), FibLeg(100.2, 200, True, 60)]
        assert max(n for _, n in confluence_zones(near, ATR)) == 2


class TestFibonacciDetector:
    def test_golden_pocket_hold_fires_on_reaction(self) -> None:
        signals = FibonacciDetector().detect(
            _with_last_bar(low=136.0, open=137.0, close=141.0, high=142.0))
        hold = [s for s in signals if s.signal == "FIB GOLDEN POCKET HOLD"]
        assert len(hold) == 1
        assert hold[0].category == SignalCategory.FIBONACCI.value
        assert hold[0].strength == "STRONG BULLISH"  # volume above average

    def test_hold_grade_drops_on_light_volume(self) -> None:
        df = _with_last_bar(low=136.0, open=137.0, close=141.0, high=142.0)
        df["Volume"] = 100.0
        hold = [s for s in FibonacciDetector().detect(df) if "HOLD" in s.signal]
        assert hold[0].strength == "BULLISH"

    def test_proximity_without_reversal_emits_nothing(self) -> None:
        signals = FibonacciDetector().detect(
            _with_last_bar(low=135.0, open=138.0, close=136.5, high=138.5))
        assert signals == []

    def test_break_below_0786_is_bearish(self) -> None:
        signals = FibonacciDetector().detect(
            _with_last_bar(low=118.0, open=139.0, close=120.0, high=140.0))
        breaks = [s for s in signals if s.signal == "FIB 0.786 BREAK"]
        assert [s.strength for s in breaks] == ["BEARISH"]

    def test_no_signal_far_from_levels(self) -> None:
        assert FibonacciDetector().detect(_frame(_path() + [140.0])) == []

    def test_insufficient_or_missing_columns_return_empty(self) -> None:
        assert FibonacciDetector().detect(_frame([100.0] * 10)) == []
        assert FibonacciDetector().detect(_frame(_path()).drop(columns=["ATR"])) == []

    def test_at_most_two_signals(self) -> None:
        signals = FibonacciDetector().detect(
            _with_last_bar(low=136.0, open=137.0, close=141.0, high=142.0))
        assert len(signals) <= 2


class TestPointInTime:
    def test_legs_only_use_pivots_confirmed_by_the_bar(self) -> None:
        df = _frame(_path() + [140.0, 141.0, 139.0, 142.0])
        for i in range(40, len(df)):
            for leg in recent_legs(df.iloc[: i + 1], ATR):
                assert leg.end_index + PIVOT_WINDOW <= i

    def test_output_ignores_bars_after_the_slice(self) -> None:
        full = _with_last_bar(low=136.0, open=137.0, close=141.0, high=142.0)
        future = pd.concat([full, _frame([150.0] * 10)], ignore_index=True)
        sliced = future.iloc[: len(full)]
        assert ([s.signal for s in FibonacciDetector().detect(sliced)]
                == [s.signal for s in FibonacciDetector().detect(full)])


class TestIntegration:
    def test_category_maps_to_structure_family(self) -> None:
        assert FAMILY_BY_CATEGORY[SignalCategory.FIBONACCI.value] == "structure"

    def test_detect_is_well_inside_timeout(self) -> None:
        closes = [100 + (i % 40) * 2 + (i // 40) for i in range(500)]
        df = _frame(closes)
        start = time.perf_counter()
        FibonacciDetector().detect(df)
        assert (time.perf_counter() - start) * 1000 < DETECTOR_TIMEOUT_MS / 2
