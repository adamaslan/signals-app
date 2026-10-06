"""Evidence table E: how much a signal label has earned the right to vote.

``E`` is a multiplier in [0, 1] applied by the graded ranker on top of
``base x K(kind)``. Lookup order for a signal, most specific first:

    label + regime  ->  label  ->  concept  ->  table default

The committed baseline sets the default to 1.0 (signals that vote in production
today keep voting) and writes explicit zeros for everything that is new or was
measured at or below baseline. ``scripts/eval_signals.py`` writes a dated file
with measured values; a label never gets E > 0 from a guess.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from signals_app.config import KIND_MULTIPLIER
from signals_app.detection.base import MutableSignal

EVIDENCE_DIR: Final[Path] = Path(__file__).resolve().parents[3] / "calibration" / "evidence"
BASELINE_FILENAME: Final[str] = "evidence-baseline.json"


class EvidenceError(ValueError):
    """Raised when an evidence file is malformed."""


@dataclass(frozen=True)
class EvidenceTable:
    """Immutable E lookup; see module docstring for resolution order."""

    version: str
    default: float = 1.0
    labels: dict[str, float] = field(default_factory=dict)
    concepts: dict[str, float] = field(default_factory=dict)
    regimes: dict[str, dict[str, float]] = field(default_factory=dict)
    kind_multiplier: dict[str, float] = field(default_factory=lambda: dict(KIND_MULTIPLIER))

    def e_for(self, signal: MutableSignal, regime: str | None = None) -> float:
        """Evidence factor for one signal, optionally conditioned on the regime."""
        by_regime = self.regimes.get(signal.signal)
        if regime is not None and by_regime and regime in by_regime:
            return by_regime[regime]
        if signal.signal in self.labels:
            return self.labels[signal.signal]
        if signal.concept is not None and signal.concept in self.concepts:
            return self.concepts[signal.concept]
        return self.default


def _check_unit(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
        raise EvidenceError(f"{name} must be a number in [0, 1], got {value!r}")
    return float(value)


def parse_evidence(raw: dict[str, Any]) -> EvidenceTable:
    """Validate a decoded evidence document and build the table.

    Raises:
        EvidenceError: on a missing version, an out-of-range E, or a K that
            does not cover every signal kind.
    """
    if not isinstance(raw.get("version"), str) or not raw["version"]:
        raise EvidenceError("evidence file needs a non-empty string 'version'")
    labels = {k: _check_unit(f"labels[{k}]", v["E"] if isinstance(v, dict) else v)
              for k, v in raw.get("labels", {}).items()}
    concepts = {k: _check_unit(f"concepts[{k}]", v["E"] if isinstance(v, dict) else v)
                for k, v in raw.get("concepts", {}).items()}
    regimes = {
        label: {r: _check_unit(f"regimes[{label}][{r}]", e) for r, e in by.items()}
        for label, by in raw.get("regimes", {}).items()
    }
    kinds = dict(KIND_MULTIPLIER)
    if "K" in raw:
        kinds = {k: float(v) for k, v in raw["K"].items() if k in KIND_MULTIPLIER}
        if set(kinds) != set(KIND_MULTIPLIER):
            raise EvidenceError(f"K must cover exactly {sorted(KIND_MULTIPLIER)}")
    return EvidenceTable(
        version=raw["version"],
        default=_check_unit("default_E", raw.get("default_E", 1.0)),
        labels=labels,
        concepts=concepts,
        regimes=regimes,
        kind_multiplier=kinds,
    )


def load_evidence(path: Path | None = None) -> EvidenceTable:
    """Load an evidence file (the committed baseline when ``path`` is None)."""
    target = path or EVIDENCE_DIR / BASELINE_FILENAME
    try:
        raw = json.loads(target.read_text())
    except FileNotFoundError as exc:
        raise EvidenceError(f"evidence file not found: {target}") from exc
    except json.JSONDecodeError as exc:
        raise EvidenceError(f"evidence file is not valid JSON: {target}: {exc}") from exc
    return parse_evidence(raw)
