"""Which ranker makes the production BUY/HOLD/SELL call.

``SIGNALS_RANKER`` selects it. The default, ``production``, is the existing
``ConfluenceRanker`` and is byte-identical to before this module existed.
``graded`` switches to the graded ranker, and it refuses to start unless
``SIGNALS_THRESHOLDS_FILE`` names a thresholds file derived from shadow data:
the graded score has a different scale, so borrowing the production cut-offs
would silently change what gets published (spec §8.3 P7).
"""
from __future__ import annotations

from pathlib import Path
from typing import Final, Protocol

import pandas as pd

from signals_app.config import PUBLISH_MIN_CONFLUENCE_SCORE
from signals_app.detection.base import MutableSignal
from signals_app.detection.orchestrator import detect_all_signals, get_experimental_detectors
from signals_app.scoring.confluence import ConfluenceRanker, ConfluenceResult
from signals_app.scoring.evidence import load_evidence
from signals_app.scoring.graded import GradedConfluenceRanker
from signals_app.scoring.thresholds import GradedThresholds, load_thresholds

MODE_PRODUCTION: Final[str] = "production"
MODE_GRADED: Final[str] = "graded"
VALID_MODES: Final[tuple[str, ...]] = (MODE_PRODUCTION, MODE_GRADED)


class RankerConfigError(RuntimeError):
    """The selected ranker cannot be built from the current configuration."""


class ProductionRanker(Protocol):
    """What a scan or an analyze call needs from the ranker."""

    publish_min_score: float

    def rank_signals(
        self,
        signals: list[MutableSignal],
        strength_hit_rates: dict[str, float] | None = None,
        regime: str | None = None,
        df: pd.DataFrame | None = None,
    ) -> ConfluenceResult: ...


class LegacyRanker:
    """The existing ``ConfluenceRanker`` behind the common signature."""

    publish_min_score: float = PUBLISH_MIN_CONFLUENCE_SCORE

    def rank_signals(
        self,
        signals: list[MutableSignal],
        strength_hit_rates: dict[str, float] | None = None,
        regime: str | None = None,
        df: pd.DataFrame | None = None,
    ) -> ConfluenceResult:
        """Rank with ``ConfluenceRanker``; ``regime`` and ``df`` are not used."""
        return ConfluenceRanker().rank_signals(signals, strength_hit_rates=strength_hit_rates)


class GradedProductionRanker:
    """The graded ranker, fed the experimental events its evidence table scores."""

    def __init__(self, ranker: GradedConfluenceRanker, thresholds: GradedThresholds) -> None:
        self._ranker = ranker
        self.publish_min_score = thresholds.publish_min

    def rank_signals(
        self,
        signals: list[MutableSignal],
        strength_hit_rates: dict[str, float] | None = None,
        regime: str | None = None,
        df: pd.DataFrame | None = None,
    ) -> ConfluenceResult:
        """Rank ``signals`` plus the experimental events detected on ``df``.

        ``strength_hit_rates`` is accepted for signature parity and ignored:
        confidence here comes from the score, not a strength calibration table.
        """
        extra = detect_all_signals(df, get_experimental_detectors()) if df is not None else []
        return self._ranker.rank_signals([*signals, *extra], regime=regime, df=df)


def build_production_ranker(
    mode: str = MODE_PRODUCTION,
    thresholds_file: str | None = None,
    evidence_file: str | None = None,
) -> ProductionRanker:
    """Build the ranker for ``mode``.

    Raises:
        RankerConfigError: unknown mode, or ``graded`` without a valid
            thresholds file. Never falls back silently: a deploy that asked for
            the graded ranker and got the old one would be worse than failing.
    """
    if mode not in VALID_MODES:
        raise RankerConfigError(f"SIGNALS_RANKER must be one of {VALID_MODES}, got {mode!r}")
    if mode == MODE_PRODUCTION:
        return LegacyRanker()
    if not thresholds_file:
        raise RankerConfigError(
            "SIGNALS_RANKER=graded needs SIGNALS_THRESHOLDS_FILE: thresholds must be derived "
            "from shadow data (scripts/graded_shadow.py thresholds), not guessed"
        )
    try:
        thresholds = load_thresholds(Path(thresholds_file))
        evidence = load_evidence(Path(evidence_file) if evidence_file else None)
    except ValueError as exc:  # ThresholdsError and EvidenceError are both ValueErrors
        raise RankerConfigError(str(exc)) from exc
    return GradedProductionRanker(GradedConfluenceRanker(evidence, thresholds), thresholds)
