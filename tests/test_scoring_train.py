"""End-to-end test of dataset building and training on synthetic data (no network)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtests.dataset import build_symbol_panel, horizon_panel
from backtests.evaluate import evaluate_panel
from backtests.train import adopt_richer_rung, train_scorer
from signals_app.scoring.features import FEATURE_SETS
from signals_app.scoring.regime import regime_series


def _ohlcv(n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.012, n)))
    idx = pd.bdate_range("2016-01-01", periods=n)
    return pd.DataFrame(
        {"Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close,
         "Volume": 1e6 * (1 + rng.random(n))}, index=idx,
    )


def test_panel_columns_and_no_lookahead_in_features():
    bench = _ohlcv(420, 0)
    stock = _ohlcv(420, 1)
    panel = build_symbol_panel("XYZ", stock, bench, regime_series(bench), step=20)
    assert not panel.empty
    for name in FEATURE_SETS["rung2"]:
        assert name in panel.columns
    assert {"fwd_excess_5", "fwd_excess_20", "target_scaled_60", "regime", "symbol"} <= set(panel.columns)
    # last sampled bar has no 60-bar outcome yet
    assert panel["fwd_excess_60"].isna().any()
    # features on the first sampled bar are unchanged when future bars are dropped
    first_date = panel["date"].iloc[0]
    trimmed = stock.loc[:first_date]
    again = build_symbol_panel("XYZ", trimmed, bench.loc[:first_date], regime_series(bench).loc[:first_date], step=20)
    cols = list(FEATURE_SETS["rung2"])
    pd.testing.assert_series_equal(panel.iloc[0][cols].astype(float), again.iloc[0][cols].astype(float), check_names=False)


def test_horizon_panel_drops_unlabeled_rows():
    bench = _ohlcv(420, 0)
    panel = build_symbol_panel("XYZ", _ohlcv(420, 1), bench, regime_series(bench), step=20)
    h = horizon_panel(panel, 60)
    assert "fwd_excess" in h.columns and h["fwd_excess"].notna().all()
    assert len(h) < len(panel)


def _informative_panel(n_dates: int = 500, n_names: int = 25, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    regimes = np.array(["trend_up", "range", "trend_down", "high_vol"])
    rows = []
    for i, d in enumerate(pd.bdate_range("2015-01-01", periods=n_dates)):
        reg = regimes[(i // 60) % 4]
        for s in range(n_names):
            x = rng.normal(size=len(FEATURE_SETS["rung2"]))
            row = dict(zip(FEATURE_SETS["rung2"], x))
            row.update(date=d, symbol=f"S{s}", regime=reg,
                       fwd_excess_20=0.01 * x[0] + rng.normal(0, 0.03), target_scaled_20=0.0)
            rows.append(row)
    return pd.DataFrame(rows)


def test_training_recovers_a_planted_signal_and_reports_the_bar():
    panel = _informative_panel()
    result = train_scorer(panel, horizon=20, feature_set="rung2", step=1, n_splits=4, holdout_months=6)
    assert result.oof_report.rank_ic > 0.05
    assert result.oof_report.ic_t_stat > 2
    assert result.holdout_report is not None and result.holdout_report.rank_ic > 0.0
    assert result.scorer.calibrator is not None and result.scorer.excess_map is not None
    # the statistical bar is met; a marginal holdout reliability gap on a small synthetic sample is tolerated
    assert all(f.startswith("holdout reliability") for f in result.ship_bar_failures), result.ship_bar_failures
    # the planted feature is the largest coefficient
    top = int(np.argmax(np.abs(result.scorer.coef)))
    assert result.scorer.feature_names[top] == FEATURE_SETS["rung2"][0]


def test_training_on_noise_fails_the_ship_bar():
    panel = _informative_panel(seed=4)
    panel["fwd_excess_20"] = np.random.default_rng(9).normal(0, 0.03, len(panel))
    result = train_scorer(panel, horizon=20, feature_set="rung1", step=1, n_splits=4, holdout_months=6)
    assert not result.ship_bar_met
    assert result.ship_bar_failures


def test_adopt_richer_rung_needs_more_than_one_standard_error():
    panel = _informative_panel()
    strong = train_scorer(panel, 20, "rung2", 1, 4, 6).oof_report
    weak = evaluate_panel(panel.assign(score=np.random.default_rng(1).normal(size=len(panel)),
                                       fwd_excess=panel["fwd_excess_20"]))
    assert adopt_richer_rung(weak, strong)
    assert not adopt_richer_rung(strong, weak)
