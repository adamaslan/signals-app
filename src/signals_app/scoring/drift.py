"""Live-vs-backtest IC drift detection (plan P7).

Alert when the live 20-day rank IC has been below half the backtest IC for four
straight weeks — a sustained decay, not one bad week.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import pandas as pd

DRIFT_WEEKS = 4
DRIFT_RATIO = 0.5
MIN_NAMES_PER_DAY = 5


def weekly_live_ic(scored: pd.DataFrame) -> pd.DataFrame:
    """Realized rank IC of ``p_outperform`` against realized excess, per ISO week.

    Args:
        scored: Rows with ``bar_ts``, ``p_outperform`` and ``realized_excess``
            (forward excess return once known).

    Returns:
        Frame ``week_start``, ``live_ic``, ``n_dates``, ``n_names``, oldest first.
        Each day's IC is a cross-section over that day's published names; the
        week is the mean of its daily ICs.
    """
    data = scored.dropna(subset=["p_outperform", "realized_excess"]).copy()
    if data.empty:
        return pd.DataFrame(columns=["week_start", "live_ic", "n_dates", "n_names"])
    data["day"] = pd.to_datetime(data["bar_ts"], utc=True).dt.tz_localize(None).dt.normalize()
    daily: list[dict[str, Any]] = []
    for day, group in data.groupby("day"):
        if len(group) < MIN_NAMES_PER_DAY or group["p_outperform"].nunique() < 2:
            continue
        ic = group["p_outperform"].rank().corr(group["realized_excess"].rank())
        if not math.isnan(ic):
            daily.append({"day": day, "ic": ic, "n": len(group)})
    if not daily:
        return pd.DataFrame(columns=["week_start", "live_ic", "n_dates", "n_names"])
    frame = pd.DataFrame(daily)
    frame["week_start"] = frame["day"] - pd.to_timedelta(frame["day"].dt.weekday, unit="D")
    grouped = frame.groupby("week_start")
    return pd.DataFrame(
        {"live_ic": grouped["ic"].mean(), "n_dates": grouped.size(), "n_names": grouped["n"].sum()}
    ).reset_index()


def drift_alert(
    history: Sequence[dict[str, Any]], weeks: int = DRIFT_WEEKS, ratio: float = DRIFT_RATIO
) -> bool:
    """True when each of the latest ``weeks`` weeks has live IC < ``ratio`` x backtest IC.

    Args:
        history: Rows with ``live_ic`` and ``backtest_ic``, most recent first.
            A week with no measurable live IC counts as *not* drifting (absence
            of data is not evidence of decay).
    """
    recent = list(history)[:weeks]
    if len(recent) < weeks:
        return False
    for row in recent:
        live, backtest = row.get("live_ic"), row.get("backtest_ic")
        if live is None or backtest is None or math.isnan(live) or backtest <= 0:
            return False
        if live >= ratio * backtest:
            return False
    return True
