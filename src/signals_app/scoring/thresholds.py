"""Decision thresholds for the graded ranker, kept apart from the score.

The graded score lives on a different scale from the production one (it is a
mean of per-family nets, not a weighted vote sum), so the production BUY/SELL
cut-offs cannot be reused. Thresholds are *data*: derived from shadow runs so
the BUY rate stays comparable to today's (``scripts/graded_shadow.py
thresholds``), and loaded from a file. ``DEFAULT_THRESHOLDS`` only drives the
shadow ranker, which never decides anything.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

# The family ranker's starting values, untuned (scoring/confluence.py).
SHADOW_BUY_THRESHOLD: Final[float] = 0.20
SHADOW_SELL_THRESHOLD: Final[float] = -0.20
SHADOW_MIN_AGREEING: Final[int] = 3
SHADOW_MAX_OPPOSING: Final[int] = 1


class ThresholdsError(ValueError):
    """Raised for a missing or invalid thresholds file."""


@dataclass(frozen=True)
class GradedThresholds:
    """Cut-offs that turn a graded score into BUY / SELL / HOLD and a publish gate."""

    version: str
    buy: float
    sell: float
    min_agreeing: int
    max_opposing: int
    publish_min: float
    derived_from: str = "constants"

    def __post_init__(self) -> None:
        if not self.buy > 0 or not self.sell < 0:
            raise ThresholdsError(f"buy must be > 0 and sell < 0, got {self.buy}, {self.sell}")
        if not self.publish_min > 0:
            raise ThresholdsError(f"publish_min must be > 0, got {self.publish_min}")
        if self.min_agreeing < 1 or self.max_opposing < 0:
            raise ThresholdsError("min_agreeing must be >= 1 and max_opposing >= 0")


DEFAULT_THRESHOLDS: Final[GradedThresholds] = GradedThresholds(
    version="shadow-defaults",
    buy=SHADOW_BUY_THRESHOLD,
    sell=SHADOW_SELL_THRESHOLD,
    min_agreeing=SHADOW_MIN_AGREEING,
    max_opposing=SHADOW_MAX_OPPOSING,
    publish_min=SHADOW_BUY_THRESHOLD,
)


def parse_thresholds(raw: dict[str, Any]) -> GradedThresholds:
    """Build thresholds from a decoded document.

    Raises:
        ThresholdsError: on a missing key, a non-number, or an invalid value.
    """
    try:
        return GradedThresholds(
            version=str(raw["version"]),
            buy=float(raw["buy"]),
            sell=float(raw["sell"]),
            min_agreeing=int(raw["min_agreeing"]),
            max_opposing=int(raw["max_opposing"]),
            publish_min=float(raw["publish_min"]),
            derived_from=str(raw.get("derived_from", "unknown")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ThresholdsError):
            raise
        raise ThresholdsError(f"invalid thresholds document: {exc!r}") from exc


def load_thresholds(path: Path) -> GradedThresholds:
    """Load a thresholds file written by ``graded_shadow.py thresholds``."""
    try:
        return parse_thresholds(json.loads(path.read_text()))
    except FileNotFoundError as exc:
        raise ThresholdsError(f"thresholds file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ThresholdsError(f"thresholds file is not valid JSON: {path}: {exc}") from exc
