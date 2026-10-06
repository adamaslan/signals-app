"""GradedConfluenceRanker: kind-aware, evidence-weighted confluence.

Spec: docs/states-and-near-a-level-as-signals-2026-10-06.md §6.3 and §8.2.
Runs *beside* ``ConfluenceRanker`` (shadow mode) and changes no production
score. ``FamilyConfluenceRanker`` is not touched either: its output feeds the
learned scorer's ``fam_*`` features, so editing it would silently change a
trained model's inputs.

Pipeline for one bar:
  1. tag every signal with a kind and a concept (``scoring/kinds.py``);
  2. collapse within (family, concept, side): the strongest signal votes, and
     extra agreeing signals add a small capped bonus (SA4, generalized);
  3. points = (base x K(kind) + category bonus for events) x E(evidence);
  4. same-bar volume spike multiplies that bar's event points by 1.25, once;
  5. optional recency decay for events from the two prior bars;
  6. cap standing states (S + P) at 1.0 per family and side;
  7. zero bearish "extension" votes in an uptrend (breakdowns still vote);
  8. net per family, mean over families;
  9. BUY/SELL need family agreement AND a live event with E > 0 on that side;
 10. attach risk context and location, which size and place the call but never vote.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Final

import pandas as pd

from signals_app.config import SignalKind
from signals_app.detection.base import MutableSignal
from signals_app.scoring.confluence import (
    _CATEGORY_BONUS,
    _STRENGTH_BULL_WEIGHT,
    FAMILY_BUY_THRESHOLD,
    FAMILY_MAX_OPPOSING,
    FAMILY_MIN_AGREEING,
    FAMILY_PSEUDO_COUNT,
    FAMILY_SELL_THRESHOLD,
    FAMILY_SIDE_MIN_NET,
    ConfluenceResult,
)
from signals_app.scoring.context import location, risk_context
from signals_app.scoring.evidence import EvidenceTable, load_evidence
from signals_app.scoring.families import FAMILIES, family_of, is_bearish_extension_vote
from signals_app.scoring.kinds import concept_of, kind_of
from signals_app.scoring.regime import TREND_UP

RANKER_VERSION: Final[str] = "graded-1"

VOLUME_AMPLIFIER: Final[float] = 1.25
STATE_BUDGET_PER_SIDE: Final[float] = 1.0
CONCEPT_BONUS_PER_EXTRA: Final[float] = 0.15
CONCEPT_BONUS_CAP: Final[float] = 0.5
# Event points kept from one and two bars ago (spec §8.2 step 5).
EVENT_DECAY_BY_AGE: Final[tuple[float, ...]] = (0.5, 0.25)
MAX_DRIVERS: Final[int] = 5

_EVENT_KINDS: Final[frozenset[str]] = frozenset(
    {SignalKind.EVENT_CROSS.value, SignalKind.EVENT_THRESHOLD.value}
)
_STANDING_KINDS: Final[frozenset[str]] = frozenset(
    {SignalKind.STATE.value, SignalKind.PROXIMITY.value}
)
_VOLUME_CONCEPT: Final[str] = "vol_spike"


@dataclass(frozen=True)
class _Vote:
    """One collapsed, scored vote."""

    signal: str
    family: str
    concept: str
    kind: str
    side: int
    evidence: float
    points: float


@dataclass
class GradedConfluenceResult(ConfluenceResult):
    """``ConfluenceResult`` plus what the graded pipeline adds.

    Attributes:
        events: Fired X/T signals after collapsing (whether or not they vote).
        states: Fired S signals after collapsing.
        proximity: Fired P signals after collapsing.
        live_events: Events with E > 0 that actually voted.
        drivers: Top votes by points, for the card.
        flag_only: Labels that fired but carry E = 0, shown without scoring.
        risk_context: Standing risk readings; never a vote.
        location: Nearest levels in ATR; never a vote.
        ranker_version: Version of this pipeline.
        evidence_version: Version of the evidence table used.
        unclassified: Signals with no kind, which cannot vote.
    """

    events: int = 0
    states: int = 0
    proximity: int = 0
    live_events: int = 0
    drivers: list[dict[str, Any]] = field(default_factory=list)
    flag_only: list[str] = field(default_factory=list)
    risk_context: dict[str, Any] | None = None
    location: dict[str, Any] | None = None
    ranker_version: str = RANKER_VERSION
    evidence_version: str | None = None
    unclassified: int = 0

    def to_dict(self) -> dict:
        """Serialize, extending the base payload."""
        payload = super().to_dict()
        payload.update(
            events=self.events, states=self.states, proximity=self.proximity,
            live_events=self.live_events, drivers=self.drivers, flag_only=self.flag_only,
            risk_context=self.risk_context, location=self.location,
            ranker_version=self.ranker_version, evidence_version=self.evidence_version,
            unclassified=self.unclassified,
        )
        return payload


class GradedConfluenceRanker:
    """Score one bar's signals by kind and evidence. Pure: no I/O in ``rank_signals``."""

    def __init__(self, evidence: EvidenceTable | None = None) -> None:
        self._evidence = evidence if evidence is not None else load_evidence()

    def rank_signals(
        self,
        signals: list[MutableSignal],
        regime: str | None = None,
        prior_bars: list[list[MutableSignal]] | None = None,
        df: pd.DataFrame | None = None,
    ) -> GradedConfluenceResult:
        """Rank signals for the latest bar.

        Args:
            signals: Detector output for the bar (default + experimental).
            regime: Market regime label; ``trend_up`` neutralises bearish
                extension votes and selects regime-conditional evidence.
            prior_bars: Signals from the previous bars, newest first, used only
                for event recency decay. None switches decay off.
            df: Indicator frame, only to attach ``risk_context`` and ``location``.

        Returns:
            GradedConfluenceResult; ``score`` is the mean family net in (-1, 1).
        """
        tally = _Tally()
        votes = self._collapse(self._score(signals, regime, tally))
        tally.fired = votes
        votes = self._amplify(votes, tally.volume_spike)
        votes += self._decayed_events(votes, prior_bars or [], regime)
        votes = self._budget_states(votes)
        return self._finish(votes, signals, regime, tally, df)

    # -- steps ---------------------------------------------------------------

    def _score(
        self, signals: list[MutableSignal], regime: str | None, tally: _Tally,
    ) -> list[_Vote]:
        """Steps 1, 3 and 7: tag, point, and regime-gate each signal."""
        scored: list[_Vote] = []
        for signal in signals:
            kind, concept = kind_of(signal), concept_of(signal)
            if kind is None or concept is None:
                tally.unclassified += 1
                continue
            if kind == SignalKind.CONTEXT.value:
                tally.volume_spike |= concept == _VOLUME_CONCEPT
                continue
            base = _STRENGTH_BULL_WEIGHT.get(signal.strength, 0.0)
            family = family_of(signal)
            if base == 0.0 or family is None:
                continue
            if regime == TREND_UP and is_bearish_extension_vote(signal):
                tally.gated += 1
                continue
            scored.append(self._vote(signal, kind, concept, family, base, regime))
        return scored

    def _vote(self, signal: MutableSignal, kind: str, concept: str, family: str, base: float,
              regime: str | None) -> _Vote:
        evidence = self._evidence.e_for(signal, regime)
        multiplier = self._evidence.kind_multiplier[kind]
        bonus = _CATEGORY_BONUS.get(signal.category, 0.0) if kind in _EVENT_KINDS else 0.0
        # The category bonus is scaled by E too: an unearned event must be inert,
        # not worth +0.5 just for being a MACD signal.
        points = (abs(base) * multiplier + bonus) * evidence
        return _Vote(signal.signal, family, concept, kind, 1 if base > 0 else -1, evidence, points)

    def _collapse(self, scored: list[_Vote]) -> list[_Vote]:
        """Step 2: one vote per (family, concept, side), plus a capped agreement bonus."""
        groups: dict[tuple[str, str, int], list[_Vote]] = defaultdict(list)
        for vote in scored:
            groups[(vote.family, vote.concept, vote.side)].append(vote)
        collapsed: list[_Vote] = []
        for members in groups.values():
            strongest = max(members, key=lambda v: v.points)
            bonus = min(CONCEPT_BONUS_PER_EXTRA * (len(members) - 1), CONCEPT_BONUS_CAP)
            scale = self._evidence.kind_multiplier[strongest.kind] * strongest.evidence
            collapsed.append(_Vote(strongest.signal, strongest.family, strongest.concept,
                                   strongest.kind, strongest.side, strongest.evidence,
                                   strongest.points + bonus * scale))
        return collapsed

    @staticmethod
    def _amplify(votes: list[_Vote], volume_spike: bool) -> list[_Vote]:
        """Step 4: a same-bar volume spike backs this bar's events, once."""
        if not volume_spike:
            return votes
        return [
            _Vote(v.signal, v.family, v.concept, v.kind, v.side, v.evidence,
                  v.points * VOLUME_AMPLIFIER) if v.kind in _EVENT_KINDS else v
            for v in votes
        ]

    def _decayed_events(self, current: list[_Vote], prior_bars: list[list[MutableSignal]],
                        regime: str | None) -> list[_Vote]:
        """Step 5: events from the last bars still count, at a reduced weight."""
        seen = {(v.family, v.concept, v.side) for v in current}
        decayed: list[_Vote] = []
        for age, bar_signals in enumerate(prior_bars[: len(EVENT_DECAY_BY_AGE)]):
            weight = EVENT_DECAY_BY_AGE[age]
            earlier = self._collapse(self._score(bar_signals, regime, _Tally()))
            for vote in earlier:
                key = (vote.family, vote.concept, vote.side)
                if vote.kind not in _EVENT_KINDS or key in seen:
                    continue
                seen.add(key)
                decayed.append(_Vote(vote.signal, vote.family, vote.concept, vote.kind, vote.side,
                                     vote.evidence, vote.points * weight))
        return decayed

    @staticmethod
    def _budget_states(votes: list[_Vote]) -> list[_Vote]:
        """Step 6: S + P points per family and side are capped, scaled proportionally."""
        totals: dict[tuple[str, int], float] = defaultdict(float)
        for vote in votes:
            if vote.kind in _STANDING_KINDS:
                totals[(vote.family, vote.side)] += vote.points
        scaled: list[_Vote] = []
        for vote in votes:
            total = totals.get((vote.family, vote.side), 0.0)
            if vote.kind in _STANDING_KINDS and total > STATE_BUDGET_PER_SIDE:
                factor = STATE_BUDGET_PER_SIDE / total
                vote = _Vote(vote.signal, vote.family, vote.concept, vote.kind, vote.side,
                             vote.evidence, vote.points * factor)
            scaled.append(vote)
        return scaled

    def _finish(self, votes: list[_Vote], signals: list[MutableSignal], regime: str | None,
                tally: _Tally, df: pd.DataFrame | None) -> GradedConfluenceResult:
        bull = {f: 0.0 for f in FAMILIES}
        bear = {f: 0.0 for f in FAMILIES}
        for vote in votes:
            (bull if vote.side > 0 else bear)[vote.family] += vote.points
        nets = {f: (bull[f] - bear[f]) / (bull[f] + bear[f] + FAMILY_PSEUDO_COUNT) for f in FAMILIES}
        score = round(sum(nets.values()) / len(FAMILIES), 4)
        bull_families = sum(1 for n in nets.values() if n >= FAMILY_SIDE_MIN_NET)
        bear_families = sum(1 for n in nets.values() if n <= -FAMILY_SIDE_MIN_NET)
        live = [v for v in votes if v.kind in _EVENT_KINDS and v.points > 0]
        live_side = {v.side for v in live}

        buy = (score >= FAMILY_BUY_THRESHOLD and bull_families >= FAMILY_MIN_AGREEING
               and bear_families <= FAMILY_MAX_OPPOSING and 1 in live_side)
        sell = (score <= FAMILY_SELL_THRESHOLD and bear_families >= FAMILY_MIN_AGREEING
                and bull_families <= FAMILY_MAX_OPPOSING and -1 in live_side)
        action = "BUY" if buy else "SELL" if sell else "HOLD"
        bias = "bullish" if score >= 0.05 else "bearish" if score <= -0.05 else "neutral"
        abs_score = abs(score)
        confidence = "HIGH" if abs_score >= 0.35 else "MEDIUM" if abs_score >= 0.2 else "LOW"

        fired = tally.fired
        voting = [v for v in votes if v.points > 0]
        top = sorted(voting, key=lambda v: v.points, reverse=True)[:MAX_DRIVERS]
        return GradedConfluenceResult(
            score=score, bias=bias, confidence_label=confidence, action=action,
            bull_count=sum(1 for v in voting if v.side > 0),
            bear_count=sum(1 for v in voting if v.side < 0),
            neutral_count=sum(1 for v in votes if v.points == 0),
            total_signals=len(signals),
            bull_weight=round(sum(bull.values()), 3), bear_weight=round(sum(bear.values()), 3),
            max_weight=round(sum(bull.values()) + sum(bear.values()), 3),
            families={f: round(n, 4) for f, n in nets.items()},
            agreeing_families=max(bull_families, bear_families), regime=regime,
            events=sum(1 for v in fired if v.kind in _EVENT_KINDS),
            states=sum(1 for v in fired if v.kind == SignalKind.STATE.value),
            proximity=sum(1 for v in fired if v.kind == SignalKind.PROXIMITY.value),
            live_events=len(live),
            drivers=[{"signal": v.signal, "kind": v.kind, "concept": v.concept,
                      "side": "bull" if v.side > 0 else "bear", "points": round(v.points, 3),
                      "evidence": v.evidence} for v in top],
            flag_only=sorted({v.signal for v in fired if v.evidence == 0.0}),
            risk_context=risk_context(df) if df is not None else None,
            location=location(df) if df is not None else None,
            evidence_version=self._evidence.version, unclassified=tally.unclassified,
        )


@dataclass
class _Tally:
    """Mutable per-call scratch: counters the pipeline steps share."""

    unclassified: int = 0
    gated: int = 0
    volume_spike: bool = False
    fired: list[_Vote] = field(default_factory=list)
