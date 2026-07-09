"""Detection base types.

Provides the SignalDetector Protocol, MutableSignal, SignalList (with .degraded
and .warnings metadata), and _run_detector_with_timeout().

Ported exactly from gcp-app-w-mcp1/mcp-finance1/src/technical_analysis_mcp/signals.py
and models.py.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any, Protocol

import pandas as pd
from pydantic import BaseModel

from signals_app.config import SignalCategory, SignalStrength

logger = logging.getLogger(__name__)


class MutableSignal(BaseModel):
    """Mutable signal built during detection, before promotion to the immutable schema.

    Attributes:
        signal: Short label for the signal (e.g., "GOLDEN CROSS").
        description: Human-readable description of what triggered it.
        strength: Strength classification from SignalStrength enum.
        category: Category classification from SignalCategory enum.
        ai_score: Optional AI-assigned score (1–100).
        ai_reasoning: Optional AI explanation.
        rank: Optional rank position in a sorted signal list.
    """

    signal: str
    description: str
    strength: str
    category: str
    ai_score: int | None = None
    ai_reasoning: str | None = None
    rank: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to plain dictionary for serialization.

        Returns:
            Dictionary representation.
        """
        return {
            "signal": self.signal,
            "description": self.description,
            "strength": self.strength,
            "category": self.category,
            "ai_score": self.ai_score,
            "ai_reasoning": self.ai_reasoning,
            "rank": self.rank,
        }


class SignalDetector(Protocol):
    """Protocol for signal detection strategies.

    Any object with a detect() method matching this signature
    is a valid SignalDetector — no inheritance required.
    """

    def detect(self, df: pd.DataFrame) -> list[MutableSignal]:
        """Detect signals from indicator data.

        Args:
            df: DataFrame with calculated indicators (output of compute_indicators).

        Returns:
            List of detected MutableSignal objects.
        """
        ...


class SignalList(list):
    """list[MutableSignal] with robustness metadata.

    Fully backwards-compatible with callers that treat the return value of
    detect_all_signals() as a plain list. New callers can inspect .degraded
    and .warnings for pipeline health information.

    Attributes:
        degraded: True when >= MAX_DETECTOR_FAILURES detectors failed.
        warnings: List of per-detector failure messages.
        timings_ms: Per-detector wall-clock time in milliseconds — observability
            hook for spotting a slow/regressing detector before it eats the
            per-request timeout budget.
    """

    degraded: bool
    warnings: list[str]
    timings_ms: dict[str, float]

    def __init__(
        self,
        signals: list[MutableSignal],
        degraded: bool,
        warnings: list[str],
        timings_ms: dict[str, float] | None = None,
    ) -> None:
        """Initialize SignalList.

        Args:
            signals: Detected signals from all detectors.
            degraded: Whether too many detectors failed.
            warnings: Per-failure warning messages.
            timings_ms: Per-detector elapsed time in milliseconds, keyed by
                detector class name. Defaults to empty for backwards compat
                with callers constructing SignalList directly.
        """
        super().__init__(signals)
        self.degraded = degraded
        self.warnings = warnings
        self.timings_ms = timings_ms if timings_ms is not None else {}


def _run_detector_with_timeout(
    detector: SignalDetector,
    df: pd.DataFrame,
    timeout_ms: int,
) -> list[MutableSignal]:
    """Run a single detector with a wall-clock timeout using a thread pool.

    SIGALRM is process-wide and not safe in concurrent async environments —
    concurrent requests would overwrite each other's timers, and signal.signal
    can only be called from the main thread. ThreadPoolExecutor.submit gives
    a clean future we can wait on with a timeout, safe across threads and
    asyncio tasks.

    Args:
        detector: SignalDetector instance to run.
        df: Indicator DataFrame to pass to the detector.
        timeout_ms: Wall-clock budget in milliseconds.

    Returns:
        List of MutableSignal objects from the detector.

    Raises:
        TimeoutError: If the detector exceeds timeout_ms.
    """
    name = detector.__class__.__name__
    timeout_s = timeout_ms / 1000.0

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(detector.detect, df)
        try:
            return future.result(timeout=timeout_s)
        except FuturesTimeoutError:
            raise TimeoutError(f"Detector {name} exceeded {timeout_ms}ms")
