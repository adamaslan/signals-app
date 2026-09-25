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
    # How many of ``total`` were bullish calls (the rest bearish). Lets a
    # caller compute the bucket's own chance baseline from the up-rate:
    # a bucket of all-bullish calls in a rising market should hit often
    # by default, so its hit-rate alone proves nothing.
    bullish: int = 0

    @property
    def hit_rate(self) -> float:
        return self.hits / self.total if self.total else 0.0


# Key of the single bucket in the "baseline" list: hits = scored bars whose
# forward return was positive, total = scored bars. Kept as a HitRateBucket so
# it merges across symbols with merge_hit_rate_buckets like every other list.
BASELINE_UP_KEY = "UP"


def _is_bullish(strength: str) -> bool:
    return "BULLISH" in strength


def _is_bearish(strength: str) -> bool:
    return "BEARISH" in strength


def _benchmark_forward_return(
    benchmark_close: pd.Series, start: pd.Timestamp, end: pd.Timestamp
) -> float | None:
    """Benchmark return between two dates (last close at or before each), or None."""
    start_pos = benchmark_close.index.searchsorted(start, side="right") - 1
    end_pos = benchmark_close.index.searchsorted(end, side="right") - 1
    if start_pos < 0 or end_pos <= start_pos:
        return None
    b0 = float(benchmark_close.iloc[start_pos])
    b1 = float(benchmark_close.iloc[end_pos])
    if math.isnan(b0) or math.isnan(b1) or b0 == 0.0:
        return None
    return (b1 - b0) / b0


def excess_target(
    excess_return: float, realized_vol: float, horizon_days: int
) -> float | None:
    """Vol-scaled excess return: excess / (daily vol * sqrt(horizon)).

    Stops high-beta names (TQQQ, TSLA) dominating a pooled target.

    Args:
        excess_return: Symbol forward return minus benchmark forward return.
        realized_vol: Trailing daily-return standard deviation.
        horizon_days: Forward horizon in bars.

    Returns:
        The scaled target, or None when the volatility is unusable.
    """
    if math.isnan(realized_vol) or realized_vol <= 0.0:
        return None
    return excess_return / (realized_vol * math.sqrt(horizon_days))


def score_historical_signals(
    df: pd.DataFrame,
    bar_signals: list[BarSignals],
    horizon_days: int = BACKTEST_FORWARD_HORIZON_DAYS,
    benchmark_df: pd.DataFrame | None = None,
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
        benchmark_df: Optional benchmark (e.g. SPY) OHLCV frame. When given,
            "by_category"/"by_strength" score a hit against *excess* return
            (symbol minus benchmark), so market beta no longer earns credit;
            bars with no benchmark coverage are skipped. Without it, the raw
            sign of the forward return is used (legacy label).

    Returns:
        Dict with "by_category", "by_strength" and "by_signal" (detector
        signal name, e.g. "GOLDEN CROSS") hit-rate bucket lists, plus
        "by_strength_raw" (always the legacy raw-sign label, for comparison)
        and "baseline" — one ``BASELINE_UP_KEY`` bucket counting how many
        scored bars rose over the horizon, the chance rate every directional
        bucket has to beat.
    """
    index_pos = {ts: pos for pos, ts in enumerate(df.index)}
    by_category: dict[str, list[bool]] = {}
    by_strength: dict[str, list[bool]] = {}
    by_signal: dict[str, list[bool]] = {}
    by_strength_raw: dict[str, list[bool]] = {}
    # Bullish-call counts per bucket key, parallel to the hit lists above.
    bull_category: dict[str, int] = {}
    bull_strength: dict[str, int] = {}
    bull_signal: dict[str, int] = {}
    up_bars = 0
    scored_bars = 0
    benchmark_close = benchmark_df["Close"] if benchmark_df is not None else None

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

        label_return = forward_return
        if benchmark_close is not None:
            bench_return = _benchmark_forward_return(
                benchmark_close, bar.date, df.index[pos + horizon_days]
            )
            if bench_return is None:
                skipped_unresolved += 1
                continue
            label_return = forward_return - bench_return

        scored_bars += 1
        if label_return > 0:
            up_bars += 1

        for sig in bar.signals:
            if _is_bullish(sig.strength):
                bullish = True
                hit = label_return > 0
                raw_hit = forward_return > 0
            elif _is_bearish(sig.strength):
                bullish = False
                hit = label_return < 0
                raw_hit = forward_return < 0
            else:
                continue

            by_strength_raw.setdefault(sig.strength, []).append(raw_hit)
            by_category.setdefault(sig.category, []).append(hit)
            by_strength.setdefault(sig.strength, []).append(hit)
            by_signal.setdefault(sig.signal, []).append(hit)
            if bullish:
                bull_category[sig.category] = bull_category.get(sig.category, 0) + 1
                bull_strength[sig.strength] = bull_strength.get(sig.strength, 0) + 1
                bull_signal[sig.signal] = bull_signal.get(sig.signal, 0) + 1

    logger.info(
        "score_historical_signals: scored %d bars, skipped %d (unresolved horizon)",
        len(bar_signals) - skipped_unresolved,
        skipped_unresolved,
    )

    return {
        "by_category": _buckets(by_category, bull_category),
        "by_strength": _buckets(by_strength, bull_strength),
        "by_signal": _buckets(by_signal, bull_signal),
        "baseline": [HitRateBucket(key=BASELINE_UP_KEY, hits=up_bars, total=scored_bars)],
        "by_strength_raw": [
            HitRateBucket(key=k, hits=sum(v), total=len(v))
            for k, v in sorted(by_strength_raw.items())
        ],
    }


def _buckets(hits: dict[str, list[bool]], bullish: dict[str, int]) -> list[HitRateBucket]:
    """Collapse per-key hit lists into sorted HitRateBuckets."""
    return [
        HitRateBucket(key=k, hits=sum(v), total=len(v), bullish=bullish.get(k, 0))
        for k, v in sorted(hits.items())
    ]


def bucket_baseline(bucket: HitRateBucket, up_rate: float) -> float:
    """Chance hit-rate for a bucket given its bullish/bearish mix.

    A bullish call "hits" by chance with probability ``up_rate``; a bearish
    call with ``1 - up_rate``. A bucket only shows skill when it beats this
    mix-weighted rate, not a flat 50%.

    Args:
        bucket: The bucket to score.
        up_rate: Fraction of scored bars that rose over the same horizon.

    Returns:
        The expected hit-rate of a direction-blind caller with the same mix.
    """
    if bucket.total == 0:
        return 0.0
    bearish = bucket.total - bucket.bullish
    return (bucket.bullish * up_rate + bearish * (1.0 - up_rate)) / bucket.total


def merge_hit_rate_buckets(bucket_lists: list[list[HitRateBucket]]) -> list[HitRateBucket]:
    """Merge same-key HitRateBuckets from multiple backtest runs (e.g. per symbol).

    Sums hits/total per key rather than averaging hit_rate directly, so a
    100-signal symbol doesn't get the same weight as a 5-signal one.

    Args:
        bucket_lists: Multiple "by_strength" or "by_category" lists to combine.

    Returns:
        One merged HitRateBucket per key, sorted by key.
    """
    hits: dict[str, int] = {}
    totals: dict[str, int] = {}
    bullish: dict[str, int] = {}
    for buckets in bucket_lists:
        for b in buckets:
            hits[b.key] = hits.get(b.key, 0) + b.hits
            totals[b.key] = totals.get(b.key, 0) + b.total
            bullish[b.key] = bullish.get(b.key, 0) + b.bullish
    return [
        HitRateBucket(key=k, hits=hits[k], total=totals[k], bullish=bullish[k])
        for k in sorted(totals)
    ]
