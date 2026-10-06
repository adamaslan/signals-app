"""P4 event detectors: fire on events only, never on standing conditions."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from signals_app.detection.events import (
    KumoEventDetector,
    MACDHistTurnDetector,
    PivotReactionDetector,
    RangeBreakoutDetector,
    RSIDivergenceDetector,
    RSIZoneExitDetector,
)
from signals_app.detection.fibonacci import FibonacciVariantDetector
from signals_app.detection.orchestrator import (
    detect_all_signals,
    get_default_detectors,
    get_experimental_detectors,
)
from signals_app.indicators.compute import compute_indicators
from signals_app.scoring.confluence import ConfluenceRanker, FamilyConfluenceRanker

from .test_kinds import _random_walk_ohlcv

WARMUP = 230


def _trend_ohlcv(direction: int, n: int = 420) -> pd.DataFrame:
    """Noise-free constant-rate trend: the Q4 'constant-trend series'."""
    close = 100 * np.exp(direction * 0.002 * np.arange(n))
    return pd.DataFrame(
        {"Open": close, "High": close * 1.001, "Low": close * 0.999, "Close": close,
         "Volume": np.full(n, 2_000_000.0)},
        index=pd.date_range("2024-01-01", periods=n, freq="B"),
    )


@pytest.mark.parametrize("direction", [1, -1])
def test_constant_trend_emits_no_events_after_warmup(direction: int) -> None:
    """Q4: a standing trend is a state, so no event detector may fire on it."""
    full = compute_indicators(_trend_ohlcv(direction))
    detectors = [d for d in get_experimental_detectors() if not isinstance(d, FibonacciVariantDetector)]
    for end in range(WARMUP, len(full) + 1):
        fired = detect_all_signals(full.iloc[:end], detectors)
        assert not fired, f"bar {end}: {[s.signal for s in fired]}"


def test_no_repainting_appending_a_bar_never_changes_earlier_output() -> None:
    full = compute_indicators(_random_walk_ohlcv(5))
    detectors = get_experimental_detectors()
    for end in range(WARMUP, len(full) - 1, 7):
        short = [s.signal for s in detect_all_signals(full.iloc[:end], detectors)]
        again = [s.signal for s in detect_all_signals(full.iloc[:end].copy(), detectors)]
        assert short == again


def test_extended_output_is_default_plus_experimental() -> None:
    """The experimental pass only ever adds; the default path is byte-identical."""
    assert len(get_default_detectors()) == 19
    full = compute_indicators(_random_walk_ohlcv(2))
    for end in range(WARMUP, len(full) + 1, 25):
        window = full.iloc[:end]
        default = [s.signal for s in detect_all_signals(window)]
        experimental = [s.signal for s in detect_all_signals(window, get_experimental_detectors())]
        extended = [s.signal for s in detect_all_signals(window, include_experimental=True)]
        assert sorted(extended) == sorted(default + experimental)


def test_production_rankers_see_no_experimental_signal() -> None:
    """Default output never contains an experimental label, so production scores cannot move."""
    full = compute_indicators(_random_walk_ohlcv(9))
    experimental_labels = set()
    for end in range(WARMUP, len(full) + 1, 10):
        window = full.iloc[:end]
        experimental_labels |= {s.signal for s in detect_all_signals(window, get_experimental_detectors())}
        default = list(detect_all_signals(window))
        assert not experimental_labels & {s.signal for s in default}
        ConfluenceRanker().rank_signals(default)
        FamilyConfluenceRanker().rank_signals(default)


def test_experimental_signals_are_absent_from_default_output() -> None:
    experimental_labels: set[str] = set()
    default_labels: set[str] = set()
    for seed in range(8):
        full = compute_indicators(_random_walk_ohlcv(seed))
        for end in range(WARMUP, len(full) + 1, 4):
            default_labels |= {s.signal for s in detect_all_signals(full.iloc[:end])}
            experimental_labels |= {
                s.signal for s in detect_all_signals(full.iloc[:end], get_experimental_detectors())
            }
    assert experimental_labels, "experimental detectors never fired on the fuzz set"
    assert not experimental_labels & default_labels


def test_each_new_event_fires_somewhere_in_the_fuzz() -> None:
    seen: set[str] = set()
    for seed in range(40):
        full = compute_indicators(_random_walk_ohlcv(seed, n=500))
        for end in range(WARMUP, len(full) + 1, 2):
            seen |= {s.signal.split(" ")[0] + " " + s.signal.split(" ")[1]
                     for s in detect_all_signals(full.iloc[:end], get_experimental_detectors())}
    expected_prefixes = {"KUMO BREAKOUT", "KUMO BREAKDOWN", "KUMO TWIST", "CHIKOU CROSS",
                         "RANGE BREAKOUT", "RANGE BREAKDOWN", "RSI14 EXIT", "RSI BULLISH",
                         "RSI BEARISH", "MACD HIST", "PIVOT SUPPORT", "PIVOT RESISTANCE"}
    missing = expected_prefixes - seen
    assert len(missing) <= 2, f"detectors that never fired on 40 series: {sorted(missing)}"


def test_detectors_tolerate_short_and_incomplete_frames() -> None:
    tiny = pd.DataFrame({"Close": [1.0, 2.0]})
    for detector in [KumoEventDetector(), RangeBreakoutDetector(), RSIZoneExitDetector(),
                     RSIDivergenceDetector(), MACDHistTurnDetector(), PivotReactionDetector(),
                     FibonacciVariantDetector()]:
        assert detector.detect(tiny) == []


def test_variant_labels_never_collide_with_the_default_fib_label() -> None:
    for seed in range(15):
        full = compute_indicators(_random_walk_ohlcv(seed))
        for end in range(WARMUP, len(full) + 1, 3):
            for sig in FibonacciVariantDetector().detect(full.iloc[:end]):
                assert sig.signal.endswith("(VARIANT)")


def test_each_experimental_detector_fits_the_latency_budget() -> None:
    """E1: well inside DETECTOR_TIMEOUT_MS on a 500-bar frame (checked at 40% of it)."""
    import time

    from signals_app.config import DETECTOR_TIMEOUT_MS

    full = compute_indicators(_random_walk_ohlcv(1, n=500))
    for detector in get_experimental_detectors():
        started = time.perf_counter()
        for _ in range(5):
            detector.detect(full)
        per_call_ms = (time.perf_counter() - started) * 1000 / 5
        assert per_call_ms < DETECTOR_TIMEOUT_MS * 0.4, (type(detector).__name__, per_call_ms)
