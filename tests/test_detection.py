"""Basic smoke tests for the detection pipeline.

Tests the L1–L4 pipeline stages without LLM or network calls.
Uses synthetic OHLCV data to verify that detectors run without error
and produce expected signal counts and types.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from signals_app.detection.base import MutableSignal, SignalList
from signals_app.detection.orchestrator import detect_all_signals, get_default_detectors
from signals_app.indicators.compute import compute_indicators
from signals_app.scoring.confluence import ConfluenceRanker, ConfluenceResult
from signals_app.schemas.signal_output import (
    Evidence,
    EvidenceItem,
    EvidenceSource,
    Signal,
    SignalDirection,
    Timeframe,
    alignment_score,
    classify_divergence,
    DivergencePattern,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_ohlcv(n: int = 300, trend: str = "up") -> pd.DataFrame:
    """Generate synthetic OHLCV data for testing.

    Args:
        n: Number of bars to generate.
        trend: "up", "down", or "flat".

    Returns:
        OHLCV DataFrame with DatetimeIndex.
    """
    dates = pd.date_range(start=date.today() - timedelta(days=n), periods=n, freq="B")

    if trend == "up":
        close = np.linspace(100, 200, n) + np.random.normal(0, 1, n)
    elif trend == "down":
        close = np.linspace(200, 100, n) + np.random.normal(0, 1, n)
    else:
        close = np.full(n, 150.0) + np.random.normal(0, 2, n)

    close = np.abs(close)
    high = close * (1 + np.abs(np.random.normal(0, 0.01, n)))
    low = close * (1 - np.abs(np.random.normal(0, 0.01, n)))
    open_ = close * (1 + np.random.normal(0, 0.005, n))
    volume = np.random.randint(1_000_000, 10_000_000, n).astype(float)

    return pd.DataFrame({
        "Open": open_,
        "High": high,
        "Low": low,
        "Close": close,
        "Volume": volume,
    }, index=dates)


@pytest.fixture
def df_up() -> pd.DataFrame:
    """Synthetic uptrending OHLCV DataFrame."""
    np.random.seed(42)
    return _make_ohlcv(300, "up")


@pytest.fixture
def df_down() -> pd.DataFrame:
    """Synthetic downtrending OHLCV DataFrame."""
    np.random.seed(42)
    return _make_ohlcv(300, "down")


@pytest.fixture
def df_small() -> pd.DataFrame:
    """Minimal 25-bar OHLCV DataFrame."""
    np.random.seed(42)
    return _make_ohlcv(25, "flat")


# ---------------------------------------------------------------------------
# Indicator computation tests
# ---------------------------------------------------------------------------


class TestComputeIndicators:
    def test_returns_dataframe(self, df_up: pd.DataFrame) -> None:
        result = compute_indicators(df_up)
        assert isinstance(result, pd.DataFrame)

    def test_has_standard_indicators(self, df_up: pd.DataFrame) -> None:
        result = compute_indicators(df_up)
        for col in ["RSI", "MACD", "MACD_Signal", "BB_Upper", "BB_Lower", "ADX", "ATR"]:
            assert col in result.columns, f"Missing column: {col}"

    def test_has_sma_columns(self, df_up: pd.DataFrame) -> None:
        result = compute_indicators(df_up)
        for period in (5, 10, 20, 50, 100, 200):
            assert f"SMA_{period}" in result.columns

    def test_has_ichimoku(self, df_up: pd.DataFrame) -> None:
        result = compute_indicators(df_up)
        for col in ["Ichimoku_Tenkan", "Ichimoku_Kijun", "Ichimoku_SpanA", "Ichimoku_SpanB"]:
            assert col in result.columns

    def test_has_obv_cmf(self, df_up: pd.DataFrame) -> None:
        result = compute_indicators(df_up)
        assert "OBV" in result.columns
        assert "CMF" in result.columns

    def test_no_inf_in_key_columns(self, df_up: pd.DataFrame) -> None:
        result = compute_indicators(df_up)
        for col in ["RSI", "MACD", "ADX"]:
            vals = result[col].dropna()
            assert not any(math.isinf(v) for v in vals), f"Inf found in {col}"

    def test_rsi_range(self, df_up: pd.DataFrame) -> None:
        result = compute_indicators(df_up)
        rsi = result["RSI"].dropna()
        assert (rsi >= 0).all() and (rsi <= 100).all(), "RSI out of [0, 100]"

    def test_missing_column_raises(self) -> None:
        bad_df = pd.DataFrame({"Close": [1, 2, 3], "Volume": [100, 200, 300]})
        with pytest.raises(ValueError, match="missing required OHLCV columns"):
            compute_indicators(bad_df)

    def test_long_sma_nan_when_history_too_short(self) -> None:
        """A period-N SMA computed on fewer than N bars must be NaN, not an
        average over whatever bars happen to exist.

        Regression for the 2026-09-01 universe scan: with 63 bars, SMA_100
        and SMA_200 both silently collapsed to the 63-bar mean (min_periods=1),
        so ">10% ABOVE 100SMA" and ">10% ABOVE 200SMA" fired with the exact
        same reported distance — a 200-day average that never existed drove
        published SELL calls (A, ABT) past the confluence gate.
        """
        np.random.seed(42)
        short_df = _make_ohlcv(63, "up")
        result = compute_indicators(short_df)

        assert result["SMA_200"].isna().all(), "SMA_200 must be NaN over 63 bars"
        assert result["SMA_100"].isna().all(), "SMA_100 must be NaN over 63 bars"
        # A period the window *can* satisfy should still compute normally.
        assert result["SMA_50"].notna().any()

    def test_short_and_long_sma_are_not_numerically_identical(self) -> None:
        """SMA_100 and SMA_200 must diverge once both have partial warmup —
        guards against any reintroduction of a shared min_periods=1 collapse."""
        np.random.seed(42)
        df = _make_ohlcv(250, "up")
        result = compute_indicators(df)

        sma_100 = result["SMA_100"].iloc[-1]
        sma_200 = result["SMA_200"].iloc[-1]
        assert sma_100 != pytest.approx(sma_200)


# ---------------------------------------------------------------------------
# Detection tests
# ---------------------------------------------------------------------------


class TestDetection:
    def test_detect_all_signals_returns_signal_list(self, df_up: pd.DataFrame) -> None:
        df = compute_indicators(df_up)
        result = detect_all_signals(df)
        assert isinstance(result, SignalList)

    def test_signal_list_has_metadata(self, df_up: pd.DataFrame) -> None:
        df = compute_indicators(df_up)
        result = detect_all_signals(df)
        assert hasattr(result, "degraded")
        assert hasattr(result, "warnings")
        assert isinstance(result.warnings, list)

    def test_signals_are_mutable_signals(self, df_up: pd.DataFrame) -> None:
        df = compute_indicators(df_up)
        result = detect_all_signals(df)
        for sig in result:
            assert isinstance(sig, MutableSignal)
            assert sig.signal
            assert sig.strength
            assert sig.category

    def test_uptrend_produces_bullish_signals(self, df_up: pd.DataFrame) -> None:
        df = compute_indicators(df_up)
        result = detect_all_signals(df)
        bull = [s for s in result if "BULL" in s.strength or "BULLISH" in s.strength]
        assert len(bull) > 0, "Expected at least one bullish signal in uptrend data"

    def test_small_df_does_not_crash(self, df_small: pd.DataFrame) -> None:
        df = compute_indicators(df_small)
        result = detect_all_signals(df)
        assert isinstance(result, SignalList)

    def test_no_100_or_200_ma_signals_on_63_bars(self) -> None:
        """End-to-end regression: 63 bars (a "3mo" scan) must never produce a
        100SMA or 200SMA signal — those indicators are NaN over that window
        and MADistanceExpandedDetector/MovingAverageSignalDetector already
        skip NaN inputs via _sf(); this pins that neither detector regresses
        to emitting a signal keyed to an SMA that was never actually computed."""
        np.random.seed(42)
        short_df = _make_ohlcv(63, "up")
        df = compute_indicators(short_df)
        result = detect_all_signals(df)

        offending = [s for s in result if "100SMA" in s.signal or "200SMA" in s.signal
                     or "SMA_100" in s.signal or "SMA_200" in s.signal]
        assert offending == [], f"Fabricated long-MA signals on short history: {offending}"

    def test_default_detectors_count(self) -> None:
        detectors = get_default_detectors()
        assert len(detectors) == 18, f"Expected 18 detectors, got {len(detectors)}"

    def test_not_degraded_on_good_data(self, df_up: pd.DataFrame) -> None:
        df = compute_indicators(df_up)
        result = detect_all_signals(df)
        # With good data, should not be degraded
        assert not result.degraded, f"Unexpected degraded=True: {result.warnings}"


# ---------------------------------------------------------------------------
# Confluence scoring tests
# ---------------------------------------------------------------------------


class TestConfluenceRanker:
    def test_empty_signals_returns_hold(self) -> None:
        ranker = ConfluenceRanker()
        result = ranker.rank_signals([])
        assert result.action == "HOLD"
        assert result.score == 0.0

    def test_all_bullish_returns_buy(self) -> None:
        from signals_app.config import SignalCategory, SignalStrength
        signals = [
            MutableSignal(
                signal=f"TEST_BULL_{i}",
                description="test",
                strength=SignalStrength.STRONG_BULLISH.value,
                category=SignalCategory.RSI.value,
            )
            for i in range(5)
        ]
        ranker = ConfluenceRanker()
        result = ranker.rank_signals(signals)
        assert result.action == "BUY"
        assert result.score > 0

    def test_all_bearish_returns_sell(self) -> None:
        from signals_app.config import SignalCategory, SignalStrength
        signals = [
            MutableSignal(
                signal=f"TEST_BEAR_{i}",
                description="test",
                strength=SignalStrength.STRONG_BEARISH.value,
                category=SignalCategory.RSI.value,
            )
            for i in range(5)
        ]
        ranker = ConfluenceRanker()
        result = ranker.rank_signals(signals)
        assert result.action == "SELL"
        assert result.score < 0

    def test_score_in_range(self, df_up: pd.DataFrame) -> None:
        df = compute_indicators(df_up)
        signal_list = detect_all_signals(df)
        ranker = ConfluenceRanker()
        result = ranker.rank_signals(list(signal_list))
        assert -1.0 <= result.score <= 1.0


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestSignalSchema:
    def _make_evidence(self, confidence: float) -> Evidence:
        """Make valid evidence for a given confidence level."""
        items = [
            EvidenceItem(
                source=EvidenceSource.technical,
                weight=1.0,
                summary="Test evidence",
                is_counter=False,
            )
        ]
        if confidence > 0.6:
            items.append(EvidenceItem(
                source=EvidenceSource.rule_based,
                weight=0.0,
                summary="Counter argument",
                is_counter=True,
            ))
        return Evidence(items=items)

    def test_valid_signal(self) -> None:
        evidence = self._make_evidence(0.55)
        signal = Signal(
            direction=SignalDirection.buy,
            confidence=0.55,
            timeframe=Timeframe.one_day,
            evidence=evidence,
        )
        assert signal.direction == SignalDirection.buy
        assert signal.confidence == 0.55

    def test_confidence_zero_raises(self) -> None:
        evidence = self._make_evidence(0.3)
        with pytest.raises(Exception):
            Signal(
                direction=SignalDirection.hold,
                confidence=0.0,
                timeframe=Timeframe.one_day,
                evidence=evidence,
            )

    def test_confidence_one_raises(self) -> None:
        evidence = self._make_evidence(0.9)
        with pytest.raises(Exception):
            Signal(
                direction=SignalDirection.buy,
                confidence=1.0,
                timeframe=Timeframe.one_day,
                evidence=evidence,
            )

    def test_hold_confidence_cap(self) -> None:
        evidence = self._make_evidence(0.55)
        with pytest.raises(Exception):
            Signal(
                direction=SignalDirection.hold,
                confidence=0.80,
                timeframe=Timeframe.one_day,
                evidence=evidence,
            )

    def test_high_confidence_requires_counter(self) -> None:
        items = [
            EvidenceItem(
                source=EvidenceSource.technical,
                weight=1.0,
                summary="Only bullish",
                is_counter=False,
            )
        ]
        evidence = Evidence(items=items)
        with pytest.raises(Exception, match="counter_argument"):
            Signal(
                direction=SignalDirection.strong_buy,
                confidence=0.75,
                timeframe=Timeframe.one_day,
                evidence=evidence,
            )

    def test_evidence_weights_must_sum_to_one(self) -> None:
        items = [
            EvidenceItem(source=EvidenceSource.technical, weight=0.3, summary="a", is_counter=False),
            EvidenceItem(source=EvidenceSource.macro, weight=0.3, summary="b", is_counter=False),
        ]
        with pytest.raises(Exception, match="sum to 1.0"):
            Evidence(items=items)

    def test_alignment_score_all_same(self) -> None:
        evidence = self._make_evidence(0.55)
        signals = [
            Signal(
                direction=SignalDirection.buy,
                confidence=0.55,
                timeframe=Timeframe.one_day,
                evidence=evidence,
            )
            for _ in range(3)
        ]
        score = alignment_score(signals)
        assert score == 1.0

    def test_alignment_score_empty(self) -> None:
        assert alignment_score([]) == 0.0

    def test_classify_divergence_aligned_bullish(self) -> None:
        def _bull_signal(tf: Timeframe) -> Signal:
            evidence = self._make_evidence(0.55)
            return Signal(
                direction=SignalDirection.buy,
                confidence=0.55,
                timeframe=tf,
                evidence=evidence,
            )

        signals = {
            "1D": _bull_signal(Timeframe.one_day),
            "5D": _bull_signal(Timeframe.five_day),
            "1M": _bull_signal(Timeframe.one_month),
        }
        pattern = classify_divergence(signals)
        assert pattern == DivergencePattern.aligned_bullish
