"""Historical bar-by-bar signal scanning.

Runs the existing detector pipeline against every historical bar, not just the
latest one — the "multiply signals" doc's core prescription
(`for i in range(lookback, len(df))`). Indicators only need to be computed once
on the full dataframe: every indicator in indicators/compute.py is rolling- or
ewm-based (strictly backward-looking, verified no `.shift(-n)` forward shifts),
so df.iloc[i] is already correct as of bar i with no lookahead bias. Each bar's
signals then come from slicing df.iloc[:i+1] and reusing the unmodified
per-bar detectors, so detector logic itself never changes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from signals_app.config import (
    DETECTOR_TIMEOUT_MS,
    MAX_DETECTOR_FAILURES,
    MIN_HISTORICAL_LOOKBACK,
)
from signals_app.detection.base import MutableSignal, SignalDetector
from signals_app.detection.orchestrator import detect_all_signals, get_default_detectors

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BarSignals:
    """Signals detected as of one historical bar."""

    date: pd.Timestamp
    close: float
    signals: list[MutableSignal]
    degraded: bool


def scan_historical(
    df: pd.DataFrame,
    detectors: list[SignalDetector] | None = None,
    min_lookback: int = MIN_HISTORICAL_LOOKBACK,
    timeout_ms: int = DETECTOR_TIMEOUT_MS,
    max_failures: int = MAX_DETECTOR_FAILURES,
) -> list[BarSignals]:
    """Run all detectors against every historical bar from min_lookback onward.

    Args:
        df: DataFrame with indicators already computed (output of
            compute_indicators), DatetimeIndex sorted oldest-first.
        detectors: Detectors to run per bar. Defaults to all 18 standard detectors.
        min_lookback: Skip bars before this index — detectors need warmup history
            (longest indicator period) to avoid NaN-driven false signals.
        timeout_ms: Per-detector wall-clock budget in milliseconds, per bar.
        max_failures: Detector failure count that marks a bar's result degraded.

    Returns:
        One BarSignals entry per scanned bar, oldest-first.
    """
    if detectors is None:
        detectors = get_default_detectors()
    if len(df) <= min_lookback:
        logger.warning(
            "scan_historical: df has %d rows, below min_lookback=%d — nothing to scan",
            len(df),
            min_lookback,
        )
        return []

    results: list[BarSignals] = []
    for i in range(min_lookback, len(df)):
        window = df.iloc[: i + 1]
        signal_list = detect_all_signals(
            window, detectors=detectors, timeout_ms=timeout_ms, max_failures=max_failures
        )
        results.append(
            BarSignals(
                date=window.index[-1],
                close=float(window.iloc[-1]["Close"]),
                signals=list(signal_list),
                degraded=signal_list.degraded,
            )
        )

    logger.info(
        "scan_historical: scanned %d bars (of %d total, min_lookback=%d)",
        len(results),
        len(df),
        min_lookback,
    )
    return results
