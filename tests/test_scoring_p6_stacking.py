"""P6: real-interval resampling, stacking features, and the OOF stacking dataset."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from backtests.dataset import build_symbol_panel
from backtests.stacking import (
    attach_interval_probabilities,
    build_interval_panel,
    interval_oof,
    stack_beats_base,
)
from backtests.evaluate import EvalReport
from signals_app.scoring.mtf import (
    STACK_FEATURES,
    TIMEFRAME_WEIGHTS,
    compute_multi_timeframe,
    resample_ohlcv,
    stack_features,
    stack_probabilities,
)
from signals_app.scoring.regime import regime_series


def _ohlcv(n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, n)))
    return pd.DataFrame(
        {"Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close,
         "Volume": 1e6 * (1 + rng.random(n))},
        index=pd.bdate_range("2012-01-02", periods=n),
    )


def test_resample_aggregates_ohlcv_correctly():
    daily = _ohlcv(260, 0)
    weekly = resample_ohlcv(daily, "weekly")
    first_week = daily.iloc[:5]
    assert weekly.iloc[0]["Open"] == first_week.iloc[0]["Open"]
    assert weekly.iloc[0]["High"] == first_week["High"].max()
    assert weekly.iloc[0]["Low"] == first_week["Low"].min()
    assert weekly.iloc[0]["Close"] == first_week.iloc[-1]["Close"]
    assert weekly.iloc[0]["Volume"] == first_week["Volume"].sum()
    assert len(resample_ohlcv(daily, "monthly")) == pytest.approx(12, abs=1)
    assert resample_ohlcv(daily, "daily") is daily
    with pytest.raises(ValueError):
        resample_ohlcv(daily, "hourly")


def test_missing_interval_is_nan_plus_flag_not_a_fabricated_half():
    row = stack_features({"daily": 0.6, "weekly": None, "monthly": float("nan")})
    assert row["p_daily"] == 0.6 and row["avail_daily"] == 1.0
    assert math.isnan(row["p_weekly"]) and row["avail_weekly"] == 0.0
    assert math.isnan(row["dispersion"])  # one view has no spread
    full = stack_features({"daily": 0.6, "weekly": 0.5, "monthly": 0.4})
    assert full["dispersion"] == pytest.approx(np.std([0.6, 0.5, 0.4]))
    assert set(full) == set(STACK_FEATURES)


def test_fallback_shrinks_toward_half_as_views_disagree():
    agree = stack_probabilities({"daily": 0.65, "weekly": 0.65, "monthly": 0.65})
    disagree = stack_probabilities({"daily": 0.85, "weekly": 0.65, "monthly": 0.45})
    assert agree.p_outperform == pytest.approx(0.65)
    assert abs(disagree.p_outperform - 0.5) < abs(0.67 - 0.5)  # shrunk
    assert not agree.used_meta_model and agree.available_weight_fraction == pytest.approx(1.0)
    assert stack_probabilities({}) is None
    partial = stack_probabilities({"daily": 0.6})
    assert partial.available_weight_fraction == pytest.approx(0.5)
    assert partial.intervals_available == ("daily",)


def test_multi_timeframe_reports_available_weight_fraction():
    # 1D and 5D return too few bars to score, exactly as in production (bug B7).
    frames = {"1D": _ohlcv(1, 0), "5D": _ohlcv(5, 1), "1M": _ohlcv(300, 2), "3M": _ohlcv(300, 3), "6M": _ohlcv(300, 4)}
    result = compute_multi_timeframe("TEST", frames)
    # 1M+3M+6M of the 8-timeframe weights (1Y/5Y/MAX absent from `frames` too).
    expected = sum(TIMEFRAME_WEIGHTS[tf] for tf in ("1M", "3M", "6M"))
    assert result.available_weight_fraction == pytest.approx(expected)
    assert result.to_dict()["available_weight_fraction"] == pytest.approx(expected)


def test_stack_dataset_has_no_lookahead_and_carries_availability():
    n_bars = 2700
    bench = _ohlcv(n_bars, 99)
    regimes = regime_series(bench)
    daily_panels, weekly_panels, monthly_panels = [], [], []
    for k in range(8):
        stock = _ohlcv(n_bars, k)
        sym = f"S{k}"
        daily_panels.append(build_symbol_panel(sym, stock, bench, regimes, horizons=(20,), step=40))
        weekly_panels.append(build_interval_panel(sym, "weekly", stock, bench, regimes))
        monthly_panels.append(build_interval_panel(sym, "monthly", stock, bench, regimes))
    daily = pd.concat(daily_panels, ignore_index=True)
    oofs = {
        "weekly": interval_oof(pd.concat(weekly_panels, ignore_index=True), "weekly", 1, 3),
        "monthly": interval_oof(pd.concat(monthly_panels, ignore_index=True), "monthly", 1, 3),
    }
    assert not oofs["weekly"].empty
    stacked = attach_interval_probabilities(daily, oofs)
    assert set(STACK_FEATURES) <= set(stacked.columns)
    assert len(stacked) == len(daily)
    # every attached weekly p comes from a bar dated on/before the daily row
    check = stacked.merge(oofs["weekly"].rename(columns={"date": "wdate", "p": "wp"}), on="symbol")
    check = check[(check["wdate"] <= pd.to_datetime(check["date"])) & (check["wp"] == check["p_weekly"])]
    joined = stacked.dropna(subset=["p_weekly"])
    assert len(joined) > 0
    assert (check.groupby(["symbol", "date"]).size().index.nunique()) >= len(joined) * 0.99
    # early rows (before any completed OOF weekly bar) are NaN + flag 0, not filled
    early = stacked[stacked["p_weekly"].isna()]
    assert (early["avail_weekly"] == 0.0).all()


def _report(ic: float) -> EvalReport:
    return EvalReport(ic, 0.1, 3.0, 0.3, 0.01, (0.0, 0.1), 1.0, 100, None)


def test_stack_must_beat_base_and_not_lose_in_any_regime():
    base_reg = pd.DataFrame({"regime": ["trend_up", "range"], "rank_ic": [0.02, 0.02]})
    good = pd.DataFrame({"regime": ["trend_up", "range"], "rank_ic": [0.03, 0.02]})
    bad = pd.DataFrame({"regime": ["trend_up", "range"], "rank_ic": [0.05, 0.01]})
    assert stack_beats_base(_report(0.03), _report(0.02), good, base_reg) == []
    assert stack_beats_base(_report(0.03), _report(0.02), bad, base_reg) == ["stack loses to base in regime range"]
    assert stack_beats_base(_report(0.01), _report(0.02), good, base_reg)
