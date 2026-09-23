"""Scorer evaluation harness — measures whether a scorer ranks forward returns.

Operates on a long panel with one row per (date, symbol): a scorer output, the
forward excess return (see ``backtests.engine`` for the label), and optionally a
predicted probability and the signal families that fired. Every metric is
cross-sectional (per date, then averaged) so market-wide drift cannot inflate it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

SCORE_COL = "score"
TARGET_COL = "fwd_excess"
DATE_COL = "date"
PROB_COL = "p_outperform"

MIN_NAMES_PER_DATE = 5
DECILES = 10
RELIABILITY_BINS = 10
MIN_FAMILY_SAMPLES = 40


@dataclass(frozen=True)
class EvalReport:
    """Headline metrics for one scorer over one panel."""

    rank_ic: float
    ic_std: float
    ic_t_stat: float
    ic_ir: float
    decile_spread: float
    decile_means: tuple[float, ...]
    coverage: float
    n_dates: int
    brier: float | None

    @property
    def monotone_deciles(self) -> bool:
        """True when mean forward excess rises across score terciles."""
        third = len(self.decile_means) // 3
        if third == 0:
            return False
        bottom = float(np.mean(self.decile_means[:third]))
        middle = float(np.mean(self.decile_means[third:-third]))
        top = float(np.mean(self.decile_means[-third:]))
        return top > middle > bottom

    def to_dict(self) -> dict:
        return {
            "rank_ic": self.rank_ic,
            "ic_std": self.ic_std,
            "ic_t_stat": self.ic_t_stat,
            "ic_ir": self.ic_ir,
            "decile_spread": self.decile_spread,
            "decile_means": list(self.decile_means),
            "monotone_deciles": self.monotone_deciles,
            "coverage": self.coverage,
            "n_dates": self.n_dates,
            "brier": self.brier,
        }


def _clean(panel: pd.DataFrame) -> pd.DataFrame:
    return panel.dropna(subset=[SCORE_COL, TARGET_COL])


def daily_rank_ic(panel: pd.DataFrame) -> pd.Series:
    """Spearman correlation of score vs forward excess return, per date."""
    ics: dict = {}
    for date, group in _clean(panel).groupby(DATE_COL):
        if len(group) < MIN_NAMES_PER_DATE:
            continue
        if group[SCORE_COL].nunique() < 2 or group[TARGET_COL].nunique() < 2:
            continue
        ics[date] = group[SCORE_COL].rank().corr(group[TARGET_COL].rank())
    return pd.Series(ics, dtype=float)


def decile_means(panel: pd.DataFrame, buckets: int = DECILES) -> tuple[float, ...]:
    """Mean forward excess return by cross-sectional score bucket (low to high)."""
    sums = np.zeros(buckets)
    counts = np.zeros(buckets)
    for _, group in _clean(panel).groupby(DATE_COL):
        if len(group) < buckets:
            continue
        ranks = group[SCORE_COL].rank(method="first")
        bucket = np.minimum(((ranks - 1) * buckets / len(group)).astype(int), buckets - 1)
        for b, value in zip(bucket, group[TARGET_COL]):
            sums[b] += value
            counts[b] += 1
    with np.errstate(invalid="ignore", divide="ignore"):
        means = np.where(counts > 0, sums / counts, np.nan)
    return tuple(float(m) for m in means)


def brier_score(panel: pd.DataFrame) -> float | None:
    """Brier score of ``p_outperform`` against ``fwd_excess > 0``; None if absent."""
    if PROB_COL not in panel.columns:
        return None
    valid = panel.dropna(subset=[PROB_COL, TARGET_COL])
    if valid.empty:
        return None
    outcome = (valid[TARGET_COL] > 0).astype(float)
    return float(((valid[PROB_COL] - outcome) ** 2).mean())


def reliability_curve(panel: pd.DataFrame, bins: int = RELIABILITY_BINS) -> pd.DataFrame:
    """Predicted probability vs observed outperform frequency, per probability bin."""
    if PROB_COL not in panel.columns:
        return pd.DataFrame(columns=["bin", "predicted", "observed", "n"])
    valid = panel.dropna(subset=[PROB_COL, TARGET_COL]).copy()
    valid["bin"] = pd.cut(valid[PROB_COL], np.linspace(0.0, 1.0, bins + 1), include_lowest=True)
    valid["hit"] = (valid[TARGET_COL] > 0).astype(float)
    grouped = valid.groupby("bin", observed=True)
    return pd.DataFrame(
        {"predicted": grouped[PROB_COL].mean(), "observed": grouped["hit"].mean(), "n": grouped.size()}
    ).reset_index()


def evaluate_panel(panel: pd.DataFrame, published: pd.Series | None = None) -> EvalReport:
    """Score a panel with the headline metrics.

    Args:
        panel: Long frame with ``date``, ``score`` and ``fwd_excess`` columns
            (optionally ``p_outperform``).
        published: Optional boolean mask of rows that cleared the publication
            gate, used only for the coverage figure.

    Returns:
        An EvalReport. IC statistics are NaN when no date had enough names.
    """
    ics = daily_rank_ic(panel)
    n = len(ics)
    mean_ic = float(ics.mean()) if n else math.nan
    std_ic = float(ics.std(ddof=1)) if n > 1 else math.nan
    ir = mean_ic / std_ic if n > 1 and std_ic > 0 else math.nan
    t_stat = ir * math.sqrt(n) if not math.isnan(ir) else math.nan

    means = decile_means(panel)
    spread = means[-1] - means[0] if means and not (math.isnan(means[0]) or math.isnan(means[-1])) else math.nan
    coverage = float(published.mean()) if published is not None and len(published) else 1.0

    return EvalReport(
        rank_ic=mean_ic,
        ic_std=std_ic,
        ic_t_stat=t_stat,
        ic_ir=ir,
        decile_spread=spread,
        decile_means=means,
        coverage=coverage,
        n_dates=n,
        brier=brier_score(panel),
    )


def evaluate_scorer(panel: pd.DataFrame, scorer) -> EvalReport:
    """Apply ``scorer(row) -> float`` to every row, then evaluate.

    Args:
        panel: Long frame with ``date`` and ``fwd_excess`` plus whatever
            feature columns the scorer reads.
        scorer: Callable taking a row (Series) and returning a score.
    """
    scored = panel.copy()
    scored[SCORE_COL] = scored.apply(scorer, axis=1)
    return evaluate_panel(scored)


def detector_report_card(events: pd.DataFrame, base_rate: float | None = None) -> pd.DataFrame:
    """Per signal-family report card.

    Args:
        events: One row per signal firing with ``family``, ``direction`` (+1 for
            a bullish vote, -1 bearish) and ``fwd_excess``.
        base_rate: Share of all observations with positive excess. Defaults to
            the events' own rate.

    Returns:
        Frame with n, excess hit rate, lift over base, and mean
        direction-adjusted excess return, weakest families first. Families with
        fewer than ``MIN_FAMILY_SAMPLES`` firings are dropped.
    """
    frame = events.dropna(subset=["fwd_excess"]).copy()
    frame["adj"] = frame["fwd_excess"] * frame["direction"]
    frame["hit"] = frame["adj"] > 0
    if base_rate is None:
        base_rate = float((frame["fwd_excess"] > 0).mean()) if len(frame) else 0.5
    grouped = frame.groupby(["family", "direction"])
    card = pd.DataFrame(
        {
            "n": grouped.size(),
            "excess_hit": grouped["hit"].mean(),
            "mean_dir_adj_excess": grouped["adj"].mean(),
        }
    ).reset_index()
    card["lift"] = card["excess_hit"] - np.where(card["direction"] > 0, base_rate, 1.0 - base_rate)
    card = card[card["n"] >= MIN_FAMILY_SAMPLES]
    return card.sort_values("mean_dir_adj_excess").reset_index(drop=True)
