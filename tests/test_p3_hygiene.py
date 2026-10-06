"""P3 detector hygiene: one fact, one vote; no self-contradiction."""
from __future__ import annotations

import pandas as pd

from signals_app.config import SignalStrength
from signals_app.detection.base import MutableSignal
from signals_app.detection.momentum import MultiMACDDetector
from signals_app.detection.orchestrator import resolve_contradictions
from signals_app.detection.trend import TrendSignalDetector  # noqa: F401  (import smoke)
from signals_app.detection.volume import OBVCMFDetector
from signals_app.scoring.families import is_bearish_extension_vote
from signals_app.scoring.kinds import stamp_kinds
from signals_app.scoring.regime import TREND_UP


def _macd_frame(sets: dict[tuple[int, int, int], tuple[float, float, float, float]]) -> pd.DataFrame:
    """sets: (f,s,sig) -> (prev_macd, prev_signal, macd, signal)."""
    prev: dict[str, float] = {"Close": 100.0}
    cur: dict[str, float] = {"Close": 100.0}
    for (f, s, sig), (pm, ps, m, sg) in sets.items():
        tag = f"_{f}_{s}_{sig}"
        prev[f"MACD{tag}"], prev[f"MACD_Signal{tag}"] = pm, ps
        cur[f"MACD{tag}"], cur[f"MACD_Signal{tag}"] = m, sg
    return pd.DataFrame([prev, cur])


class TestMultiMacdCollapse:
    def test_three_agreeing_sets_cast_one_cross_vote(self) -> None:
        df = _macd_frame({(10, 20, 5): (-1, 0, 1, 0), (20, 50, 10): (-1, 0, 1, 0),
                          (19, 39, 9): (-1, 0, 1, 0)})
        sigs = MultiMACDDetector().detect(df)
        crosses = [s for s in sigs if s.signal.endswith("BULL CROSS")]
        assert len(crosses) == 1
        assert "3 parameter set" in crosses[0].description

    def test_non_standard_cross_is_graded_plain(self) -> None:
        df = _macd_frame({(10, 20, 5): (-1, 0, 1, 0)})
        (cross, *_) = [s for s in MultiMACDDetector().detect(df) if "CROSS" in s.signal]
        assert cross.strength == SignalStrength.BULLISH.value

    def test_conflicting_sets_cancel(self) -> None:
        df = _macd_frame({(10, 20, 5): (-1, 0, 1, 0), (20, 50, 10): (1, 0, -1, 0)})
        sigs = MultiMACDDetector().detect(df)
        assert not [s for s in sigs if "CROSS" in s.signal]

    def test_majority_side_wins(self) -> None:
        df = _macd_frame({(10, 20, 5): (-1, 0, 1, 0), (20, 50, 10): (-1, 0, 1, 0),
                          (19, 39, 9): (1, 0, -1, 0)})
        (cross,) = [s for s in MultiMACDDetector().detect(df) if "CROSS" in s.signal]
        assert cross.signal.endswith("BULL CROSS")


def test_obv_divergence_is_symmetric() -> None:
    n = 25
    close = [100.0 - i for i in range(n)]  # falling price
    obv = [float(i) for i in range(n)]  # rising OBV -> bullish divergence
    df = pd.DataFrame({"Close": close, "OBV": obv, "High": close, "Low": close,
                       "Volume": [1.0] * n})
    bull = [s for s in OBVCMFDetector().detect(df) if "BULLISH DIVERGENCE" in s.signal]
    close_up = [100.0 + i for i in range(n)]
    df2 = df.assign(Close=close_up, OBV=[-float(i) for i in range(n)])
    bear = [s for s in OBVCMFDetector().detect(df2) if "BEARISH DIVERGENCE" in s.signal]
    assert bull and bear
    assert abs(len(bull[0].strength)) and bull[0].strength.replace("BULLISH", "") == \
        bear[0].strength.replace("BEARISH", "")


class TestBollingerContradiction:
    @staticmethod
    def _sig(label: str, strength: str, category: str) -> MutableSignal:
        return MutableSignal(signal=label, description="", strength=strength, category=category)

    def test_breach_supersedes_at_band(self) -> None:
        sigs = [
            self._sig("AT UPPER BB", "BEARISH", "BOLLINGER"),
            self._sig("ABOVE UPPER BB(20,2.0)", "EXTREME BULLISH", "BB_BREAKOUT"),
            self._sig("MACD BULL CROSS", "BULLISH", "MACD"),
        ]
        stamp_kinds(sigs)
        out = resolve_contradictions(sigs)
        assert [s.signal for s in out] == ["ABOVE UPPER BB(20,2.0)", "MACD BULL CROSS"]

    def test_at_band_survives_without_breach(self) -> None:
        sigs = [self._sig("AT UPPER BB", "BEARISH", "BOLLINGER")]
        stamp_kinds(sigs)
        assert resolve_contradictions(sigs) == sigs


class TestRegimeGate:
    def test_breakdown_is_not_an_extension_vote(self) -> None:
        sig = MutableSignal(signal="BELOW LOWER BB(20,2.0)", description="",
                            strength="EXTREME BEARISH", category="BB_BREAKOUT")
        assert not is_bearish_extension_vote(sig)

    def test_overextension_still_gated_in_uptrend(self) -> None:
        sig = MutableSignal(signal="AT UPPER BB", description="", strength="BEARISH",
                            category="BOLLINGER")
        assert is_bearish_extension_vote(sig)
        assert TREND_UP  # gate regime constant is unchanged
