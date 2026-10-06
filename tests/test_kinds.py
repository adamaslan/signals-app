"""P1 taxonomy: every emitted label has a kind and a concept; scoring is unchanged."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from signals_app.config import KIND_MULTIPLIER, SignalKind
from signals_app.detection.base import MutableSignal
from signals_app.detection.fibonacci import FibonacciDetector
from signals_app.detection.orchestrator import detect_all_signals, get_default_detectors
from signals_app.indicators.compute import compute_indicators
from signals_app.scoring.confluence import ConfluenceRanker, FamilyConfluenceRanker
from signals_app.scoring.kinds import concept_of, kind_of, stamp_kinds

_VALID_KINDS = {k.value for k in SignalKind}


def _random_walk_ohlcv(seed: int, n: int = 420) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    drift = rng.normal(0.0004, 0.0003)
    vol = rng.uniform(0.008, 0.03)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    spread = np.abs(rng.normal(0, vol / 2, n))
    open_ = close * (1 + rng.normal(0, vol / 3, n))
    volume = rng.integers(1_000_000, 8_000_000, n).astype(float)
    spikes = rng.random(n) < 0.04
    volume[spikes] *= rng.uniform(1.8, 4.0, spikes.sum())
    return pd.DataFrame(
        {
            "Open": open_,
            "High": np.maximum(close, open_) * (1 + spread),
            "Low": np.minimum(close, open_) * (1 - spread),
            "Close": close,
            "Volume": volume,
        },
        index=pd.date_range("2024-01-01", periods=n, freq="B"),
    )


def _all_detectors() -> list:
    detectors = [d for d in get_default_detectors() if not isinstance(d, FibonacciDetector)]
    return [*detectors, FibonacciDetector(experimental=True)]


def test_every_emitted_label_is_classified() -> None:
    """Fuzz many series and last-bar positions; an unclassified label fails CI."""
    detectors = _all_detectors()
    seen: set[str] = set()
    unclassified: set[str] = set()
    for seed in range(12):
        full = compute_indicators(_random_walk_ohlcv(seed))
        for end in range(210, len(full) + 1, 3):
            for sig in detect_all_signals(full.iloc[:end], detectors):
                seen.add(sig.signal)
                if sig.kind not in _VALID_KINDS or not sig.concept:
                    unclassified.add(sig.signal)
    assert len(seen) >= 25, f"fuzz too thin to be meaningful: {sorted(seen)}"
    assert not unclassified, f"unclassified labels: {sorted(unclassified)}"


@pytest.mark.parametrize(
    ("label", "kind", "concept"),
    [
        ("GOLDEN CROSS", "X", "ma_cross"),
        ("10/50 MA BULL CROSS", "X", "ma_cross"),
        ("MA ALIGNMENT BULLISH", "S", "ma_stack"),
        ("PRICE ABOVE KUMO", "S", "ichimoku_cloud_pos"),
        ("ICHIMOKU TK BULL CROSS", "X", "ichimoku_tk"),
        ("AT UPPER BB", "P", "bb_edge"),
        ("ABOVE UPPER BB(20,2.0)", "T", "bb_breach"),
        ("BB(20,2.0) RIDING UPPER BAND", "S", "bb_ride"),
        (">10% ABOVE 50SMA", "P", "ma_distance"),
        ("WITHIN 1% OF 52b HIGH", "P", "range_edge"),
        ("RSI14 OVERSOLD (<30)", "S", "rsi_zone"),
        ("RSI14 CROSSED 50 BULL", "X", "rsi_mid_cross"),
        ("MACD(5,35,5) BULL CROSS", "X", "macd_cross"),
        ("MACD(5,35,5) ZERO BEAR", "X", "macd_zero"),
        ("STOCH BULL CROSS (OVERSOLD)", "X", "stoch_cross"),
        ("VOLUME SPIKE >2x (MA20)", "C", "vol_spike"),
        ("OBV BULLISH DIVERGENCE", "S", "obv_divergence"),
        ("CMF CROSSED POSITIVE", "X", "cmf_cross"),
        ("FIB GOLDEN POCKET HOLD", "X", "fib_hold"),
        ("FIB 1.618 TARGET", "T", "fib_target"),
    ],
)
def test_known_labels(label: str, kind: str, concept: str) -> None:
    sig = MutableSignal(signal=label, description="", strength="BULLISH", category="TREND")
    assert kind_of(sig) == kind
    assert concept_of(sig) == concept


def test_unknown_label_is_none_not_guessed() -> None:
    sig = MutableSignal(signal="SOMETHING NEW", description="", strength="BULLISH", category="TREND")
    assert kind_of(sig) is None
    assert concept_of(sig) is None


def test_explicit_field_wins_over_table() -> None:
    sig = MutableSignal(
        signal="GOLDEN CROSS", description="", strength="BULLISH", category="MA_CROSS", kind="S"
    )
    assert kind_of(sig) == "S"


def test_multipliers_cover_every_kind() -> None:
    assert set(KIND_MULTIPLIER) == _VALID_KINDS


def test_stamping_does_not_change_existing_rankers() -> None:
    full = compute_indicators(_random_walk_ohlcv(3))
    detectors = _all_detectors()
    for end in range(210, len(full) + 1, 20):
        signals = list(detect_all_signals(full.iloc[:end], detectors))
        unstamped = [s.model_copy(update={"kind": None, "concept": None}) for s in signals]
        assert ConfluenceRanker().rank_signals(signals) == ConfluenceRanker().rank_signals(unstamped)
        assert FamilyConfluenceRanker().rank_signals(signals) == FamilyConfluenceRanker().rank_signals(unstamped)


def test_stamp_kinds_is_idempotent() -> None:
    sig = MutableSignal(signal="MACD BULL CROSS", description="", strength="BULLISH", category="MACD")
    stamp_kinds([sig])
    first = (sig.kind, sig.concept)
    stamp_kinds([sig])
    assert (sig.kind, sig.concept) == first == ("X", "macd_cross")


def test_experimental_labels_are_classified() -> None:
    """Every label from the experimental detectors resolves to a kind and concept."""
    from signals_app.detection.orchestrator import get_experimental_detectors

    unclassified: set[str] = set()
    seen: set[str] = set()
    for seed in range(20):
        full = compute_indicators(_random_walk_ohlcv(seed, n=500))
        for end in range(210, len(full) + 1, 3):
            for sig in detect_all_signals(full.iloc[:end], get_experimental_detectors()):
                seen.add(sig.signal)
                if sig.kind not in _VALID_KINDS or not sig.concept:
                    unclassified.add(sig.signal)
    assert len(seen) >= 8, sorted(seen)
    assert not unclassified, sorted(unclassified)
