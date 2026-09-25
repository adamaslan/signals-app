"""Backtest hypotheses — turn what the engine sees *now* into testable claims.

A scan run says "these detectors fired on these tickers today". Each of those
firings carries an implicit claim — "a GOLDEN CROSS on NVDA predicts a rise
over the next 20 bars" — that the historical replay can check. This module
generates those claims as runnable backtest specs (:func:`suggest_hypotheses`)
and scores a finished backtest against them (:func:`evaluate_hypothesis`).

Pure functions only: no fetching, no FastAPI. ``service.suggest_backtests``
does the I/O and calls in here.
"""
from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from backtests.engine import HitRateBucket, bucket_baseline
from signals_app.config import SignalCategory
from signals_app.detection.base import MutableSignal

FocusGroup = Literal["signal", "category", "strength"]
VerdictStatus = Literal["supported", "contradicted", "inconclusive", "no_data"]

# Below this many scored calls a verdict is "inconclusive" whatever the
# interval says — mirrors the frontend's THIN_BUCKET_N.
MIN_VERDICT_SAMPLES = 30
WILSON_Z_95 = 1.96
DEFAULT_BACKTEST_PERIOD = "2y"
DEFAULT_MAX_SUGGESTIONS = 8
# A signal must fire on at least this many tickers to become a cluster claim.
MIN_CLUSTER_TICKERS = 2
# Per-ticker claims are generated for at most this many tickers, strongest first.
MAX_SINGLE_TICKER_CLAIMS = 3
# Clusters are the most common claim on a correlated basket; cap them so the
# other kinds (conflict, category, strength, single) still surface.
MAX_CLUSTER_CLAIMS = 3

# How far ahead a signal's claim reasonably reaches. Oscillator crosses are
# short-lived; MA / trend structure plays out over weeks; volume events fade
# fastest.
_HORIZON_BY_CATEGORY: dict[str, int] = {
    SignalCategory.RSI.value: 10,
    SignalCategory.MACD.value: 10,
    SignalCategory.STOCHASTIC.value: 10,
    SignalCategory.VOLUME.value: 5,
}
_DEFAULT_HORIZON = 20

_STRENGTH_RANK: dict[str, int] = {
    "EXTREME BULLISH": 3,
    "EXTREME BEARISH": 3,
    "STRONG BULLISH": 2,
    "STRONG BEARISH": 2,
    "BULLISH": 1,
    "BEARISH": 1,
}


@dataclass(frozen=True)
class HypothesisFocus:
    """Which bucket of a backtest result a hypothesis is about."""

    group: FocusGroup
    key: str


@dataclass(frozen=True)
class BacktestHypothesis:
    """A runnable backtest spec plus the claim it tests."""

    id: str
    kind: str
    title: str
    rationale: str
    symbols: tuple[str, ...]
    period: str
    horizon_days: int
    focus: tuple[HypothesisFocus, ...]
    priority: float


@dataclass(frozen=True)
class FocusVerdict:
    """How one focus bucket fared against its chance baseline."""

    focus: HypothesisFocus
    status: VerdictStatus
    hits: int
    total: int
    hit_rate: float | None
    lower: float | None
    upper: float | None
    baseline: float | None
    message: str


@dataclass(frozen=True)
class HypothesisVerdict:
    """Overall outcome: the best-supported focus decides the headline."""

    status: VerdictStatus
    focuses: tuple[FocusVerdict, ...]
    message: str


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
def wilson_interval(hits: int, total: int, z: float = WILSON_Z_95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Args:
        hits: Successes.
        total: Trials.
        z: Normal quantile (1.96 → 95%).

    Returns:
        ``(lower, upper)``; ``(0.0, 1.0)`` when ``total`` is 0.
    """
    if total <= 0:
        return 0.0, 1.0
    p = hits / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


# ---------------------------------------------------------------------------
# Suggestion
# ---------------------------------------------------------------------------
def _is_directional(sig: MutableSignal) -> bool:
    return sig.strength in _STRENGTH_RANK


def _direction_word(strength: str) -> str:
    return "rise" if "BULLISH" in strength else "fall"


def horizon_for_category(category: str) -> int:
    """Default forward horizon (bars) for a signal category."""
    return _HORIZON_BY_CATEGORY.get(category, _DEFAULT_HORIZON)


def _hypothesis_id(kind: str, symbols: Sequence[str], focus: Sequence[HypothesisFocus], h: int) -> str:
    raw = "|".join(
        [kind, ",".join(sorted(symbols)), ";".join(f"{f.group}={f.key}" for f in focus), str(h)]
    )
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def _make(
    kind: str,
    title: str,
    rationale: str,
    symbols: Sequence[str],
    focus: Sequence[HypothesisFocus],
    horizon_days: int,
    period: str,
    priority: float,
) -> BacktestHypothesis:
    syms = tuple(sorted(set(symbols)))
    foc = tuple(focus)
    return BacktestHypothesis(
        id=_hypothesis_id(kind, syms, foc, horizon_days),
        kind=kind,
        title=title,
        rationale=rationale,
        symbols=syms,
        period=period,
        horizon_days=horizon_days,
        focus=foc,
        priority=priority,
    )


def _cluster_claims(
    firings: Mapping[str, list[tuple[str, MutableSignal]]],
    basket_size: int,
    period: str,
    horizon_override: int | None,
) -> list[BacktestHypothesis]:
    """One signal firing on several tickers at once → does it pay across them?"""
    out: list[BacktestHypothesis] = []
    for name, hits in firings.items():
        tickers = sorted({t for t, _ in hits})
        if len(tickers) < MIN_CLUSTER_TICKERS:
            continue
        sample = hits[0][1]
        h = horizon_override or horizon_for_category(sample.category)
        out.append(
            _make(
                "cluster",
                f"{name} fired on {len(tickers)} of {basket_size} tickers — does it pay?",
                (
                    f"This run has {name} ({sample.strength.lower()}) live on "
                    f"{', '.join(tickers[:6])}{'…' if len(tickers) > 6 else ''}. "
                    f"If the signal has edge, its past firings on these same tickers "
                    f"should {_direction_word(sample.strength)} over {h} bars more often "
                    f"than chance."
                ),
                tickers,
                [HypothesisFocus("signal", name)],
                h,
                period,
                priority=2.0 + len(tickers) / max(1, basket_size),
            )
        )
    return out


def _single_ticker_claims(
    latest: Mapping[str, Sequence[MutableSignal]],
    period: str,
    horizon_override: int | None,
) -> list[BacktestHypothesis]:
    """Each ticker's strongest live signal → has it ever worked on *this* ticker?"""
    ranked: list[tuple[int, str, MutableSignal]] = []
    for ticker, signals in latest.items():
        directional = [s for s in signals if _is_directional(s)]
        if not directional:
            continue
        best = max(directional, key=lambda s: _STRENGTH_RANK[s.strength])
        ranked.append((_STRENGTH_RANK[best.strength], ticker, best))
    ranked.sort(key=lambda r: (-r[0], r[1]))

    out: list[BacktestHypothesis] = []
    for rank, ticker, sig in ranked[:MAX_SINGLE_TICKER_CLAIMS]:
        h = horizon_override or horizon_for_category(sig.category)
        out.append(
            _make(
                "single",
                f"{ticker}: does {sig.signal} actually work on {ticker}?",
                (
                    f"{ticker}'s strongest live call is {sig.signal} "
                    f"({sig.strength.lower()}). Detectors are tuned market-wide; "
                    f"this checks whether it has called {ticker}'s {h}-bar direction "
                    f"better than chance, on {ticker} alone."
                ),
                [ticker],
                [HypothesisFocus("signal", sig.signal)],
                h,
                period,
                priority=1.0 + rank / 3,
            )
        )
    return out


def _conflict_claims(
    latest: Mapping[str, Sequence[MutableSignal]],
    period: str,
    horizon_override: int | None,
) -> list[BacktestHypothesis]:
    """A ticker with live bullish *and* bearish calls → which side has the edge?"""
    out: list[BacktestHypothesis] = []
    for ticker, signals in sorted(latest.items()):
        bulls = [s for s in signals if _is_directional(s) and "BULLISH" in s.strength]
        bears = [s for s in signals if _is_directional(s) and "BEARISH" in s.strength]
        if not bulls or not bears:
            continue
        bull = max(bulls, key=lambda s: _STRENGTH_RANK[s.strength])
        bear = max(bears, key=lambda s: _STRENGTH_RANK[s.strength])
        h = horizon_override or min(
            horizon_for_category(bull.category), horizon_for_category(bear.category)
        )
        out.append(
            _make(
                "conflict",
                f"{ticker}: {bull.signal} vs {bear.signal} — which side has the edge?",
                (
                    f"{ticker} is sending mixed signals right now. Replaying both on "
                    f"{ticker}'s history shows which one has actually called its "
                    f"{h}-bar direction — a tiebreak the live confluence score can't give."
                ),
                [ticker],
                [HypothesisFocus("signal", bull.signal), HypothesisFocus("signal", bear.signal)],
                h,
                period,
                priority=1.5,
            )
        )
    return out


def _category_claim(
    firings_by_category: Mapping[str, set[str]],
    basket_size: int,
    period: str,
    horizon_override: int | None,
) -> list[BacktestHypothesis]:
    """The category driving this run → does that family carry this basket?"""
    if basket_size < MIN_CLUSTER_TICKERS or not firings_by_category:
        return []
    category, tickers = max(firings_by_category.items(), key=lambda kv: (len(kv[1]), kv[0]))
    if len(tickers) < MIN_CLUSTER_TICKERS:
        return []
    h = horizon_override or horizon_for_category(category)
    return [
        _make(
            "category",
            f"{category} signals drive this run — do they carry this basket?",
            (
                f"{category} detectors account for the most live directional calls "
                f"({len(tickers)} tickers). If this run leans on them, their pooled "
                f"history across the basket should beat chance at {h} bars."
            ),
            sorted(tickers),
            [HypothesisFocus("category", category)],
            h,
            period,
            priority=1.8,
        )
    ]


def _strength_ladder_claim(
    latest: Mapping[str, Sequence[MutableSignal]],
    period: str,
    horizon_override: int | None,
) -> list[BacktestHypothesis]:
    """STRONG / EXTREME calls fired → is the label earned on this basket?"""
    strong: dict[str, set[str]] = {}
    for ticker, signals in latest.items():
        for s in signals:
            if _STRENGTH_RANK.get(s.strength, 0) >= 2:
                strong.setdefault(s.strength, set()).add(ticker)
    if not strong:
        return []
    strength, _ = max(strong.items(), key=lambda kv: (len(kv[1]), kv[0]))
    plain = "BULLISH" if "BULLISH" in strength else "BEARISH"
    symbols = sorted(latest.keys())
    h = horizon_override or _DEFAULT_HORIZON
    return [
        _make(
            "strength",
            f"Is '{strength}' earned? {strength} vs plain {plain} on this basket",
            (
                f"The run labels some calls {strength.lower()}. A stronger label "
                f"should mean a higher hit-rate than plain {plain.lower()} calls — "
                f"otherwise the label is decoration."
            ),
            symbols,
            [HypothesisFocus("strength", strength), HypothesisFocus("strength", plain)],
            h,
            period,
            priority=1.2,
        )
    ]


def suggest_hypotheses(
    latest: Mapping[str, Sequence[MutableSignal]],
    *,
    period: str = DEFAULT_BACKTEST_PERIOD,
    horizon_days: int | None = None,
    max_suggestions: int = DEFAULT_MAX_SUGGESTIONS,
    max_symbols: int | None = None,
) -> list[BacktestHypothesis]:
    """Generate backtest hypotheses from the signals live on the latest bar.

    Args:
        latest: Ticker → signals detected on its most recent bar.
        period: Backtest history window for every generated spec.
        horizon_days: Force one forward horizon; ``None`` picks one per
            signal category.
        max_suggestions: Cap on returned hypotheses, highest priority first.
        max_symbols: Trim each spec to at most this many tickers (the run
            endpoint's cap), noting the trim in its rationale.

    Returns:
        Deduplicated hypotheses, highest priority first.
    """
    basket_size = len(latest)
    firings: dict[str, list[tuple[str, MutableSignal]]] = {}
    by_category: dict[str, set[str]] = {}
    for ticker, signals in latest.items():
        for s in signals:
            if not _is_directional(s):
                continue
            firings.setdefault(s.signal, []).append((ticker, s))
            by_category.setdefault(s.category, set()).add(ticker)

    candidates = [
        *_cluster_claims(firings, basket_size, period, horizon_days),
        *_category_claim(by_category, basket_size, period, horizon_days),
        *_conflict_claims(latest, period, horizon_days),
        *_strength_ladder_claim(latest, period, horizon_days),
        *_single_ticker_claims(latest, period, horizon_days),
    ]
    seen: set[str] = set()
    ordered: list[BacktestHypothesis] = []
    clusters = 0
    for h in sorted(candidates, key=lambda c: (-c.priority, c.title)):
        if h.id in seen:
            continue
        if h.kind == "cluster":
            if clusters >= MAX_CLUSTER_CLAIMS:
                continue
            clusters += 1
        seen.add(h.id)
        ordered.append(h)
    picked = _interleave_kinds(ordered)[: max(0, max_suggestions)]
    if max_symbols is None:
        return picked
    return [_trim_symbols(h, max_symbols) for h in picked]


def _trim_symbols(h: BacktestHypothesis, max_symbols: int) -> BacktestHypothesis:
    """Cap a spec's basket so it stays runnable; the id tracks the trimmed set."""
    if len(h.symbols) <= max_symbols:
        return h
    kept = h.symbols[:max_symbols]
    return replace(
        h,
        symbols=kept,
        id=_hypothesis_id(h.kind, kept, h.focus, h.horizon_days),
        rationale=f"{h.rationale} (Tests the first {max_symbols} of {len(h.symbols)} tickers.)",
    )


def _interleave_kinds(ordered: Sequence[BacktestHypothesis]) -> list[BacktestHypothesis]:
    """Round-robin across kinds (each kind keeps its priority order), so a
    capped list shows one of each kind before a second of any."""
    by_kind: dict[str, list[BacktestHypothesis]] = {}
    for h in ordered:
        by_kind.setdefault(h.kind, []).append(h)
    queues = list(by_kind.values())
    out: list[BacktestHypothesis] = []
    while any(queues):
        for q in queues:
            if q:
                out.append(q.pop(0))
    return out


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
def _buckets_for(group: FocusGroup, buckets: Mapping[str, Sequence[HitRateBucket]]) -> Sequence[HitRateBucket]:
    return buckets.get(group, ())


def evaluate_focus(
    focus: HypothesisFocus,
    buckets: Mapping[str, Sequence[HitRateBucket]],
    up_rate: float | None,
) -> FocusVerdict:
    """Score one focus bucket against its bullish/bearish-mix chance baseline.

    ``supported`` needs the Wilson lower bound above baseline; ``contradicted``
    needs the upper bound below it; both need ``MIN_VERDICT_SAMPLES`` calls.

    Args:
        focus: Which bucket to look at.
        buckets: ``{"signal": [...], "category": [...], "strength": [...]}``.
        up_rate: Fraction of scored bars that rose, or None when unknown.

    Returns:
        A :class:`FocusVerdict`.
    """
    bucket = next((b for b in _buckets_for(focus.group, buckets) if b.key == focus.key), None)
    if bucket is None or bucket.total == 0:
        return FocusVerdict(
            focus, "no_data", 0, 0, None, None, None, None,
            f"{focus.key} never fired in the replayed history.",
        )
    lower, upper = wilson_interval(bucket.hits, bucket.total)
    baseline = bucket_baseline(bucket, up_rate) if up_rate is not None else 0.5
    rate = bucket.hit_rate
    pct = f"{rate:.0%} ({bucket.hits}/{bucket.total})"
    if bucket.total < MIN_VERDICT_SAMPLES:
        status: VerdictStatus = "inconclusive"
        msg = f"{focus.key}: {pct} — too few calls (< {MIN_VERDICT_SAMPLES}) to judge."
    elif lower > baseline:
        status = "supported"
        msg = f"{focus.key}: {pct}, 95% CI {lower:.0%}–{upper:.0%} clears chance {baseline:.0%}."
    elif upper < baseline:
        status = "contradicted"
        msg = f"{focus.key}: {pct}, 95% CI {lower:.0%}–{upper:.0%} is below chance {baseline:.0%}."
    else:
        status = "inconclusive"
        msg = f"{focus.key}: {pct}, 95% CI {lower:.0%}–{upper:.0%} straddles chance {baseline:.0%}."
    return FocusVerdict(focus, status, bucket.hits, bucket.total, rate, lower, upper, baseline, msg)


def evaluate_hypothesis(
    focus: Sequence[HypothesisFocus],
    buckets: Mapping[str, Sequence[HitRateBucket]],
    up_rate: float | None,
) -> HypothesisVerdict:
    """Score every focus and roll them up into one headline status.

    A single focus sets the headline directly. Several foci (a strength or
    conflict comparison) are scored independently, never against each other,
    so one focus beating chance is not evidence for the comparison: the
    headline is ``supported``/``contradicted`` only when every focus agrees,
    and otherwise ``inconclusive`` with a message naming the per-focus result.

    Args:
        focus: The hypothesis's focus buckets.
        buckets: Result buckets keyed by group.
        up_rate: Chance up-rate for the tested basket and horizon.

    Returns:
        A :class:`HypothesisVerdict`.
    """
    verdicts = tuple(evaluate_focus(f, buckets, up_rate) for f in focus)
    if not verdicts:
        return HypothesisVerdict("no_data", (), "No focus buckets to evaluate.")
    detail = " · ".join(v.message for v in verdicts)
    if len(verdicts) == 1:
        return HypothesisVerdict(verdicts[0].status, verdicts, detail)
    statuses = {v.status for v in verdicts}
    if len(statuses) == 1:
        return HypothesisVerdict(verdicts[0].status, verdicts, detail)
    cleared = [v.focus.key for v in verdicts if v.status == "supported"]
    if cleared:
        detail = (
            f"Per-focus evidence only, not a direct comparison — {', '.join(cleared)} "
            f"cleared chance on its own. {detail}"
        )
    return HypothesisVerdict("inconclusive", verdicts, detail)
