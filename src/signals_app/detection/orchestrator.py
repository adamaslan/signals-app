"""Signal detection orchestrator.

Runs all detectors with timeout isolation. A single detector failure or
timeout cannot crash the pipeline — each detector runs in its own thread pool.

Ported from gcp-app-w-mcp1/mcp-finance1/src/technical_analysis_mcp/signals.py.
"""
from __future__ import annotations

import logging
import time

import pandas as pd

from signals_app.config import DETECTOR_TIMEOUT_MS, MAX_DETECTOR_FAILURES
from signals_app.detection.base import (
    MutableSignal,
    SignalDetector,
    SignalList,
    _run_detector_with_timeout,
)
from signals_app.detection.events import (
    KumoEventDetector,
    MACDHistTurnDetector,
    PivotReactionDetector,
    RangeBreakoutDetector,
    RSIDivergenceDetector,
    RSIZoneExitDetector,
)
from signals_app.detection.fibonacci import FibonacciDetector, FibonacciVariantDetector
from signals_app.detection.momentum import (
    MACDSignalDetector,
    MultiMACDDetector,
    MultiRSIDetector,
    RSISignalDetector,
    StochasticCrossDetector,
    StochasticSignalDetector,
)
from signals_app.detection.trend import (
    BBExpansionDetector,
    BollingerBandSignalDetector,
    ExpandedMACrossDetector,
    HLProximityDetector,
    IchimokuDetector,
    MADistanceExpandedDetector,
    MovingAverageSignalDetector,
    PriceActionSignalDetector,
    TrendSignalDetector,
)
from signals_app.detection.volume import (
    OBVCMFDetector,
    VolumeDivergenceDetector,
    VolumeSignalDetector,
)
from signals_app.scoring.kinds import stamp_kinds

logger = logging.getLogger(__name__)


def get_default_detectors() -> list[SignalDetector]:
    """Build and return the default list of all 19 signal detectors.

    Returns:
        List of SignalDetector instances covering all signal categories.
    """
    return [
        # Trend
        MovingAverageSignalDetector(),
        ExpandedMACrossDetector(),
        TrendSignalDetector(),
        IchimokuDetector(),
        BollingerBandSignalDetector(),
        BBExpansionDetector(),
        HLProximityDetector(),
        MADistanceExpandedDetector(),
        PriceActionSignalDetector(),
        # Momentum
        RSISignalDetector(),
        MultiRSIDetector(),
        MACDSignalDetector(),
        MultiMACDDetector(),
        StochasticSignalDetector(),
        StochasticCrossDetector(),
        # Volume
        VolumeSignalDetector(),
        VolumeDivergenceDetector(),
        OBVCMFDetector(),
        # Structure
        FibonacciDetector(),
    ]


def get_experimental_detectors() -> list[SignalDetector]:
    """Event detectors that exist to be measured, not yet to vote.

    Kept out of ``get_default_detectors()`` on purpose: ``ConfluenceRanker`` and
    ``FamilyConfluenceRanker`` vote on any directional signal, so registering
    these there would change production scores before the evaluator has earned
    them any weight. Only the graded ranker (evidence E = 0 until earned) and
    detector-hit storage consume this list.
    """
    return [
        KumoEventDetector(),
        RangeBreakoutDetector(),
        RSIZoneExitDetector(),
        RSIDivergenceDetector(),
        MACDHistTurnDetector(),
        PivotReactionDetector(),
        FibonacciVariantDetector(),
    ]


def resolve_contradictions(signals: list[MutableSignal]) -> list[MutableSignal]:
    """Drop readings that a stronger reading on the same bar already contradicts.

    ``AT UPPER BB`` (a bearish proximity read) also matches a close *above* the
    band, so on a breakout bar one stock cast -1 and +3 in the same family.
    A fresh band breach supersedes the "at the edge" proximity reading.

    Args:
        signals: Stamped signals for one bar (``stamp_kinds`` already run).

    Returns:
        The signals with superseded proximity readings removed.
    """
    if not any(s.concept == "bb_breach" for s in signals):
        return signals
    return [s for s in signals if s.concept != "bb_edge"]


def detect_all_signals(
    df: pd.DataFrame,
    detectors: list[SignalDetector] | None = None,
    timeout_ms: int = DETECTOR_TIMEOUT_MS,
    max_failures: int = MAX_DETECTOR_FAILURES,
    include_experimental: bool = False,
) -> SignalList:
    """Detect all trading signals from indicator data.

    Orchestrates all detectors, isolating each so that a single failure or
    timeout cannot crash the pipeline. Backwards-compatible: returns a
    SignalList (a list subclass) with .degraded and .warnings metadata.

    Args:
        df: DataFrame with calculated indicators (output of compute_indicators).
        detectors: Detectors to run. Defaults to all 19 standard detectors.
        timeout_ms: Per-detector wall-clock budget in milliseconds.
        max_failures: Number of detector failures that marks the result degraded.
        include_experimental: Also run ``get_experimental_detectors()``. Off by
            default so every production ranker sees exactly the signals it did
            before; ignored when ``detectors`` is passed explicitly.

    Returns:
        SignalList — a list of MutableSignal objects with .degraded and
        .warnings attributes carrying robustness metadata.
    """
    if detectors is None:
        detectors = get_default_detectors()
        if include_experimental:
            detectors = [*detectors, *get_experimental_detectors()]

    signals: list[MutableSignal] = []
    failure_count = 0
    warnings: list[str] = []
    timings_ms: dict[str, float] = {}

    for detector in detectors:
        name = detector.__class__.__name__
        started = time.perf_counter()
        try:
            detected = _run_detector_with_timeout(detector, df, timeout_ms)
            signals.extend(detected)
            logger.debug("Detector %s found %d signals", name, len(detected))
        except TimeoutError:
            failure_count += 1
            warnings.append(f"detector_timeout:{name}")
            logger.warning("Detector %s timed out after %d ms", name, timeout_ms)
        except Exception as exc:
            failure_count += 1
            warnings.append(f"detector_error:{name}:{type(exc).__name__}")
            logger.warning("Detector %s failed: %s", name, exc)
        finally:
            timings_ms[name] = round((time.perf_counter() - started) * 1000, 2)

    stamp_kinds(signals)
    signals = resolve_contradictions(signals)
    degraded = failure_count >= max_failures

    if degraded:
        logger.error(
            "%d/%d detectors failed — marking result degraded",
            failure_count,
            len(detectors),
        )

    logger.info(
        "Detected %d total signals (%d detector failures, degraded=%s)",
        len(signals),
        failure_count,
        degraded,
    )

    slow_threshold_ms = timeout_ms * 0.8
    slow_detectors = {n: t for n, t in timings_ms.items() if t >= slow_threshold_ms}
    if slow_detectors:
        logger.warning(
            "%d detector(s) used >=80%% of the %dms timeout budget: %s",
            len(slow_detectors), timeout_ms, slow_detectors,
        )

    return SignalList(signals, degraded, warnings, timings_ms)
