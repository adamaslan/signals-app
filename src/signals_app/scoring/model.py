"""Learned scorer: L2 logistic regression with purged walk-forward validation (P3).

Training imports scikit-learn lazily; inference is plain numpy so the live scan
carries no ML dependency. The artifact is JSON (no pickle).
"""
from __future__ import annotations

import json
import logging
import math
import warnings
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterator

import numpy as np
import pandas as pd

from signals_app.scoring.probability import IsotonicCalibrator

logger = logging.getLogger(__name__)

DEFAULT_MODEL_PATH = Path(__file__).resolve().parents[3] / "calibration" / "scorer_model.json"
DEFAULT_L2_C = 0.1
DRIVER_COUNT = 3
MIN_TRAIN_ROWS = 500
DEFAULT_HORIZON_DAYS = 20
DEFAULT_PUBLISH_DELTA = 0.03
# Plan §4: HIGH needs a confident probability AND enough historical analogs.
HIGH_P_THRESHOLD = 0.62
MEDIUM_P_THRESHOLD = 0.56
HIGH_MIN_ANALOGS = 40


@dataclass(frozen=True)
class LogisticScorer:
    """A fitted, serialisable logistic scorer.

    Attributes:
        feature_names: Column order the model expects.
        mean / scale: Standardisation fitted on the training rows.
        coef / intercept: Logistic parameters over standardised features.
        horizon_days: Forward horizon the label was built on.
        model_version: Identifier stamped on every prediction's provenance.
        feature_set: ``rung1`` or ``rung2`` (see ``scoring.features``).
        calibrator: Isotonic map from raw probability to observed frequency.
        excess_map: Isotonic map from raw probability to mean forward excess return.
        metrics: Out-of-sample metrics recorded at training time.
    """

    feature_names: tuple[str, ...]
    mean: tuple[float, ...]
    scale: tuple[float, ...]
    coef: tuple[float, ...]
    intercept: float
    horizon_days: int
    model_version: str
    feature_set: str = "rung2"
    calibrator: IsotonicCalibrator | None = None
    excess_map: IsotonicCalibrator | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    def _standardise(self, X: np.ndarray) -> np.ndarray:
        Z = (X - np.array(self.mean)) / np.array(self.scale)
        return np.nan_to_num(Z, nan=0.0, posinf=0.0, neginf=0.0)

    def matrix(self, rows: pd.DataFrame | list[dict[str, float]]) -> np.ndarray:
        """Feature matrix in this model's column order; absent columns are NaN."""
        frame = rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
        return frame.reindex(columns=list(self.feature_names)).to_numpy(dtype=float)

    def raw_proba(self, X: np.ndarray) -> np.ndarray:
        """Uncalibrated P(outperform)."""
        logit = self._standardise(X) @ np.array(self.coef) + self.intercept
        return 1.0 / (1.0 + np.exp(-np.clip(logit, -30.0, 30.0)))

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """Calibrated P(outperform) (raw when no calibrator has been fitted)."""
        raw = self.raw_proba(X)
        return self.calibrator.predict(raw) if self.calibrator else raw

    def expected_excess(self, X: np.ndarray) -> np.ndarray:
        """Expected forward excess return; NaN when no excess map was fitted."""
        if self.excess_map is None:
            return np.full(len(X), np.nan)
        return self.excess_map.predict(self.raw_proba(X))

    def analog_support(self, raw_p: float) -> int:
        """Historical samples behind the calibration block this raw probability falls in."""
        return self.calibrator.support(raw_p) if self.calibrator else 0

    def publish_delta(self) -> float:
        """Minimum |p - 0.5| to publish, set at training time to hit the target publish rate."""
        return float(self.metrics.get("publish_delta", DEFAULT_PUBLISH_DELTA))

    def drivers(self, x_row: np.ndarray, top: int = DRIVER_COUNT) -> list[dict[str, float | str]]:
        """Largest signed contributions to the logit for one row.

        These are exact linear contributions (coef x standardised value), the
        logistic-model analogue of SHAP values.
        """
        contrib = self._standardise(x_row.reshape(1, -1))[0] * np.array(self.coef)
        order = np.argsort(-np.abs(contrib))[:top]
        return [
            {"feature": self.feature_names[i], "contribution": round(float(contrib[i]), 4)}
            for i in order
            if contrib[i] != 0.0
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_names": list(self.feature_names),
            "mean": list(self.mean),
            "scale": list(self.scale),
            "coef": list(self.coef),
            "intercept": self.intercept,
            "horizon_days": self.horizon_days,
            "model_version": self.model_version,
            "feature_set": self.feature_set,
            "calibrator": self.calibrator.to_dict() if self.calibrator else None,
            "excess_map": self.excess_map.to_dict() if self.excess_map else None,
            "metrics": self.metrics,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LogisticScorer":
        cal = data.get("calibrator")
        exm = data.get("excess_map")
        return cls(
            feature_names=tuple(data["feature_names"]),
            mean=tuple(float(v) for v in data["mean"]),
            scale=tuple(float(v) for v in data["scale"]),
            coef=tuple(float(v) for v in data["coef"]),
            intercept=float(data["intercept"]),
            horizon_days=int(data["horizon_days"]),
            model_version=str(data["model_version"]),
            feature_set=str(data.get("feature_set", "rung2")),
            calibrator=IsotonicCalibrator.from_dict(cal) if cal else None,
            excess_map=IsotonicCalibrator.from_dict(exm) if exm else None,
            metrics=dict(data.get("metrics", {})),
        )

    def with_calibration(
        self, calibrator: IsotonicCalibrator, excess_map: IsotonicCalibrator | None, metrics: dict[str, Any]
    ) -> "LogisticScorer":
        return replace(self, calibrator=calibrator, excess_map=excess_map, metrics=metrics)

    def save(self, path: Path | str = DEFAULT_MODEL_PATH) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2))


def load_scorer_model(path: Path | str | None = None) -> LogisticScorer | None:
    """Load the shipped scorer, or None when absent or unreadable.

    None means "run the legacy confluence path unchanged" — a missing model
    must never break a scan.
    """
    target = Path(path) if path else DEFAULT_MODEL_PATH
    if not target.exists():
        return None
    try:
        return LogisticScorer.from_dict(json.loads(target.read_text()))
    except (OSError, ValueError, KeyError) as exc:
        logger.warning("scorer model %s unreadable, ignoring: %s", target, exc)
        return None


def fit_logistic(
    X: np.ndarray,
    y: np.ndarray,
    feature_names: tuple[str, ...],
    horizon_days: int,
    model_version: str,
    feature_set: str = "rung2",
    c: float = DEFAULT_L2_C,
) -> LogisticScorer:
    """Fit an L2 logistic regression on raw (unstandardised) features.

    Args:
        X: Feature matrix; NaN is imputed to the column mean.
        y: Binary outcome (1 = outperformed).
        c: Inverse L2 strength (smaller = stronger shrinkage).

    Raises:
        ValueError: If there are too few rows or only one class.
    """
    from sklearn.linear_model import LogisticRegression

    finite = np.isfinite(y)
    X, y = X[finite], y[finite].astype(int)
    if len(y) < MIN_TRAIN_ROWS:
        raise ValueError(f"need at least {MIN_TRAIN_ROWS} rows to fit, got {len(y)}")
    if len(np.unique(y)) < 2:
        raise ValueError("training labels contain a single class")

    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN columns are handled below
        mean = np.nanmean(X, axis=0)
        scale = np.nanstd(X, axis=0)
    mean = np.where(np.isfinite(mean), mean, 0.0)
    scale = np.where(np.isfinite(scale) & (scale > 1e-9), scale, 1.0)
    Z = np.nan_to_num((X - mean) / scale, nan=0.0)

    clf = LogisticRegression(C=c, max_iter=1000)  # default penalty is L2
    clf.fit(Z, y)
    return LogisticScorer(
        feature_names=tuple(feature_names),
        mean=tuple(float(v) for v in mean),
        scale=tuple(float(v) for v in scale),
        coef=tuple(float(v) for v in clf.coef_[0]),
        intercept=float(clf.intercept_[0]),
        horizon_days=horizon_days,
        model_version=model_version,
        feature_set=feature_set,
    )


def purged_walk_forward_splits(
    dates: pd.Series,
    n_splits: int,
    horizon_bars: int,
    sample_step: int = 1,
    embargo_bars: int = 0,
    holdout_start: pd.Timestamp | None = None,
) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """Expanding-window walk-forward folds with purging (plan §5c).

    A row's label spans ``horizon_bars`` bars forward, so any training row
    whose window reaches into the test fold would leak the answer. Training
    rows are dropped when their date is within ``horizon_bars`` (+ embargo) of
    the test fold's first date. Splits are by *time* across all symbols at once.

    Args:
        dates: One timestamp per panel row.
        n_splits: Number of test folds.
        horizon_bars: Label horizon in trading days.
        sample_step: Trading days between sampled dates (a dataset built every
            3rd bar has 3 bars per date step), used to convert bars to dates.
        embargo_bars: Extra gap after the purge window.
        holdout_start: Rows on/after this date are excluded from every fold
            (the final-holdout year, looked at once at the end).

    Yields:
        (train_row_positions, test_row_positions) per fold.
    """
    d = pd.to_datetime(dates).reset_index(drop=True)
    usable = d < holdout_start if holdout_start is not None else pd.Series(True, index=d.index)
    unique = np.sort(d[usable].unique())
    if len(unique) < n_splits + 1:
        return
    gap = int(math.ceil((horizon_bars + embargo_bars) / max(sample_step, 1)))
    chunks = np.array_split(unique, n_splits + 1)
    for k in range(1, n_splits + 1):
        test_dates = chunks[k]
        test_start_pos = int(np.searchsorted(unique, test_dates[0]))
        train_end_pos = test_start_pos - gap
        if train_end_pos <= 0:
            continue
        train_cutoff = unique[train_end_pos - 1]
        train_mask = (d <= train_cutoff) & usable
        test_mask = d.isin(test_dates) & usable
        yield np.flatnonzero(train_mask.to_numpy()), np.flatnonzero(test_mask.to_numpy())


def load_scorer_from_supabase(horizon_days: int = DEFAULT_HORIZON_DAYS) -> LogisticScorer | None:
    """The active scorer from the ``scorer_models`` table, or None.

    Never raises: an unreachable or empty table means "no model", and the caller
    falls back to the legacy confluence path.
    """
    import httpx

    from signals_app.config import (
        SUPABASE_REQUEST_TIMEOUT_SECONDS,
        SUPABASE_SERVICE_ROLE_KEY,
        SUPABASE_URL,
    )

    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return None
    try:
        resp = httpx.get(
            f"{SUPABASE_URL.rstrip('/')}/rest/v1/scorer_models",
            params={"select": "artifact", "is_active": "eq.true", "horizon_days": f"eq.{horizon_days}", "limit": "1"},
            headers={"apikey": SUPABASE_SERVICE_ROLE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"},
            timeout=SUPABASE_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        rows = resp.json()
        return LogisticScorer.from_dict(rows[0]["artifact"]) if rows else None
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        logger.warning("scorer: failed to load from Supabase: %s — ignoring", exc)
        return None


def load_active_scorer(horizon_days: int = DEFAULT_HORIZON_DAYS) -> LogisticScorer | None:
    """Active scorer: Supabase first (survives container restarts), then the local file."""
    return load_scorer_from_supabase(horizon_days) or load_scorer_model()


def confidence_label(p: float, analogs: int) -> str:
    """HIGH / MEDIUM / LOW from a calibrated probability and its analog count.

    Symmetric about 0.5: p = 0.38 is as confident a *sell* as 0.62 is a buy.
    """
    edge = max(p, 1.0 - p)
    if edge >= HIGH_P_THRESHOLD and analogs >= HIGH_MIN_ANALOGS:
        return "HIGH"
    if edge >= MEDIUM_P_THRESHOLD:
        return "MEDIUM"
    return "LOW"
