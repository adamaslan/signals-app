"""Row 1 (correctness) tests for MA / Ichimoku crosses.

Covers harness/FIB-ICHIMOKU-MA.md defects D1 (50/200 emitted twice), D3 (TK cross
over-graded), D4 (tie re-fires a cross) and D5/D6 (leading cloud, causal Chikou).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from signals_app.config import SignalStrength
from signals_app.detection.crosses import cross_at_last_bar, sign_cross
from signals_app.detection.orchestrator import detect_all_signals
from signals_app.detection.trend import (
    ExpandedMACrossDetector,
    IchimokuDetector,
    MovingAverageSignalDetector,
)
from signals_app.indicators.compute import compute_indicators

DISPLACEMENT = 26
CURRENT_CLOUD_WARMUP = 78  # 52 (LeadB) + 26 (displacement)


def _s(values: list[float]) -> pd.Series:
    return pd.Series(values, dtype=float)


def _ohlcv(close: np.ndarray) -> pd.DataFrame:
    idx = pd.date_range("2022-01-03", periods=len(close), freq="B")
    return pd.DataFrame(
        {
            "Open": close,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": np.full(len(close), 1_000_000.0),
        },
        index=idx,
    )


def _v_shaped_close(n_down: int = 150, n_up: int = 200, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    down = np.linspace(200, 100, n_down)
    up = np.linspace(100, 260, n_up)
    return np.concatenate([down, up]) + rng.normal(0, 0.3, n_down + n_up)


# ---------------------------------------------------------------------------
# sign_cross
# ---------------------------------------------------------------------------


class TestSignCross:
    def test_simple_cross_up_and_down(self) -> None:
        fast = _s([1, 1, 3, 3, 1])
        slow = _s([2, 2, 2, 2, 2])
        assert sign_cross(fast, slow).tolist() == [0, 0, 1, 0, -1]

    def test_tie_between_same_sides_does_not_fire(self) -> None:
        """+, tie, +: the old `prev <= slow and now > slow` re-fired here (D4)."""
        fast = _s([3, 2, 3])
        slow = _s([2, 2, 2])
        assert sign_cross(fast, slow).tolist() == [0, 0, 0]

    def test_cross_through_a_tie_fires_once_on_exit(self) -> None:
        fast = _s([1, 2, 3, 3])
        slow = _s([2, 2, 2, 2])
        assert sign_cross(fast, slow).tolist() == [0, 0, 1, 0]

    def test_nan_inputs_never_fire(self) -> None:
        fast = _s([np.nan, np.nan, 3, 1, 3])
        slow = _s([2, 2, 2, 2, 2])
        assert sign_cross(fast, slow).tolist() == [0, 0, 0, -1, 1]

    def test_nan_gap_does_not_bridge_a_cross(self) -> None:
        fast = _s([1, np.nan, 3])
        slow = _s([2, 2, 2])
        assert sign_cross(fast, slow).tolist() == [0, 0, 0]

    def test_is_causal_slice_equality(self) -> None:
        rng = np.random.default_rng(1)
        fast = pd.Series(np.cumsum(rng.normal(size=300)))
        slow = pd.Series(np.cumsum(rng.normal(size=300)))
        full = sign_cross(fast, slow)
        for cut in (50, 120, 299):
            prefix = sign_cross(fast.iloc[:cut], slow.iloc[:cut])
            pd.testing.assert_series_equal(full.iloc[:cut], prefix)

    def test_cross_at_last_bar_missing_column_is_no_cross(self) -> None:
        df = pd.DataFrame({"A": [1.0, 2.0]})
        assert cross_at_last_bar(df, "A", "B") == 0


# ---------------------------------------------------------------------------
# D1: 50/200 cross emitted once
# ---------------------------------------------------------------------------


class TestSingleFiftyTwoHundredEmission:
    @pytest.fixture()
    def indicator_df(self) -> pd.DataFrame:
        return compute_indicators(_ohlcv(_v_shaped_close()))

    def _golden_bar(self, df: pd.DataFrame) -> int:
        events = sign_cross(df["SMA_50"], df["SMA_200"])
        bars = np.flatnonzero(events.to_numpy() == 1)
        assert len(bars) >= 1, "fixture must contain a 50/200 golden cross"
        return int(bars[0])

    def test_pipeline_emits_golden_cross_exactly_once(self, indicator_df: pd.DataFrame) -> None:
        bar = self._golden_bar(indicator_df)
        result = detect_all_signals(indicator_df.iloc[: bar + 1])
        golden = [s for s in result if s.signal == "GOLDEN CROSS"]
        assert len(golden) == 1

    def test_only_expanded_detector_emits_it(self, indicator_df: pd.DataFrame) -> None:
        bar = self._golden_bar(indicator_df)
        window = indicator_df.iloc[: bar + 1]
        simple = MovingAverageSignalDetector().detect(window)
        expanded = ExpandedMACrossDetector().detect(window)
        assert [s for s in simple if s.signal in ("GOLDEN CROSS", "DEATH CROSS")] == []
        assert [s.signal for s in expanded if s.signal == "GOLDEN CROSS"] == ["GOLDEN CROSS"]

    def test_death_cross_emitted_once(self) -> None:
        rng = np.random.default_rng(11)
        close = np.concatenate([np.linspace(100, 200, 150), np.linspace(200, 80, 250)])
        close = close + rng.normal(0, 0.3, len(close))  # up then down -> death cross
        df = compute_indicators(_ohlcv(close))
        events = sign_cross(df["SMA_50"], df["SMA_200"]).to_numpy()
        bars = np.flatnonzero(events == -1)
        assert len(bars) >= 1
        result = detect_all_signals(df.iloc[: int(bars[0]) + 1])
        assert len([s for s in result if s.signal == "DEATH CROSS"]) == 1

    def test_no_cross_on_ordinary_bar(self, indicator_df: pd.DataFrame) -> None:
        result = detect_all_signals(indicator_df.iloc[:260])
        assert [s for s in result if s.signal in ("GOLDEN CROSS", "DEATH CROSS")] == []


# ---------------------------------------------------------------------------
# D3 / D4: Ichimoku TK cross
# ---------------------------------------------------------------------------


def _ichimoku_frame(tenkan: list[float], kijun: list[float]) -> pd.DataFrame:
    n = len(tenkan)
    return pd.DataFrame(
        {
            "Close": np.full(n, 100.0),
            "Ichimoku_Tenkan": tenkan,
            "Ichimoku_Kijun": kijun,
            "Ichimoku_SpanA": np.full(n, 95.0),
            "Ichimoku_SpanB": np.full(n, 90.0),
        }
    )


def _tk(df: pd.DataFrame) -> list:
    return [s for s in IchimokuDetector().detect(df) if "TK" in s.signal]


class TestTkCross:
    def test_bull_cross_is_graded_bullish_not_strong(self) -> None:
        hits = _tk(_ichimoku_frame([9, 9, 11], [10, 10, 10]))
        assert [(s.signal, s.strength) for s in hits] == [
            ("ICHIMOKU TK BULL CROSS", SignalStrength.BULLISH.value)
        ]

    def test_bear_cross_is_graded_bearish_not_strong(self) -> None:
        hits = _tk(_ichimoku_frame([11, 11, 9], [10, 10, 10]))
        assert [(s.signal, s.strength) for s in hits] == [
            ("ICHIMOKU TK BEAR CROSS", SignalStrength.BEARISH.value)
        ]

    def test_tie_then_same_side_does_not_refire(self) -> None:
        # Tenkan above, touches Kijun, leaves upward again: no new cross (D4).
        assert _tk(_ichimoku_frame([11, 10, 11], [10, 10, 10])) == []

    def test_cross_through_tie_fires_on_exit(self) -> None:
        hits = _tk(_ichimoku_frame([9, 10, 11], [10, 10, 10]))
        assert [s.signal for s in hits] == ["ICHIMOKU TK BULL CROSS"]

    def test_no_cross_when_tenkan_stays_on_one_side(self) -> None:
        assert _tk(_ichimoku_frame([11, 12, 13], [10, 10, 10])) == []


# ---------------------------------------------------------------------------
# D5 / D6: new columns, causal
# ---------------------------------------------------------------------------


class TestIchimokuColumns:
    @pytest.fixture()
    def df(self) -> pd.DataFrame:
        return compute_indicators(_ohlcv(_v_shaped_close()))

    def test_new_columns_exist(self, df: pd.DataFrame) -> None:
        for col in ("Ichimoku_LeadA", "Ichimoku_LeadB", "Chikou_Diff"):
            assert col in df.columns

    def test_displaced_cloud_is_lead_shifted_by_26(self, df: pd.DataFrame) -> None:
        pd.testing.assert_series_equal(
            df["Ichimoku_SpanA"], df["Ichimoku_LeadA"].shift(DISPLACEMENT), check_names=False
        )
        pd.testing.assert_series_equal(
            df["Ichimoku_SpanB"], df["Ichimoku_LeadB"].shift(DISPLACEMENT), check_names=False
        )

    def test_lead_is_unshifted_midpoint_formula(self, df: pd.DataFrame) -> None:
        expected_a = (df["Ichimoku_Tenkan"] + df["Ichimoku_Kijun"]) / 2.0
        pd.testing.assert_series_equal(df["Ichimoku_LeadA"], expected_a, check_names=False)
        expected_b = (df["High"].rolling(52).max() + df["Low"].rolling(52).min()) / 2.0
        pd.testing.assert_series_equal(df["Ichimoku_LeadB"], expected_b, check_names=False)

    def test_cloud_warmup_is_78_bars(self, df: pd.DataFrame) -> None:
        assert df["Ichimoku_LeadB"].first_valid_index() == df.index[51]
        assert df["Ichimoku_SpanB"].first_valid_index() == df.index[CURRENT_CLOUD_WARMUP - 1]
        assert df["Ichimoku_SpanA"].first_valid_index() == df.index[25 + 26]

    def test_chikou_diff_is_close_minus_close_26_bars_ago(self, df: pd.DataFrame) -> None:
        expected = df["Close"] - df["Close"].shift(DISPLACEMENT)
        pd.testing.assert_series_equal(df["Chikou_Diff"], expected, check_names=False)
        assert df["Chikou_Diff"].iloc[:DISPLACEMENT].isna().all()

    @pytest.mark.parametrize("cut", [60, 100, 250])
    def test_new_columns_are_causal(self, df: pd.DataFrame, cut: int) -> None:
        """Slice-equality: recomputing on a prefix gives identical values, so no
        bar's value depends on a later bar (no look-ahead)."""
        prefix = compute_indicators(_ohlcv(_v_shaped_close())[:cut])
        for col in ("Ichimoku_LeadA", "Ichimoku_LeadB", "Chikou_Diff"):
            pd.testing.assert_series_equal(df[col].iloc[:cut], prefix[col], check_names=False)
