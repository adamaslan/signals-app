"""Backtest engine — scores historical signals against realized forward returns.

Turns detector output into a calibrated hit-rate, closing the "confidence
calibration" gap from the signal-multiplication-analysis doc: a HIGH confidence
label should mean a measurably higher hit-rate, not just a label. Consumes the
output of detection.historical.scan_historical().
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import pandas as pd

from signals_app.config import BACKTEST_FORWARD_HORIZON_DAYS
from signals_app.detection.historical import BarSignals

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HitRateBucket:
    """Aggregate hit-rate for one grouping key (category, strength, etc.)."""

    key: str
    hits: int
    total: int

    @property
    def hit_rate(self) -> float:
        return self.hits / self.total if self.total else 0.0


def _is_bullish(strength: str) -> bool:
    return "BULLISH" in strength


def _is_bearish(strength: str) -> bool:
    return "BEARISH" in strength


def score_historical_signals(
    df: pd.DataFrame,
    bar_signals: list[BarSignals],
    horizon_days: int = BACKTEST_FORWARD_HORIZON_DAYS,
) -> dict[str, list[HitRateBucket]]:
    """Score every historical signal against its realized forward return.

    A signal is a "hit" when its direction agrees with the sign of the forward
    return over `horizon_days` bars. Signals within `horizon_days` of the end
    of `df` are skipped since their outcome isn't known yet. NEUTRAL signals
    carry no directional claim and are not scored.

    Args:
        df: The same indicator-computed DataFrame passed to scan_historical
            (needed to look up prices beyond each bar's own snapshot).
        bar_signals: Output of detection.historical.scan_historical.
        horizon_days: How many bars ahead to measure the realized return.

    Returns:
        Dict with "by_category" and "by_strength" hit-rate bucket lists.
    """
    index_pos = {ts: pos for pos, ts in enumerate(df.index)}
    by_category: dict[str, list[bool]] = {}
    by_strength: dict[str, list[bool]] = {}

    skipped_unresolved = 0
    for bar in bar_signals:
        pos = index_pos.get(bar.date)
        if pos is None or pos + horizon_days >= len(df):
            skipped_unresolved += 1
            continue
        forward_close = float(df.iloc[pos + horizon_days]["Close"])
        # bool(nan) is True in Python, so an `if bar.close` guard would let NaN
        # closes through and silently score every signal there as a miss.
        if math.isnan(forward_close) or math.isnan(bar.close) or bar.close == 0.0:
            skipped_unresolved += 1
            continue
        forward_return = (forward_close - bar.close) / bar.close

        for sig in bar.signals:
            if _is_bullish(sig.strength):
                hit = forward_return > 0
            elif _is_bearish(sig.strength):
                hit = forward_return < 0
            else:
                continue

            by_category.setdefault(sig.category, []).append(hit)
            by_strength.setdefault(sig.strength, []).append(hit)

    logger.info(
        "score_historical_signals: scored %d bars, skipped %d (unresolved horizon)",
        len(bar_signals) - skipped_unresolved,
        skipped_unresolved,
    )

    return {
        "by_category": [
            HitRateBucket(key=k, hits=sum(v), total=len(v)) for k, v in sorted(by_category.items())
        ],
        "by_strength": [
            HitRateBucket(key=k, hits=sum(v), total=len(v)) for k, v in sorted(by_strength.items())
        ],
    }
