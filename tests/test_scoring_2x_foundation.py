"""Tests for the scoring-2x P-1/P0/P1 changes (bugs B1-B9, harness, excess label)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtests.engine import _benchmark_forward_return, excess_target
from backtests.evaluate import detector_report_card, evaluate_panel
from signals_app.config import SignalCategory, SignalStrength
from signals_app.detection.base import MutableSignal
from signals_app.detection.momentum import MultiMACDDetector, MultiRSIDetector
from signals_app.detection.trend import (
    BBExpansionDetector,
    HLProximityDetector,
    MADistanceExpandedDetector,
    TrendSignalDetector,
)
from signals_app.scoring.confluence import ConfluenceRanker


def _sig(strength: SignalStrength, category: SignalCategory = SignalCategory.TREND) -> MutableSignal:
    return MutableSignal(signal="x", description="", strength=strength.value, category=category.value)


class TestConfluenceScoring:
    def test_direction_less_strengths_do_not_vote(self):
        result = ConfluenceRanker().rank_signals(
            [_sig(SignalStrength.VERY_SIGNIFICANT), _sig(SignalStrength.SIGNIFICANT), _sig(SignalStrength.TRENDING)]
        )
        assert result.bull_count == 0 and result.bear_count == 0
        assert result.score == 0.0

    def test_two_agreeing_votes_do_not_saturate(self):
        result = ConfluenceRanker().rank_signals([_sig(SignalStrength.BULLISH)] * 2)
        assert 0.0 < result.score < 0.5

    def test_hit_rate_alone_does_not_promote_to_high(self):
        rates = {SignalStrength.BULLISH.value: 0.9}
        result = ConfluenceRanker().rank_signals([_sig(SignalStrength.BULLISH)] * 2, rates)
        assert result.confidence_label != "HIGH"


def _frame(**cols: float) -> pd.DataFrame:
    base = {"Close": 100.0}
    base.update(cols)
    return pd.DataFrame([base, base])


class TestDetectorFixes:
    def test_trend_signal_is_directional(self):
        df = _frame(ADX=40.0, SMA_50=110.0)
        (sig,) = TrendSignalDetector().detect(df)
        assert sig.strength == SignalStrength.BEARISH.value

    def test_hl_proximity_emits_one_signal_per_side(self):
        cols = {}
        for lb in (5, 10, 20, 50, 100):
            cols[f"High_{lb}b"] = 100.0
            cols[f"Low_{lb}b"] = 50.0
        sigs = HLProximityDetector().detect(_frame(**cols))
        highs = [s for s in sigs if "HIGH" in s.signal]
        assert len(highs) == 1 and highs[0].strength == SignalStrength.EXTREME_BULLISH.value

    def test_ma_distance_emits_one_signal_per_side(self):
        sigs = MADistanceExpandedDetector().detect(
            _frame(Dist_SMA_10=30.0, Dist_SMA_20=30.0, Dist_SMA_50=30.0, Dist_SMA_200=30.0)
        )
        assert len([s for s in sigs if "ABOVE" in s.signal]) == 1

    def test_bb_breach_is_a_single_non_contradictory_vote(self):
        cols = {}
        for period in (10, 20, 30, 50):
            for tag in ("1_5", "2_0", "2_5", "3_0"):
                cols[f"BB_{period}_{tag}_Upper"] = 90.0
                cols[f"BB_{period}_{tag}_Lower"] = 70.0
                cols[f"BB_{period}_{tag}_Pct"] = 1.5
        sigs = BBExpansionDetector().detect(_frame(**cols))
        assert len([s for s in sigs if s.signal.startswith("ABOVE UPPER")]) == 1
        assert not any("%B" in s.signal for s in sigs)

    def test_rsi_collapses_to_one_oversold_vote(self):
        sigs = MultiRSIDetector().detect(_frame(RSI=10.0, RSI_7=10.0, RSI_21=10.0))
        assert len([s for s in sigs if "OVERSOLD" in s.signal]) == 1

    def test_macd_histogram_flip_no_longer_double_counts(self):
        df = pd.DataFrame(
            [
                {"MACD_5_35_5": -1.0, "MACD_Signal_5_35_5": 0.0, "MACD_Hist_5_35_5": -1.0},
                {"MACD_5_35_5": 1.0, "MACD_Signal_5_35_5": 0.0, "MACD_Hist_5_35_5": 1.0},
            ]
        )
        sigs = MultiMACDDetector().detect(df)
        assert not any("HIST" in s.signal for s in sigs)


class TestExcessLabel:
    def test_benchmark_forward_return(self):
        idx = pd.date_range("2024-01-01", periods=5, freq="D")
        bench = pd.Series([100.0, 101, 102, 103, 110], index=idx)
        assert _benchmark_forward_return(bench, idx[0], idx[4]) == pytest.approx(0.10)

    def test_benchmark_missing_coverage_returns_none(self):
        idx = pd.date_range("2024-01-01", periods=3, freq="D")
        bench = pd.Series([100.0, 101, 102], index=idx)
        assert _benchmark_forward_return(bench, pd.Timestamp("2023-01-01"), idx[2]) is None

    def test_excess_target_scales_by_vol(self):
        assert excess_target(0.04, 0.01, 4) == pytest.approx(2.0)
        assert excess_target(0.04, 0.0, 4) is None


class TestEvaluationHarness:
    def _panel(self, informative: bool) -> pd.DataFrame:
        rng = np.random.default_rng(0)
        rows = []
        for d in pd.date_range("2024-01-01", periods=60):
            noise = rng.normal(size=40)
            score = rng.normal(size=40)
            target = (score * 0.5 + noise) if informative else noise
            rows += [{"date": d, "score": s, "fwd_excess": t} for s, t in zip(score, target)]
        return pd.DataFrame(rows)

    def test_informative_scorer_has_positive_ic_and_spread(self):
        report = evaluate_panel(self._panel(True))
        assert report.rank_ic > 0.2 and report.ic_t_stat > 5
        assert report.decile_spread > 0

    def test_random_scorer_has_no_edge(self):
        report = evaluate_panel(self._panel(False))
        assert abs(report.ic_t_stat) < 3

    def test_report_card_flags_anti_predictive_family(self):
        events = pd.DataFrame(
            {"family": ["a"] * 60, "direction": [1] * 60, "fwd_excess": [-0.01] * 40 + [0.01] * 20}
        )
        card = detector_report_card(events)
        assert card.iloc[0]["mean_dir_adj_excess"] < 0
