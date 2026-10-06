#!/usr/bin/env python3
"""Earn the evidence factor E for every signal label (spec §8.3 P5).

Replays every default *and* experimental detector, bar by bar and causally, over
cached daily OHLCV, and measures each label's hit rate against the unconditional
hit rate in the label's own direction over the same bars. Writes
``calibration/evidence/evidence-<date>.json`` for ``scoring/evidence.py``.

Method (each step exists to stop a specific way of fooling ourselves):
  * edge = hit rate of the label - baseline hit rate of the same direction on the
    same sampled bars, so a bullish label in a bull market is not "edge".
  * events on one ticker closer than the horizon are collapsed to one, because
    they share a forward window; counting them separately inflates z.
  * fit on everything before the last HOLDOUT_BARS bars (purged by the horizon so
    no fit window reaches into the holdout); report the holdout separately. A
    label earns E > 0 only if it clears the bar on the fit AND is above baseline
    on the held-out bars.
  * E = clip(fit edge / REF_EDGE_PP, 0, 1), and 0 unless n >= MIN_N and
    z >= MIN_Z. REF_EDGE_PP is one stated constant, not tuned per label.

K (the kind multipliers) is NOT fitted here: it needs the graded ranker's score,
so it is fitted by scripts/graded_shadow.py. This file records the starting K.

Usage:
    python scripts/eval_signals.py --cache /tmp/sigcache --sample-size 200 --step 3
    python scripts/eval_signals.py --cache /tmp/sigcache --sample-size 0 --step 5 --out e.json
"""
from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

HORIZON = 21
WARMUP = 210
STEP = 3
HOLDOUT_BARS = 252
MIN_N = 300
MIN_Z = 1.5
REF_EDGE_PP = 2.0
REGIME_BENCHMARK = "SPY"
EVIDENCE_DIR = Path(__file__).resolve().parent.parent / "calibration" / "evidence"

DetectFn = Callable[[pd.DataFrame], list]


@dataclass(frozen=True)
class Event:
    """One label firing on one bar, with what happened next."""

    ticker: str
    bar: int
    label: str
    concept: str | None
    kind: str | None
    direction: int
    fwd: float
    regime: str | None


@dataclass(frozen=True)
class FrameResult:
    """Everything one ticker contributes: its events and the baseline bars."""

    ticker: str
    n_bars: int
    events: list[Event]
    baseline: list[tuple[int, float, str | None]]  # (bar, forward return, regime)


def direction_of(strength: str) -> int:
    """+1 for a bullish label, -1 for a bearish one, 0 when it carries no side."""
    if "BULLISH" in strength:
        return 1
    if "BEARISH" in strength:
        return -1
    return 0


def evaluate_frame(
    ticker: str,
    df: pd.DataFrame,
    detect_fn: DetectFn,
    regimes: pd.Series | None = None,
    step: int = STEP,
    warmup: int = WARMUP,
    horizon: int = HORIZON,
) -> FrameResult:
    """Run ``detect_fn`` on every ``step``-th bar and pair each hit with its forward return.

    ``df`` must already carry indicators. ``detect_fn`` is injected so the
    statistics can be tested against a planted edge without the real detectors.
    """
    close = df["Close"].to_numpy()
    events: list[Event] = []
    baseline: list[tuple[int, float, str | None]] = []
    for bar in range(warmup, len(df) - horizon, step):
        fwd = close[bar + horizon] / close[bar] - 1.0
        if not math.isfinite(fwd):
            continue
        regime = _regime_at(regimes, df.index[bar])
        baseline.append((bar, fwd, regime))
        for sig in detect_fn(df.iloc[: bar + 1]):
            direction = direction_of(sig.strength)
            if direction:
                events.append(Event(ticker, bar, sig.signal, sig.concept, sig.kind,
                                    direction, fwd, regime))
    return FrameResult(ticker, len(df), events, baseline)


def _regime_at(regimes: pd.Series | None, stamp: object) -> str | None:
    if regimes is None:
        return None
    try:
        value = regimes.asof(stamp)  # type: ignore[arg-type]
    except (KeyError, TypeError):
        return None
    return value if isinstance(value, str) else None


def collapse_overlaps(events: Iterable[Event], key: Callable[[Event], str],
                      horizon: int = HORIZON) -> list[Event]:
    """Keep the first event per (ticker, key) in each horizon-wide window."""
    kept: list[Event] = []
    last_kept: dict[tuple[str, str], int] = {}
    for event in sorted(events, key=lambda e: (e.ticker, key(e), e.bar)):
        slot = (event.ticker, key(event))
        if slot in last_kept and event.bar < last_kept[slot] + horizon:
            continue
        last_kept[slot] = event.bar
        kept.append(event)
    return kept


def baseline_hit_rate(baseline: list[tuple[int, float, str | None]], direction: int) -> float:
    """Share of sampled bars whose forward return went the given way."""
    if not baseline:
        return float("nan")
    return sum(1 for _, fwd, _ in baseline if fwd * direction > 0) / len(baseline)


def label_stats(events: list[Event], baseline: list[tuple[int, float, str | None]]) -> dict:
    """Hit rate, edge over the same-direction baseline, and z for a set of events.

    Events of mixed direction are scored each against their own direction's
    baseline and pooled.
    """
    n = len(events)
    if n == 0:
        return {"n": 0, "hit_rate": None, "baseline": None, "edge_pp": 0.0, "z": 0.0}
    base_by_dir = {d: baseline_hit_rate(baseline, d) for d in (1, -1)}
    hits = sum(1 for e in events if e.fwd * e.direction > 0)
    expected = sum(base_by_dir[e.direction] for e in events) / n
    hit_rate = hits / n
    se = math.sqrt(max(expected * (1 - expected), 1e-12) / n)
    return {
        "n": n,
        "tickers": len({e.ticker for e in events}),
        "hit_rate": round(hit_rate, 4),
        "baseline": round(expected, 4),
        "edge_pp": round(100 * (hit_rate - expected), 3),
        "z": round((hit_rate - expected) / se, 3),
    }


def earn_evidence(fit: dict, holdout: dict, *, min_n: int = MIN_N, min_z: float = MIN_Z,
                  ref_edge_pp: float = REF_EDGE_PP) -> float:
    """E in [0, 1]: zero unless the label cleared the fit bar *and* held out."""
    if fit["n"] < min_n or fit["z"] < min_z or fit["edge_pp"] <= 0:
        return 0.0
    if holdout["n"] == 0 or holdout["edge_pp"] <= 0:
        return 0.0
    return round(min(1.0, max(0.0, fit["edge_pp"] / ref_edge_pp)), 3)


def split_fit_holdout(results: list[FrameResult], horizon: int = HORIZON,
                      holdout_bars: int = HOLDOUT_BARS):
    """Per-ticker split: fit bars end ``horizon`` before the holdout starts (purge)."""
    fit_events: list[Event] = []
    hold_events: list[Event] = []
    fit_base: list[tuple[int, float, str | None]] = []
    hold_base: list[tuple[int, float, str | None]] = []
    for result in results:
        cutoff = result.n_bars - holdout_bars
        fit_events += [e for e in result.events if e.bar + horizon < cutoff]
        hold_events += [e for e in result.events if e.bar >= cutoff]
        fit_base += [b for b in result.baseline if b[0] + horizon < cutoff]
        hold_base += [b for b in result.baseline if b[0] >= cutoff]
    return fit_events, hold_events, fit_base, hold_base


def _entry(events: list[Event], fit_base, hold_base, cutoff_split) -> dict:
    fit_ev, hold_ev = cutoff_split
    fit, hold = label_stats(fit_ev, fit_base), label_stats(hold_ev, hold_base)
    first = events[0]
    return {"E": earn_evidence(fit, hold), "kind": first.kind, "concept": first.concept,
            "fit": fit, "holdout": hold}


def build_evidence(results: list[FrameResult], code_version: str = "unknown",
                   today: str | None = None) -> dict:
    """Assemble the evidence document from every ticker's replay."""
    fit_ev, hold_ev, fit_base, hold_base = split_fit_holdout(results)
    labels: dict[str, dict] = {}
    concepts: dict[str, dict] = {}
    regimes: dict[str, dict[str, float]] = {}
    unmeasured: list[str] = []

    for label in sorted({e.label for e in fit_ev + hold_ev}):
        mine_fit = collapse_overlaps([e for e in fit_ev if e.label == label], lambda e: e.label)
        mine_hold = collapse_overlaps([e for e in hold_ev if e.label == label], lambda e: e.label)
        entry = _entry(mine_fit + mine_hold, fit_base, hold_base, (mine_fit, mine_hold))
        # A thinly-sampled label gets no entry of its own, so the lookup falls
        # through to its concept. An explicit E = 0 here would shadow a concept
        # that did earn evidence (parametrized labels like "WITHIN 1% OF 20b
        # HIGH" are many and individually rare).
        if entry["fit"]["n"] < MIN_N:
            unmeasured.append(label)
            continue
        labels[label] = entry
        by_regime = _regime_evidence(mine_fit, mine_hold, fit_base, hold_base)
        if by_regime:
            regimes[label] = by_regime

    for concept in sorted({e.concept for e in fit_ev + hold_ev if e.concept}):
        mine_fit = collapse_overlaps([e for e in fit_ev if e.concept == concept],
                                     lambda e: e.concept or "")
        mine_hold = collapse_overlaps([e for e in hold_ev if e.concept == concept],
                                      lambda e: e.concept or "")
        concepts[concept] = _entry(mine_fit + mine_hold, fit_base, hold_base,
                                   (mine_fit, mine_hold))

    return {
        "version": today or date.today().isoformat(),
        "code_version": code_version,
        "default_E": 0.0,
        "params": {"horizon": HORIZON, "step": STEP, "holdout_bars": HOLDOUT_BARS,
                   "min_n": MIN_N, "min_z": MIN_Z, "ref_edge_pp": REF_EDGE_PP},
        "baseline": {"hit_rate_up": round(baseline_hit_rate(fit_base, 1), 4),
                     "bars": len(fit_base)},
        "K": {"X": 1.0, "T": 0.6, "S": 0.25, "P": 0.15, "C": 0.0, "fitted": False},
        "labels": labels,
        "unmeasured_labels": unmeasured,
        "concepts": concepts,
        "regimes": regimes,
    }


def _regime_evidence(fit_ev, hold_ev, fit_base, hold_base) -> dict[str, float]:
    out: dict[str, float] = {}
    for regime in sorted({e.regime for e in fit_ev if e.regime}):
        fit = label_stats([e for e in fit_ev if e.regime == regime],
                          [b for b in fit_base if b[2] == regime])
        hold = label_stats([e for e in hold_ev if e.regime == regime],
                           [b for b in hold_base if b[2] == regime])
        if fit["n"] >= MIN_N:
            out[regime] = earn_evidence(fit, hold)
    return out


# ---------------------------------------------------------------- orchestration


def _load(ticker: str, cache: Path) -> pd.DataFrame | None:
    path = cache / f"{ticker}.pkl"
    if path.exists():
        return pd.read_pickle(path)
    import yfinance as yf

    df = yf.Ticker(ticker).history(period="5y", interval="1d", auto_adjust=True)
    if df is None or df.empty:
        return None
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    df.to_pickle(path)
    return df


def _worker(args: tuple[str, str, int]) -> FrameResult | None:
    ticker, cache_dir, step = args
    from signals_app.detection.orchestrator import detect_all_signals
    from signals_app.indicators.compute import compute_indicators
    from signals_app.scoring.regime import regime_series

    cache = Path(cache_dir)
    raw = _load(ticker, cache)
    if raw is None or len(raw) < WARMUP + HORIZON + 1:
        return None
    benchmark = _load(REGIME_BENCHMARK, cache)
    regimes = regime_series(benchmark) if benchmark is not None else None
    df = compute_indicators(raw)
    return evaluate_frame(
        ticker, df, lambda window: detect_all_signals(window, include_experimental=True),
        regimes=regimes, step=step,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache", required=True, help="Directory of cached OHLCV pickles")
    parser.add_argument("--sample-size", type=int, default=200, help="0 = whole seed universe")
    parser.add_argument("--seed", type=int, default=20260926)
    parser.add_argument("--step", type=int, default=STEP, help="Evaluate every Nth bar")
    parser.add_argument("--workers", type=int, default=max(1, mp.cpu_count() - 1))
    parser.add_argument("--out", help=f"Default: {EVIDENCE_DIR}/evidence-<date>.json")
    args = parser.parse_args()

    import logging

    logging.getLogger("signals_app").setLevel(logging.ERROR)
    from signals_app.config import SIGNALS_APP_CODE_VERSION

    import eval_fibonacci as ef

    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    tickers = ef.sample_tickers(args.sample_size, args.seed)
    with mp.get_context("spawn").Pool(args.workers) as pool:
        results = [r for r in pool.map(_worker, [(t, str(cache), args.step) for t in tickers]) if r]

    evidence = build_evidence(results, SIGNALS_APP_CODE_VERSION)
    evidence["tickers_evaluated"] = len(results)
    out = Path(args.out) if args.out else EVIDENCE_DIR / f"evidence-{evidence['version']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evidence, indent=2))
    earned = {k: v["E"] for k, v in evidence["labels"].items() if v["E"] > 0}
    print(f"{len(results)} tickers, {len(evidence['labels'])} labels, {len(earned)} earned E>0")
    print(json.dumps(earned, indent=2))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
