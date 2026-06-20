"""Detection package — L3 of the pipeline.

Protocol-based signal detectors, each isolated with a timeout budget.
"""

from .base import MutableSignal, SignalDetector, SignalList, _run_detector_with_timeout
from .orchestrator import detect_all_signals, get_default_detectors

__all__ = [
    "MutableSignal",
    "SignalDetector",
    "SignalList",
    "_run_detector_with_timeout",
    "detect_all_signals",
    "get_default_detectors",
]
