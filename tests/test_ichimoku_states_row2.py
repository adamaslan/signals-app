"""Row 2: Ichimoku cloud states are features, not votes (T5)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from signals_app.detection.orchestrator import detect_all_signals
from signals_app.detection.trend import IchimokuDetector
from signals_app.indicators.compute import compute_indicators

CURRENT_CLOUD_WARMUP = 78
STATE_SIGNALS = {"PRICE ABOVE KUMO", "PRICE BELOW KUMO", "PRICE INSIDE KUMO", "BULLISH KUMO", "BEARISH KUMO"}


def _ohlcv(close: np.ndarray) -> pd.DataFrame:
    idx = pd.date_range("2022-01-03", periods=len(close), freq="B")
    return pd.DataFrame(
        {"Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close, "Volume": np.full(len(close), 1e6)},
        index=idx,
    )


def _ichimoku_hits(df: pd.DataFrame) -> list[str]:
    return [s.signal for s in detect_all_signals(df) if s.category == "ICHIMOKU"]


@pytest.mark.parametrize("slope", [1.0, -1.0])
def test_constant_trend_emits_no_ichimoku_hits_after_warmup(slope: float) -> None:
    close = 500.0 + slope * np.arange(300)
    df = compute_indicators(_ohlcv(close))
    for end in range(CURRENT_CLOUD_WARMUP + 1, len(df) + 1):
        assert _ichimoku_hits(df.iloc[:end]) == [], f"bar {end} emitted an Ichimoku hit"


def test_detector_never_emits_state_signals() -> None:
    rng = np.random.default_rng(3)
    close = 100 + rng.normal(0, 1, 400).cumsum()
    df = compute_indicators(_ohlcv(close))
    emitted = {s.signal for end in range(CURRENT_CLOUD_WARMUP, len(df) + 1) for s in IchimokuDetector().detect(df.iloc[:end])}
    assert not emitted & STATE_SIGNALS


def test_cloud_states_are_columns_with_expected_values() -> None:
    up = compute_indicators(_ohlcv(500.0 + np.arange(300)))
    down = compute_indicators(_ohlcv(500.0 - np.arange(300)))
    assert up["Ichimoku_CloudPos"].iloc[-1] == 1.0
    assert up["Ichimoku_CloudColour"].iloc[-1] == 1.0
    assert down["Ichimoku_CloudPos"].iloc[-1] == -1.0
    assert down["Ichimoku_CloudColour"].iloc[-1] == -1.0
    assert up["Ichimoku_CloudPos"].iloc[: CURRENT_CLOUD_WARMUP - 1].isna().all()


def test_cloud_state_columns_are_causal() -> None:
    rng = np.random.default_rng(5)
    close = 100 + rng.normal(0, 1, 300).cumsum()
    full = compute_indicators(_ohlcv(close))
    prefix = compute_indicators(_ohlcv(close)[:200])
    for col in ("Ichimoku_CloudPos", "Ichimoku_CloudColour"):
        pd.testing.assert_series_equal(full[col].iloc[:200], prefix[col], check_names=False)
