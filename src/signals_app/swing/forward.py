"""Forward returns conditioned on a trigger — "what usually happens next?".

The sell-side question ("after a +30% run in 5 days, does it pull back?") and
the buy-side one ("after a 12% dip, does it bounce?") are the same query:
compare the ``horizon``-bar forward return on bars where a condition holds
against the unconditional forward return on every bar.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ForwardStats:
    """Forward-return distribution on condition bars vs. all bars."""

    horizon: int
    n_bars: int
    avg_pct: float | None
    median_pct: float | None
    down_rate: float | None
    worst_pct: float | None
    best_pct: float | None
    baseline_avg_pct: float | None
    baseline_down_rate: float | None
    edge_pct: float | None


def forward_returns(close: np.ndarray, horizon: int) -> np.ndarray:
    """Percent return from each bar to ``horizon`` bars later; NaN where unknown."""
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    close = np.asarray(close, dtype=float)
    out = np.full(len(close), np.nan)
    if len(close) > horizon:
        out[:-horizon] = (close[horizon:] / close[:-horizon] - 1) * 100
    return out


def conditional_forward(close: np.ndarray, mask: np.ndarray, horizon: int) -> ForwardStats:
    """Forward-return stats on ``mask`` bars, with the all-bars baseline."""
    fwd = forward_returns(close, horizon)
    known = ~np.isnan(fwd)
    hit = fwd[np.asarray(mask, dtype=bool) & known]
    base = fwd[known]

    def avg(x: np.ndarray) -> float | None:
        return float(x.mean()) if x.size else None

    a, b = avg(hit), avg(base)
    return ForwardStats(
        horizon=horizon,
        n_bars=int(hit.size),
        avg_pct=a,
        median_pct=float(np.median(hit)) if hit.size else None,
        down_rate=float((hit < 0).mean()) if hit.size else None,
        worst_pct=float(hit.min()) if hit.size else None,
        best_pct=float(hit.max()) if hit.size else None,
        baseline_avg_pct=b,
        baseline_down_rate=float((base < 0).mean()) if base.size else None,
        edge_pct=(a - b) if a is not None and b is not None else None,
    )
