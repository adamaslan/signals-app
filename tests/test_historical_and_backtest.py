"""Smoke tests for historical bar-by-bar scanning and the backtest engine.

Uses the same synthetic OHLCV generation approach as test_detection.py.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from backtests.engine import HitRateBucket, score_historical_signals
from signals_app.config import MIN_HISTORICAL_LOOKBACK, SignalCategory, SignalStrength
from signals_app.detection.base import MutableSignal
from signals_app.detection.historical import BarSignals, scan_historical
from signals_app.indicators.compute import compute_indicators
from signals_app.scoring.confluence import ConfluenceRanker


def _make_ohlcv(n: int = 260, trend: str = "up") -> pd.DataFrame:
    dates = pd.date_range(start=date.today() - timedelta(days=n), periods=n, freq="B")
    if trend == "up":
        close = np.linspace(100, 200, n) + np.random.normal(0, 1, n)
    else:
        close = np.full(n, 150.0) + np.random.normal(0, 2, n)
    close = np.abs(close)
    high = close * 1.01
    low = close * 0.99
    open_ = close
    volume = np.random.randint(100_000, 1_000_000, n)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=dates,
    )


def test_scan_historical_skips_warmup_and_returns_one_entry_per_bar():
    df = compute_indicators(_make_ohlcv(n=260))
    bars = scan_historical(df, min_lookback=MIN_HISTORICAL_LOOKBACK)

    assert len(bars) == len(df) - MIN_HISTORICAL_LOOKBACK
    assert all(isinstance(b, BarSignals) for b in bars)
    # Dates must be strictly increasing and match the tail of the source df.
    assert [b.date for b in bars] == list(df.index[MIN_HISTORICAL_LOOKBACK:])


def test_scan_historical_below_min_lookback_returns_empty():
    df = compute_indicators(_make_ohlcv(n=50))
    assert scan_historical(df, min_lookback=MIN_HISTORICAL_LOOKBACK) == []


def test_score_historical_signals_produces_bounded_hit_rates():
    df = compute_indicators(_make_ohlcv(n=260))
    bars = scan_historical(df, min_lookback=MIN_HISTORICAL_LOOKBACK)

    result = score_historical_signals(df, bars, horizon_days=5)

    assert set(result.keys()) == {"by_category", "by_strength"}
    for bucket_list in result.values():
        for bucket in bucket_list:
            assert isinstance(bucket, HitRateBucket)
            assert bucket.total > 0
            assert 0.0 <= bucket.hit_rate <= 1.0


def test_score_historical_signals_skips_unresolved_tail():
    df = compute_indicators(_make_ohlcv(n=260))
    bars = scan_historical(df, min_lookback=MIN_HISTORICAL_LOOKBACK)
    horizon = 5

    # Only the unresolved tail bars (within `horizon` of the end of df) —
    # none of these have a known forward return, so nothing should be scored.
    tail_bars = bars[-horizon:]
    result = score_historical_signals(df, tail_bars, horizon_days=horizon)

    assert result["by_category"] == []
    assert result["by_strength"] == []


# ---------------------------------------------------------------------------
# Confidence calibration — ConfluenceRanker.rank_signals(strength_hit_rates=...)
# ---------------------------------------------------------------------------


def _bullish_signals(n: int = 5) -> list[MutableSignal]:
    return [
        MutableSignal(
            signal=f"TEST_BULL_{i}",
            description="test",
            strength=SignalStrength.STRONG_BULLISH.value,
            category=SignalCategory.RSI.value,
        )
        for i in range(n)
    ]


def test_rank_signals_without_hit_rates_is_unchanged():
    """Default None hit-rates must reproduce the plain score-threshold label."""
    ranker = ConfluenceRanker()
    signals = _bullish_signals()

    baseline = ranker.rank_signals(signals)
    explicit_none = ranker.rank_signals(signals, strength_hit_rates=None)

    assert explicit_none.confidence_label == baseline.confidence_label
    assert explicit_none.score == baseline.score
    assert explicit_none.action == baseline.action


def test_rank_signals_high_hit_rate_pushes_confidence_to_high():
    ranker = ConfluenceRanker()
    signals = _bullish_signals()

    result = ranker.rank_signals(
        signals,
        strength_hit_rates={SignalStrength.STRONG_BULLISH.value: 0.75},
    )

    assert result.bias == "bullish"
    assert result.confidence_label == "HIGH"


def test_rank_signals_low_hit_rate_pushes_confidence_to_low():
    ranker = ConfluenceRanker()
    signals = _bullish_signals()

    result = ranker.rank_signals(
        signals,
        strength_hit_rates={SignalStrength.STRONG_BULLISH.value: 0.40},
    )

    assert result.bias == "bullish"
    assert result.confidence_label == "LOW"
