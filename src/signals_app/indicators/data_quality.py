"""Data-quality scoring — a first-class guard on the OHLCV feeding detectors.

Per signal-multiplication-analysis.md pillar 1: "signals are only as good as
the OHLCV feeding them." This turns staleness/NaN checks into a single
0.0-1.0 score plus human-readable reasons, attached to every SignalOutput
rather than silently ignored.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from signals_app.config import (
    DATA_QUALITY_MAX_NAN_RATIO,
    DATA_QUALITY_STALE_HOURS,
    MIN_BARS_BY_PERIOD,
)

logger = logging.getLogger(__name__)

_OHLCV_COLUMNS = ("Open", "High", "Low", "Close", "Volume")


@dataclass(frozen=True)
class DataQualityResult:
    """Result of scoring one OHLCV window."""

    score: float
    reasons: list[str] = field(default_factory=list)


def score_data_quality(df: pd.DataFrame, period: str) -> DataQualityResult:
    """Score an OHLCV DataFrame's quality on a 0.0-1.0 scale.

    Deducts for: too few bars for the requested period, a NaN ratio above
    threshold in OHLCV columns, and a stale last bar. Deductions are additive
    and independent — multiple problems compound, floored at 0.0.

    Args:
        df: Raw OHLCV DataFrame with DatetimeIndex, oldest-first.
        period: The yfinance period string this df was fetched for — used to
            look up the expected minimum bar count.

    Returns:
        DataQualityResult with a score in [0.0, 1.0] and the reasons for any
        deduction (empty list when score is 1.0).
    """
    score = 1.0
    reasons: list[str] = []

    if len(df) == 0:
        return DataQualityResult(score=0.0, reasons=["empty_dataframe"])

    min_bars = MIN_BARS_BY_PERIOD.get(period, 20)
    if len(df) < min_bars:
        score -= 0.4
        reasons.append(f"insufficient_bars:{len(df)}<{min_bars}")

    present_cols = [c for c in _OHLCV_COLUMNS if c in df.columns]
    if present_cols:
        nan_ratio = df[present_cols].isna().mean().mean()
        if nan_ratio > DATA_QUALITY_MAX_NAN_RATIO:
            score -= 0.4
            reasons.append(f"nan_ratio:{nan_ratio:.3f}>{DATA_QUALITY_MAX_NAN_RATIO}")

    last_ts = df.index[-1]
    try:
        last_dt = last_ts.to_pydatetime() if hasattr(last_ts, "to_pydatetime") else last_ts
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600.0
        if age_hours > DATA_QUALITY_STALE_HOURS:
            score -= 0.3
            reasons.append(f"stale_last_bar:{age_hours:.1f}h>{DATA_QUALITY_STALE_HOURS}h")
    except (TypeError, ValueError, AttributeError) as exc:
        score -= 0.3
        reasons.append(f"unparsable_last_bar_timestamp:{exc}")

    score = max(0.0, round(score, 4))
    if reasons:
        logger.info("data_quality: score=%.2f reasons=%s", score, reasons)
    return DataQualityResult(score=score, reasons=reasons)
