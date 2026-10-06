"""P8: rung-3 graded features: frozen rungs, causality, NaN handling, AUC gate."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from backtests.dataset import build_symbol_panel
from backtests.train import (
    LEAKAGE_AUC_RED_FLAG,
    RUNG3_MIN_AUC_GAIN,
    TrainResult,
    auc_score,
    rung3_failures,
    train_scorer,
)
from signals_app.detection.orchestrator import detect_all_signals, get_experimental_detectors
from signals_app.indicators.compute import compute_indicators
from signals_app.scoring.features import (
    FAMILIES,
    FEATURE_SETS,
    GRADED_FEATURES,
    build_feature_row,
    build_feature_row_rung3,
    continuous_features_frame,
    graded_features,
)
from signals_app.scoring.graded import GradedConfluenceRanker
from signals_app.scoring.regime import regime_series

from .test_kinds import _random_walk_ohlcv

WARMUP = 230


def test_rungs_1_and_2_are_frozen() -> None:
    """Models already trained on these must keep reproducing: sizes and prefixes are pinned."""
    assert len(FEATURE_SETS["rung1"]) == 5
    assert len(FEATURE_SETS["rung2"]) == 37
    assert FEATURE_SETS["rung3"][:37] == FEATURE_SETS["rung2"]
    assert FEATURE_SETS["rung3"][37:] == GRADED_FEATURES


def test_graded_feature_names() -> None:
    assert GRADED_FEATURES[: len(FAMILIES)] == tuple(f"gfam_{f}" for f in FAMILIES)
    assert {"n_events_bull", "n_events_bear", "state_budget_used", "risk_below_sma200",
            "risk_atr_pct", "loc_dist_atr"} <= set(GRADED_FEATURES)
    assert len(set(FEATURE_SETS["rung3"])) == len(FEATURE_SETS["rung3"])


def _row_at(df: pd.DataFrame, pos: int) -> dict[str, float]:
    window = df.iloc[: pos + 1]
    signals = list(detect_all_signals(window))
    extended = signals + list(detect_all_signals(window, get_experimental_detectors()))
    cont = continuous_features_frame(df).iloc[pos]
    return build_feature_row_rung3(signals, extended, cont, "range", window, GradedConfluenceRanker())


def test_rung3_row_covers_every_feature_and_keeps_the_rung2_part_unchanged() -> None:
    df = compute_indicators(_random_walk_ohlcv(3))
    row = _row_at(df, 330)
    assert set(FEATURE_SETS["rung3"]) <= set(row)
    window = df.iloc[:331]
    plain = build_feature_row(list(detect_all_signals(window)), continuous_features_frame(df).iloc[330],
                              "range")
    for name in FEATURE_SETS["rung2"]:
        a, b = row[name], plain[name]
        assert (math.isnan(a) and math.isnan(b)) or a == b


def test_graded_features_are_causal() -> None:
    """Changing every bar after t must not change the features at t (no look-ahead)."""
    original = _random_walk_ohlcv(5)
    pos = 320
    perturbed = original.copy()
    rng = np.random.default_rng(99)
    future = perturbed.index[pos + 1:]
    for column in ("Open", "High", "Low", "Close"):
        perturbed.loc[future, column] = perturbed.loc[future, column] * rng.uniform(0.5, 2.0, len(future))
    perturbed["High"] = perturbed[["Open", "High", "Low", "Close"]].max(axis=1)
    perturbed["Low"] = perturbed[["Open", "High", "Low", "Close"]].min(axis=1)
    before = _row_at(compute_indicators(original), pos)
    after = _row_at(compute_indicators(perturbed), pos)
    for name in GRADED_FEATURES:
        a, b = before[name], after[name]
        assert (math.isnan(a) and math.isnan(b)) or a == pytest.approx(b), name


def test_missing_context_becomes_nan_not_an_error() -> None:
    result = GradedConfluenceRanker().rank_signals([])
    row = graded_features(result)
    assert math.isnan(row["risk_below_sma200"]) and math.isnan(row["loc_dist_atr"])
    assert row["n_events_bull"] == 0 and row["state_budget_used"] == 0


def test_state_budget_used_reports_pressure_above_one() -> None:
    from signals_app.detection.base import MutableSignal
    from signals_app.scoring.kinds import stamp_kinds

    states = [MutableSignal(signal=label, description="", strength="STRONG BULLISH", category=cat)
              for label, cat in (("RSI EXTREME OVERSOLD", "RSI"), ("STOCHASTIC OVERSOLD", "STOCHASTIC"),
                                 ("RSI14 OVERSOLD (<30)", "RSI"), ("STOCH X OVERSOLD", "STOCHASTIC"))]
    stamp_kinds(states)
    result = GradedConfluenceRanker().rank_signals(states[:2])
    assert 0 < graded_features(result)["state_budget_used"] <= 1.0


def test_event_counts_are_split_by_side() -> None:
    from signals_app.detection.base import MutableSignal
    from signals_app.scoring.kinds import stamp_kinds

    signals = [MutableSignal(signal="MACD BULL CROSS", description="", strength="BULLISH", category="MACD"),
               MutableSignal(signal="DEATH CROSS", description="", strength="STRONG BEARISH",
                             category="MA_CROSS")]
    stamp_kinds(signals)
    row = graded_features(GradedConfluenceRanker().rank_signals(signals))
    assert (row["n_events_bull"], row["n_events_bear"]) == (1, 1)


def test_panel_with_graded_adds_columns_and_leaves_rungs_1_2_identical() -> None:
    ohlcv = _random_walk_ohlcv(2, n=420)
    bench = _random_walk_ohlcv(9, n=420)
    regimes = regime_series(bench)
    plain = build_symbol_panel("AAA", ohlcv, bench, regimes, horizons=(5,), step=20, min_lookback=230)
    graded = build_symbol_panel("AAA", ohlcv, bench, regimes, horizons=(5,), step=20,
                                min_lookback=230, include_graded=True)
    assert not set(GRADED_FEATURES) & set(plain.columns)
    assert set(GRADED_FEATURES) <= set(graded.columns)
    shared = list(FEATURE_SETS["rung2"])
    pd.testing.assert_frame_equal(plain[shared], graded[shared])


def test_auc_score_known_values() -> None:
    assert auc_score(np.array([0, 0, 1, 1]), np.array([0.1, 0.2, 0.8, 0.9])) == 1.0
    assert auc_score(np.array([0, 0, 1, 1]), np.array([0.9, 0.8, 0.2, 0.1])) == 0.0
    assert auc_score(np.array([0, 1, 0, 1]), np.array([0.5, 0.5, 0.5, 0.5])) == 0.5
    assert math.isnan(auc_score(np.array([1, 1, 1]), np.array([0.1, 0.2, 0.3])))


def test_auc_matches_a_brute_force_pair_count() -> None:
    rng = np.random.default_rng(4)
    y = rng.integers(0, 2, 200).astype(float)
    p = rng.random(200)
    pos, neg = p[y == 1], p[y == 0]
    brute = np.mean([(a > b) + 0.5 * (a == b) for a in pos for b in neg])
    assert auc_score(y, p) == pytest.approx(brute)


def _result(auc: float, ship_bar: bool = True) -> TrainResult:
    return TrainResult(scorer=None, oof_report=None, regime_ic=None, holdout_report=None,  # type: ignore[arg-type]
                       holdout_reliability_gap=0.0, ship_bar_met=ship_bar, oof_auc=auc)


class TestRung3Gate:
    def test_adopts_when_it_gains_enough_and_is_not_suspicious(self) -> None:
        assert rung3_failures(_result(0.52), _result(0.52 + RUNG3_MIN_AUC_GAIN + 0.001)) == []

    def test_drops_a_negligible_gain(self) -> None:
        (failure,) = rung3_failures(_result(0.52), _result(0.522))
        assert "AUC gain" in failure

    def test_flags_leakage_even_when_the_gain_is_large(self) -> None:
        failures = rung3_failures(_result(0.52), _result(LEAKAGE_AUC_RED_FLAG + 0.02))
        assert any("leakage" in f for f in failures)

    def test_requires_the_ship_bar(self) -> None:
        assert any("ship bar" in f for f in rung3_failures(_result(0.52), _result(0.55, ship_bar=False)))

    def test_missing_auc_blocks_adoption(self) -> None:
        assert rung3_failures(_result(math.nan), _result(0.55))


def test_train_scorer_reports_oof_auc_for_the_existing_rungs() -> None:
    from tests.test_scoring_train import _informative_panel as _panel  # existing synthetic panel

    result = train_scorer(_panel(), horizon=20, feature_set="rung2", step=1, n_splits=4, holdout_months=6)
    assert 0.0 <= result.oof_auc <= 1.0
    assert result.scorer.metrics["oof_auc"] == pytest.approx(result.oof_auc)


class _FakeScorer:
    feature_set = "rung3"
    model_version = "logit-rung3-test"

    def __init__(self, evidence_version: str | None) -> None:
        self.metrics = {"evidence_version": evidence_version}


def test_scan_refuses_a_rung3_model_trained_on_different_evidence() -> None:
    from signals_app import scanner

    scanner._graded_ranker.cache_clear()
    df = compute_indicators(_random_walk_ohlcv(1))
    signals = detect_all_signals(df)
    cont = continuous_features_frame(df).iloc[-1]
    with pytest.raises(ValueError, match="trained on evidence"):
        scanner._rung3_row(_FakeScorer("some-other-version"), df, signals, cont, "range")
    with pytest.raises(ValueError, match="trained on evidence"):
        scanner._rung3_row(_FakeScorer(None), df, signals, cont, "range")


def test_scan_builds_rung3_features_when_the_evidence_matches() -> None:
    from signals_app import scanner

    scanner._graded_ranker.cache_clear()
    df = compute_indicators(_random_walk_ohlcv(1))
    signals = detect_all_signals(df)
    cont = continuous_features_frame(df).iloc[-1]
    version = scanner._graded_ranker().evidence_version
    row = scanner._rung3_row(_FakeScorer(version), df, signals, cont, "range")
    assert set(FEATURE_SETS["rung3"]) <= set(row)
