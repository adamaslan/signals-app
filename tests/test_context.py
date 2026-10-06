"""risk_context / location: reported, point-in-time, never a vote."""
from __future__ import annotations

import numpy as np
import pandas as pd

from signals_app.indicators.compute import compute_indicators
from signals_app.scoring.context import location, risk_context

from .test_kinds import _random_walk_ohlcv


def test_risk_context_flags_a_downtrend_below_the_200_sma() -> None:
    close = 200 * np.exp(-0.002 * np.arange(400))
    df = compute_indicators(pd.DataFrame(
        {"Open": close, "High": close * 1.004, "Low": close * 0.996, "Close": close,
         "Volume": 1e6}, index=pd.date_range("2024-01-01", periods=400, freq="B")))
    ctx = risk_context(df)
    assert ctx["below_sma200"] is True
    assert ctx["sma200_dist_atr"] < 0
    assert 0 < ctx["atr_pct"] < 0.05
    assert ctx["atr_percentile"] is not None


def test_risk_context_is_point_in_time() -> None:
    full = compute_indicators(_random_walk_ohlcv(3))
    cut = 330
    assert risk_context(full.iloc[:cut]) == risk_context(full.iloc[:cut].copy())
    assert risk_context(full.iloc[:cut]) != risk_context(full)


def test_risk_context_degrades_to_none_not_an_error() -> None:
    assert risk_context(pd.DataFrame()) == {
        "below_sma200": None, "sma200_dist_atr": None, "atr_pct": None,
        "atr_percentile": None, "high_vol": None}
    sparse = risk_context(pd.DataFrame({"Close": [1.0, 2.0]}))
    assert sparse["below_sma200"] is None


def test_location_reports_support_below_and_resistance_above() -> None:
    full = compute_indicators(_random_walk_ohlcv(11))
    out = location(full)
    for side in ("nearest_support", "nearest_resistance"):
        found = out[side]
        if found is None:
            continue
        assert found["dist_atr"] <= 0 if side == "nearest_support" else found["dist_atr"] > 0
    assert out["nearest_support"] or out["nearest_resistance"]
    assert isinstance(out["levels_near"], int)


def test_location_with_no_atr_is_empty() -> None:
    assert location(pd.DataFrame({"Close": [1.0, 2.0]}))["nearest_support"] is None
