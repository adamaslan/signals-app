"""Timeframe stacking dataset and comparison (plan §7, P6).

Base models are trained per real bar interval (daily / weekly / monthly). Their
*out-of-fold* probabilities become the features of a small meta-model, so the
meta-model never sees an in-sample prediction (no stacking leakage).
"""
from __future__ import annotations

import pandas as pd

from backtests.dataset import build_symbol_panel, horizon_panel
from backtests.evaluate import EvalReport
from backtests.train import oof_predictions
from signals_app.scoring.features import FEATURE_SETS
from signals_app.scoring.mtf import STACK_FEATURES, resample_ohlcv, stack_features

# Warm-up bars per interval: indicators need history, but a monthly frame has
# ~120 bars in ten years, so the daily 200-bar warm-up cannot apply.
MIN_LOOKBACK = {"daily": 200, "weekly": 52, "monthly": 24}
# ~20 trading days expressed in each interval's own bars.
HORIZON_BARS = {"daily": 20, "weekly": 4, "monthly": 1}


def build_interval_panel(
    symbol: str,
    interval: str,
    daily_ohlcv: pd.DataFrame,
    daily_benchmark: pd.DataFrame,
    daily_regimes: pd.Series,
    step: int = 1,
) -> pd.DataFrame:
    """Feature/label panel on ``interval`` bars (labels in that interval's bars)."""
    ohlcv = resample_ohlcv(daily_ohlcv, interval)
    benchmark = resample_ohlcv(daily_benchmark, interval)
    return build_symbol_panel(
        symbol, ohlcv, benchmark, daily_regimes,
        horizons=(HORIZON_BARS[interval],), step=step, min_lookback=MIN_LOOKBACK[interval],
    )


def interval_oof(panel: pd.DataFrame, interval: str, step: int, n_splits: int) -> pd.DataFrame:
    """Out-of-fold raw probabilities for one interval's panel: ``symbol, date, p``."""
    data = horizon_panel(panel, HORIZON_BARS[interval])
    oof = oof_predictions(
        data, FEATURE_SETS["rung2"], HORIZON_BARS[interval], step, n_splits, holdout_start=None
    )
    out = data[["symbol", "date"]].assign(p=oof).dropna(subset=["p"])
    return out.sort_values("date").reset_index(drop=True)


def attach_interval_probabilities(
    daily_panel: pd.DataFrame, oof_by_interval: dict[str, pd.DataFrame]
) -> pd.DataFrame:
    """Add ``STACK_FEATURES`` to a daily panel, as-of joining weekly/monthly OOF p.

    Each daily row takes the most recent *completed* higher-interval bar at or
    before its date (backward as-of), so no future bar leaks in. The daily
    interval's own probability is taken from ``oof_by_interval["daily"]``.
    """
    base = daily_panel.copy()
    base["date"] = pd.to_datetime(base["date"])
    base = base.sort_values("date")
    for interval, oof in oof_by_interval.items():
        right = oof.rename(columns={"p": f"p_{interval}"}).copy()
        right["date"] = pd.to_datetime(right["date"])
        base = pd.merge_asof(
            base, right.sort_values("date"), on="date", by="symbol", direction="backward"
        )
    intervals = ("daily", "weekly", "monthly")
    feats = pd.DataFrame(
        [
            stack_features({i: (None if pd.isna(r.get(f"p_{i}")) else r.get(f"p_{i}")) for i in intervals})
            for r in base.to_dict("records")
        ]
    )
    base = base.drop(columns=[f"p_{i}" for i in intervals if f"p_{i}" in base.columns]).reset_index(drop=True)
    return pd.concat([base, feats], axis=1)


def stack_beats_base(
    stacked: EvalReport, base: EvalReport, stacked_regime_ic: pd.DataFrame, base_regime_ic: pd.DataFrame
) -> list[str]:
    """Plan P6 ship criterion: beat the daily-only model out of sample and never lose in a regime.

    Returns:
        Reasons the stack does not clear the bar (empty when it does).
    """
    failures: list[str] = []
    if not stacked.rank_ic > base.rank_ic:
        failures.append(f"stacked IC {stacked.rank_ic:.4f} does not beat base {base.rank_ic:.4f}")
    merged = stacked_regime_ic.merge(base_regime_ic, on="regime", suffixes=("_stack", "_base"))
    for row in merged.itertuples():
        if row.rank_ic_stack < row.rank_ic_base:
            failures.append(f"stack loses to base in regime {row.regime}")
    return failures


__all__ = [
    "STACK_FEATURES",
    "attach_interval_probabilities",
    "build_interval_panel",
    "interval_oof",
    "stack_beats_base",
]
