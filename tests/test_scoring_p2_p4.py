"""Tests for plan phases P2 (families/regime), P3 (model/CV) and P4 (probability)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from signals_app.config import SignalCategory, SignalStrength
from signals_app.detection.base import MutableSignal
from signals_app.scoring.confluence import ConfluenceRanker, FamilyConfluenceRanker
from signals_app.scoring.model import LogisticScorer, fit_logistic, purged_walk_forward_splits
from signals_app.scoring.probability import (
    IsotonicCalibrator,
    max_reliability_gap,
    rank_pct,
)
from signals_app.scoring.regime import HIGH_VOL, RANGE, TREND_DOWN, TREND_UP, regime_series


def sig(name: str, category: SignalCategory, strength: SignalStrength, desc: str = "") -> MutableSignal:
    return MutableSignal(signal=name, description=desc, strength=strength.value, category=category.value)


# ── P2 ───────────────────────────────────────────────────────────────────────


def test_fanout_in_one_family_cannot_carry_a_buy():
    signals = [sig(f"HL{i}", SignalCategory.RANGE, SignalStrength.EXTREME_BULLISH) for i in range(15)]
    legacy = ConfluenceRanker().rank_signals(signals)
    family = FamilyConfluenceRanker().rank_signals(signals)
    assert family.action == "HOLD"
    assert family.agreeing_families == 1
    assert abs(family.families["structure"]) < 1.0
    assert legacy.total_signals == family.total_signals == 15


def test_three_agreeing_families_buy():
    signals = [
        sig("MA", SignalCategory.MA_CROSS, SignalStrength.STRONG_BULLISH),
        sig("RSI", SignalCategory.RSI, SignalStrength.STRONG_BULLISH),
        sig("OBV", SignalCategory.OBV_CMF, SignalStrength.STRONG_BULLISH),
        sig("MAD", SignalCategory.MA_DISTANCE, SignalStrength.STRONG_BULLISH),
        sig("SR", SignalCategory.SUPPORT_RESISTANCE, SignalStrength.STRONG_BULLISH),
    ]
    result = FamilyConfluenceRanker().rank_signals(signals)
    assert result.agreeing_families == 5
    assert result.action == "BUY"
    assert 0.0 < result.score < 1.0


def test_two_opposing_families_block_buy():
    signals = [
        sig("MA", SignalCategory.MA_CROSS, SignalStrength.EXTREME_BULLISH),
        sig("RSI", SignalCategory.RSI, SignalStrength.EXTREME_BULLISH),
        sig("OBV", SignalCategory.OBV_CMF, SignalStrength.EXTREME_BULLISH),
        sig("MAD", SignalCategory.MA_DISTANCE, SignalStrength.EXTREME_BEARISH),
        sig("SR", SignalCategory.SUPPORT_RESISTANCE, SignalStrength.EXTREME_BEARISH),
    ]
    assert FamilyConfluenceRanker().rank_signals(signals).action == "HOLD"


def test_regime_gate_neutralises_bearish_extension_only_in_uptrend():
    signals = [
        sig("MAD", SignalCategory.MA_DISTANCE, SignalStrength.STRONG_BEARISH),
        sig("RSI OVERBOUGHT", SignalCategory.RSI, SignalStrength.BEARISH, "RSI overbought"),
        sig("MACD", SignalCategory.MACD, SignalStrength.BEARISH, "MACD cross down"),
    ]
    ungated = FamilyConfluenceRanker().rank_signals(signals, regime=RANGE)
    gated = FamilyConfluenceRanker().rank_signals(signals, regime=TREND_UP)
    assert ungated.bear_count == 3
    assert gated.bear_count == 1  # only the MACD cross survives
    assert gated.score > ungated.score


def test_direction_less_strength_does_not_vote():
    result = FamilyConfluenceRanker().rank_signals(
        [sig("VOL", SignalCategory.VOLUME, SignalStrength.VERY_SIGNIFICANT)]
    )
    assert result.score == 0.0 and result.bull_count == 0


def _benchmark(n: int, drift: float, noise: float = 0.005, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, noise, n)))
    return pd.DataFrame({"Close": close}, index=pd.bdate_range("2018-01-01", periods=n))


def test_regime_labels_up_down_and_are_point_in_time():
    up = regime_series(_benchmark(600, 0.002))
    down = regime_series(_benchmark(600, -0.002))
    assert up.iloc[:199].isna().all()
    assert up.iloc[-1] == TREND_UP
    assert down.iloc[-1] == TREND_DOWN
    # Appending future bars must not change any earlier label.
    bench = _benchmark(700, 0.001, seed=3)
    assert regime_series(bench.iloc[:600]).equals(regime_series(bench).iloc[:600])


def test_high_vol_regime_when_vol_spikes():
    bench = _benchmark(700, 0.001, noise=0.004, seed=1)
    shock = bench["Close"].to_numpy().copy()
    rng = np.random.default_rng(5)
    shock[650:] = shock[650] * np.exp(np.cumsum(rng.normal(0, 0.05, 50)))
    bench["Close"] = shock
    assert regime_series(bench).iloc[-1] == HIGH_VOL


# ── P4 ───────────────────────────────────────────────────────────────────────


def test_isotonic_is_monotone_and_recovers_a_step():
    rng = np.random.default_rng(0)
    x = rng.uniform(0, 1, 4000)
    y = (rng.uniform(0, 1, 4000) < np.where(x > 0.5, 0.7, 0.3)).astype(float)
    cal = IsotonicCalibrator.fit(x, y)
    grid = cal.predict(np.linspace(0, 1, 200))
    assert np.all(np.diff(grid) >= -1e-12)
    assert cal.predict(0.2) == pytest.approx(0.3, abs=0.05)
    assert cal.predict(0.8) == pytest.approx(0.7, abs=0.05)


def test_isotonic_roundtrips_through_dict():
    cal = IsotonicCalibrator.fit(np.arange(50.0), (np.arange(50) % 2).astype(float))
    again = IsotonicCalibrator.from_dict(cal.to_dict())
    assert np.allclose(cal.predict(np.linspace(0, 49, 20)), again.predict(np.linspace(0, 49, 20)))


def test_rank_pct_cannot_saturate_and_handles_ties():
    scores = {f"S{i}": 0.99 for i in range(50)}  # everyone unanimous
    scores["TOP"] = 1.0
    ranks = rank_pct(scores)
    assert ranks["TOP"] == 100.0
    assert ranks["S0"] < 100.0
    lone = rank_pct({"A": 0.3})
    assert lone == {"A": 50.0}
    spread = rank_pct({"A": 0.1, "B": 0.5, "C": 0.9})
    assert (spread["A"], spread["B"], spread["C"]) == (0.0, 50.0, 100.0)
    assert rank_pct({"A": float("nan"), "B": 1.0}) == {"B": 50.0}


def test_reliability_gap_flags_overconfidence():
    rng = np.random.default_rng(1)
    p = rng.uniform(0.05, 0.95, 20000)
    calibrated = (rng.uniform(size=p.size) < p).astype(float)
    overconfident = (rng.uniform(size=p.size) < 0.5 + 0.3 * (p - 0.5)).astype(float)
    assert max_reliability_gap(p, calibrated) < 0.05
    assert max_reliability_gap(p, overconfident) > 0.1


# ── P3 ───────────────────────────────────────────────────────────────────────


def _synthetic_panel(n_dates: int = 300, n_names: int = 12, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for d in pd.bdate_range("2020-01-01", periods=n_dates):
        for s in range(n_names):
            x = rng.normal(size=3)
            excess = 0.02 * x[0] + rng.normal(0, 0.05)
            rows.append({"date": d, "symbol": f"S{s}", "f0": x[0], "f1": x[1], "f2": x[2], "fwd_excess": excess})
    return pd.DataFrame(rows)


def test_purged_splits_never_leak_label_windows():
    panel = _synthetic_panel(n_dates=240, n_names=3)
    horizon = 20
    folds = list(purged_walk_forward_splits(panel["date"], n_splits=4, horizon_bars=horizon))
    assert len(folds) == 4
    dates = pd.to_datetime(panel["date"])
    for train, test in folds:
        assert set(train).isdisjoint(test)
        gap_days = np.busday_count(dates.iloc[train].max().date(), dates.iloc[test].min().date())
        assert gap_days >= horizon
        assert dates.iloc[train].max() < dates.iloc[test].min()


def test_holdout_is_excluded_from_every_fold():
    panel = _synthetic_panel(n_dates=240, n_names=2)
    cutoff = pd.Timestamp(panel["date"].sort_values().iloc[-60 * 2])
    for train, test in purged_walk_forward_splits(panel["date"], 3, 5, holdout_start=cutoff):
        assert pd.to_datetime(panel["date"]).iloc[np.r_[train, test]].max() < cutoff


def test_logistic_learns_the_signal_and_roundtrips(tmp_path):
    panel = _synthetic_panel()
    X = panel[["f0", "f1", "f2"]].to_numpy()
    y = (panel["fwd_excess"] > 0).astype(float).to_numpy()
    model = fit_logistic(X, y, ("f0", "f1", "f2"), horizon_days=20, model_version="test-1", feature_set="rung1")
    assert model.coef[0] > 0 and abs(model.coef[1]) < abs(model.coef[0])
    path = tmp_path / "m.json"
    model.save(path)
    from signals_app.scoring.model import load_scorer_model

    loaded = load_scorer_model(path)
    assert isinstance(loaded, LogisticScorer)
    assert np.allclose(loaded.predict_proba(X[:20]), model.predict_proba(X[:20]))
    assert model.drivers(X[0])[0]["feature"] in {"f0", "f1", "f2"}


def test_missing_or_corrupt_model_means_none(tmp_path):
    from signals_app.scoring.model import load_scorer_model

    assert load_scorer_model(tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert load_scorer_model(bad) is None


def test_fit_rejects_tiny_or_single_class_data():
    X = np.zeros((10, 2))
    with pytest.raises(ValueError):
        fit_logistic(X, np.zeros(10), ("a", "b"), 5, "v")
    with pytest.raises(ValueError):
        fit_logistic(np.zeros((600, 2)), np.ones(600), ("a", "b"), 5, "v")
