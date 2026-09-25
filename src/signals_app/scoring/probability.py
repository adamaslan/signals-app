"""Probability calibration and cross-sectional rank (docs/scoring-2x-plan.md §4, P4).

Pure numpy so the live scan needs no scikit-learn: isotonic fits are trained
offline (``scripts/train_scorer.py``) and shipped as JSON breakpoints.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

# Plan P4 ship bar: observed frequency within this of predicted in every bin.
RELIABILITY_TOLERANCE: float = 0.05
RELIABILITY_MIN_BIN_SAMPLES: int = 100
RELIABILITY_BINS: int = 10


@dataclass(frozen=True)
class IsotonicCalibrator:
    """Monotone non-decreasing map ``x -> y`` fit by pool-adjacent-violators.

    Works for a probability target (0/1 outcomes) or a continuous one (mean
    excess return). Predictions clamp outside the fitted range.
    """

    xs: tuple[float, ...]
    ys: tuple[float, ...]
    counts: tuple[int, ...] = ()

    @classmethod
    def fit(cls, x: np.ndarray, y: np.ndarray) -> IsotonicCalibrator:
        """Fit on paired samples; raises ValueError when fewer than 2 are finite."""
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        keep = np.isfinite(x) & np.isfinite(y)
        x, y = x[keep], y[keep]
        if len(x) < 2:
            raise ValueError("need at least 2 finite samples to fit an isotonic calibrator")
        order = np.argsort(x, kind="stable")
        x, y = x[order], y[order]

        # Each block: [sum_y, count, sum_x]; merge backwards while means violate order.
        sums: list[float] = []
        counts: list[int] = []
        xsums: list[float] = []
        for xi, yi in zip(x, y):
            sums.append(float(yi))
            counts.append(1)
            xsums.append(float(xi))
            while len(sums) > 1 and sums[-2] / counts[-2] >= sums[-1] / counts[-1]:
                last_sum, last_count, last_xsum = sums.pop(), counts.pop(), xsums.pop()
                sums[-1] += last_sum
                counts[-1] += last_count
                xsums[-1] += last_xsum
        return cls(
            xs=tuple(xs / c for xs, c in zip(xsums, counts)),
            ys=tuple(s / c for s, c in zip(sums, counts)),
            counts=tuple(counts),
        )

    def predict(self, x: np.ndarray | float) -> np.ndarray:
        """Calibrated value(s) for ``x``."""
        return np.interp(np.asarray(x, dtype=float), self.xs, self.ys)

    def support(self, x: float) -> int:
        """Training samples behind the block nearest ``x`` ("historical analogs"); 0 if unknown."""
        if not self.counts:
            return 0
        return int(self.counts[int(np.argmin(np.abs(np.asarray(self.xs) - float(x))))])

    def to_dict(self) -> dict[str, Any]:
        return {"xs": list(self.xs), "ys": list(self.ys), "counts": list(self.counts)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IsotonicCalibrator:
        return cls(
            xs=tuple(float(v) for v in data["xs"]),
            ys=tuple(float(v) for v in data["ys"]),
            counts=tuple(int(v) for v in data.get("counts", ())),
        )


def rank_pct(scores: dict[str, float]) -> dict[str, float]:
    """Percentile (0-100) of each symbol's score within one scan run.

    Ties share their average rank. A single symbol has no cross-section and
    gets 50.0. NaN scores are omitted from the result.
    """
    valid = {k: v for k, v in scores.items() if v is not None and not math.isnan(v)}
    n = len(valid)
    if n == 0:
        return {}
    if n == 1:
        return {k: 50.0 for k in valid}
    keys = list(valid)
    values = np.array([valid[k] for k in keys])
    # average-rank percentile: (count_below + 0.5 * count_equal_others) / (n - 1)
    below = (values[:, None] > values[None, :]).sum(axis=1)
    equal = (values[:, None] == values[None, :]).sum(axis=1) - 1
    pct = 100.0 * (below + 0.5 * equal) / (n - 1)
    return {k: round(float(p), 2) for k, p in zip(keys, pct)}


def reliability_gaps(
    probabilities: np.ndarray,
    outcomes: np.ndarray,
    bins: int = RELIABILITY_BINS,
    min_samples: int = RELIABILITY_MIN_BIN_SAMPLES,
) -> list[tuple[float, float, int]]:
    """(mean predicted, observed frequency, n) for each populated probability bin.

    Bins with fewer than ``min_samples`` are dropped: their observed frequency
    is too noisy to hold a calibration to.
    """
    p = np.asarray(probabilities, dtype=float)
    o = np.asarray(outcomes, dtype=float)
    keep = np.isfinite(p) & np.isfinite(o)
    p, o = p[keep], o[keep]
    if len(p) == 0:
        return []
    idx = np.minimum((p * bins).astype(int), bins - 1)
    out: list[tuple[float, float, int]] = []
    for b in range(bins):
        mask = idx == b
        if mask.sum() >= min_samples:
            out.append((float(p[mask].mean()), float(o[mask].mean()), int(mask.sum())))
    return out


def max_reliability_gap(probabilities: np.ndarray, outcomes: np.ndarray) -> float:
    """Worst |predicted - observed| across populated bins; NaN when none are populated."""
    gaps = [abs(pred - obs) for pred, obs, _ in reliability_gaps(probabilities, outcomes)]
    return max(gaps) if gaps else math.nan
