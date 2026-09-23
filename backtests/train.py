"""Purged walk-forward training and evaluation of the logistic scorer (plan P3/P4).

Everything here is pure pandas/numpy over an in-memory panel so it can be
tested without the network; ``scripts/train_scorer.py`` is the I/O wrapper.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from backtests.dataset import horizon_panel
from backtests.evaluate import EvalReport, evaluate_panel, ic_by_regime
from signals_app.scoring.features import FEATURE_SETS
from signals_app.scoring.model import LogisticScorer, fit_logistic, purged_walk_forward_splits
from signals_app.scoring.probability import IsotonicCalibrator, max_reliability_gap

logger = logging.getLogger(__name__)

# Plan §2 absolute ship bar, by horizon (bars).
IC_BAR_BY_HORIZON = {5: 0.015, 20: 0.02, 60: 0.02}
DEFAULT_IC_BAR = 0.02
T_STAT_BAR = 2.0
RELIABILITY_BAR = 0.05
DEFAULT_SPLITS = 5
DEFAULT_HOLDOUT_MONTHS = 12
MIN_HOLDOUT_ROWS = 200


@dataclass
class TrainResult:
    """Outcome of one training run."""

    scorer: LogisticScorer
    oof_report: EvalReport
    regime_ic: pd.DataFrame
    holdout_report: EvalReport | None
    holdout_reliability_gap: float
    ship_bar_met: bool
    ship_bar_failures: list[str] = field(default_factory=list)


def ship_bar_failures(report: EvalReport, regime_ic: pd.DataFrame, horizon: int) -> list[str]:
    """Reasons the out-of-sample result misses the plan's absolute bar (empty = met)."""
    failures: list[str] = []
    bar = IC_BAR_BY_HORIZON.get(horizon, DEFAULT_IC_BAR)
    if math.isnan(report.rank_ic) or report.rank_ic < bar:
        failures.append(f"rank IC {report.rank_ic:.4f} < {bar}")
    if math.isnan(report.ic_t_stat) or report.ic_t_stat < T_STAT_BAR:
        failures.append(f"IC t-stat {report.ic_t_stat:.2f} < {T_STAT_BAR}")
    if not report.monotone_deciles:
        failures.append("score buckets are not monotone (top > middle > bottom)")
    for row in regime_ic.itertuples():
        if not math.isnan(row.rank_ic) and row.rank_ic < 0:
            failures.append(f"negative IC in regime {row.regime} ({row.rank_ic:.4f})")
    return failures


def _oof_predictions(
    panel: pd.DataFrame, features: tuple[str, ...], horizon: int, step: int, n_splits: int,
    holdout_start: pd.Timestamp | None,
) -> np.ndarray:
    X = panel.reindex(columns=list(features)).to_numpy(dtype=float)
    y = (panel["fwd_excess"] > 0).astype(float).to_numpy()
    oof = np.full(len(panel), np.nan)
    for train, test in purged_walk_forward_splits(
        panel["date"], n_splits, horizon, sample_step=step, holdout_start=holdout_start
    ):
        try:
            model = fit_logistic(X[train], y[train], features, horizon, "cv")
        except ValueError as exc:
            logger.warning("skipping fold: %s", exc)
            continue
        oof[test] = model.raw_proba(X[test])
    return oof


def train_scorer(
    panel: pd.DataFrame,
    horizon: int = 20,
    feature_set: str = "rung2",
    step: int = 3,
    n_splits: int = DEFAULT_SPLITS,
    holdout_months: int = DEFAULT_HOLDOUT_MONTHS,
    model_version: str | None = None,
) -> TrainResult:
    """Cross-validate, calibrate, fit the final model and score the holdout once.

    Args:
        panel: Output of ``backtests.dataset.build_symbol_panel`` concatenated
            over symbols (labels for several horizons).
        horizon: Label horizon in bars.
        feature_set: Key of ``scoring.features.FEATURE_SETS``.
        step: Sample step the panel was built with (for purge arithmetic).
        n_splits: Walk-forward folds.
        holdout_months: Final months held out of every fold and evaluated once.
        model_version: Version stamp; defaults to a timestamped id.

    Raises:
        ValueError: If no fold could be fitted.
    """
    features = FEATURE_SETS[feature_set]
    data = horizon_panel(panel, horizon)
    version = model_version or f"logit-{feature_set}-h{horizon}-{datetime.now(timezone.utc):%Y%m%d}"
    last_date = pd.to_datetime(data["date"]).max()
    holdout_start = last_date - pd.DateOffset(months=holdout_months) if holdout_months else None

    oof = _oof_predictions(data, features, horizon, step, n_splits, holdout_start)
    scored = data.assign(score=oof, p_outperform=oof).dropna(subset=["score"])
    if scored.empty:
        raise ValueError("no walk-forward fold produced predictions")
    oof_report = evaluate_panel(scored)
    regime_ic = ic_by_regime(scored)

    outcome = (scored["fwd_excess"] > 0).astype(float).to_numpy()
    calibrator = IsotonicCalibrator.fit(scored["score"].to_numpy(), outcome)
    excess_map = IsotonicCalibrator.fit(scored["score"].to_numpy(), scored["fwd_excess"].to_numpy())

    # Final model: every row that is not in the holdout and whose label window
    # ends before the holdout begins (purge, same as in the folds).
    dates = pd.to_datetime(data["date"])
    gap = pd.tseries.offsets.BDay(horizon)
    fit_mask = (dates < holdout_start - gap) if holdout_start is not None else pd.Series(True, index=data.index)
    X_all = data.reindex(columns=list(features)).to_numpy(dtype=float)
    y_all = (data["fwd_excess"] > 0).astype(float).to_numpy()
    final = fit_logistic(X_all[fit_mask.to_numpy()], y_all[fit_mask.to_numpy()], features, horizon, version, feature_set)

    holdout_report: EvalReport | None = None
    holdout_gap = math.nan
    if holdout_start is not None:
        held = data[dates >= holdout_start]
        if len(held) >= MIN_HOLDOUT_ROWS:
            raw = final.raw_proba(held.reindex(columns=list(features)).to_numpy(dtype=float))
            cal = calibrator.predict(raw)
            hp = held.assign(score=raw, p_outperform=cal)
            holdout_report = evaluate_panel(hp)
            holdout_gap = max_reliability_gap(cal, (held["fwd_excess"] > 0).astype(float).to_numpy())

    failures = ship_bar_failures(oof_report, regime_ic, horizon)
    if not math.isnan(holdout_gap) and holdout_gap > RELIABILITY_BAR:
        failures.append(f"holdout reliability gap {holdout_gap:.3f} > {RELIABILITY_BAR}")

    metrics: dict[str, Any] = {
        "oof": oof_report.to_dict(),
        "regime_ic": regime_ic.to_dict(orient="records"),
        "holdout": holdout_report.to_dict() if holdout_report else None,
        "holdout_reliability_gap": None if math.isnan(holdout_gap) else holdout_gap,
        "ship_bar_met": not failures,
        "ship_bar_failures": failures,
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_rows": int(len(data)),
        "n_symbols": int(data["symbol"].nunique()) if "symbol" in data else None,
    }
    scorer = final.with_calibration(calibrator, excess_map, metrics)
    return TrainResult(scorer, oof_report, regime_ic, holdout_report, holdout_gap, not failures, failures)


def adopt_richer_rung(simple: EvalReport, richer: EvalReport) -> bool:
    """Plan §5b: take the richer feature set only if its OOS IC beats the simpler
    one by more than one standard error of the richer IC."""
    if math.isnan(simple.rank_ic) or math.isnan(richer.rank_ic) or richer.n_dates < 2:
        return False
    standard_error = richer.ic_std / math.sqrt(richer.n_dates)
    return richer.rank_ic - simple.rank_ic > standard_error
