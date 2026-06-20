"""Signal detection orchestrator.

Runs all detectors with timeout isolation. A single detector failure or
timeout cannot crash the pipeline — each detector runs in its own thread pool.

Ported from gcp-app-w-mcp1/mcp-finance1/src/technical_analysis_mcp/signals.py.
"""
from __future__ import annotations

import logging

import pandas as pd

from signals_app.config import DETECTOR_TIMEOUT_MS, MAX_DETECTOR_FAILURES
from signals_app.detection.base import (
    MutableSignal,
    SignalDetector,
    SignalList,
    _run_detector_with_timeout,
)
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
from signals_app.detection.volume import OBVCMFDetector, VolumeDivergenceDetector, VolumeSignalDetector

logger = logging.getLogger(__name__)


def get_default_detectors() -> list[SignalDetector]:
    """Build and return the default list of all 18 signal detectors.

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
    ]


def detect_all_signals(
    df: pd.DataFrame,
    detectors: list[SignalDetector] | None = None,
    timeout_ms: int = DETECTOR_TIMEOUT_MS,
    max_failures: int = MAX_DETECTOR_FAILURES,
) -> SignalList:
    """Detect all trading signals from indicator data.

    Orchestrates all detectors, isolating each so that a single failure or
    timeout cannot crash the pipeline. Backwards-compatible: returns a
    SignalList (a list subclass) with .degraded and .warnings metadata.

    Args:
        df: DataFrame with calculated indicators (output of compute_indicators).
        detectors: Detectors to run. Defaults to all 18 standard detectors.
        timeout_ms: Per-detector wall-clock budget in milliseconds.
        max_failures: Number of detector failures that marks the result degraded.

    Returns:
        SignalList — a list of MutableSignal objects with .degraded and
        .warnings attributes carrying robustness metadata.
    """
    if detectors is None:
        detectors = get_default_detectors()

    signals: list[MutableSignal] = []
    failure_count = 0
    warnings: list[str] = []

    for detector in detectors:
        name = detector.__class__.__name__
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

    return SignalList(signals, degraded, warnings)
