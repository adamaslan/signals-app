#!/usr/bin/env python3
"""Shadow analysis for the graded confluence ranker (spec §8.3 P6).

``report``  joins ``confluence_shadow`` to ``forward_returns`` and compares the
            production ranker with the graded one on the same bars: BUY/SELL
            counts and hit rates, rank correlation of score with forward return,
            and a flip table (HOLD->BUY, BUY->HOLD, ...) with each flip's drivers.
            Read-only against Supabase.
``fit-k``   fits the kind multipliers T, S, P (X stays 1.0 as the scale anchor) by
            grid search on rank correlation of graded score vs forward return,
            fitted on bars before a purged holdout and reported on the holdout.

Cutover criteria checked by ``report`` (P6 gate): at least MIN_SHADOW_BUYS graded
BUY calls, graded BUY hit rate not below production's, and rank correlation not
worse. The flip table still has to be read by the owner; no script signs that off.

Usage:
    python scripts/graded_shadow.py report --horizon 21 --since 2026-10-07
    python scripts/graded_shadow.py fit-k --cache /tmp/sigcache --sample-size 100
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pandas as pd

MIN_SHADOW_BUYS = 300
MAX_FLIP_EXAMPLES = 20
K_GRID = {"T": (0.4, 0.6, 0.8), "S": (0.10, 0.25, 0.40), "P": (0.05, 0.15, 0.30)}
HOLDOUT_FRACTION = 0.2


def _hit(action: str, fwd: float) -> bool | None:
    if action == "BUY":
        return fwd > 0
    if action == "SELL":
        return fwd < 0
    return None


def ranker_stats(rows: list[dict], action_key: str, score_key: str) -> dict[str, Any]:
    """Counts, hit rates and rank correlation for one ranker over the joined rows."""
    out: dict[str, Any] = {}
    for action in ("BUY", "SELL"):
        picked = [r for r in rows if r[action_key] == action]
        hits = [_hit(action, r["fwd"]) for r in picked]
        out[action.lower()] = {
            "n": len(picked),
            "hit_rate": round(sum(hits) / len(hits), 4) if hits else None,
        }
    if len(rows) >= 3:
        scores = pd.Series([r[score_key] for r in rows], dtype=float)
        fwds = pd.Series([r["fwd"] for r in rows], dtype=float)
        corr = scores.corr(fwds, method="spearman")
        out["spearman"] = None if pd.isna(corr) else round(float(corr), 4)
    else:
        out["spearman"] = None
    return out


def flip_table(rows: list[dict]) -> dict[str, Any]:
    """How the action changed between rankers, with the graded drivers of each flip."""
    flips = [r for r in rows if r["old_action"] != r["new_action"]]
    counts = Counter(f"{r['old_action']}->{r['new_action']}" for r in flips)
    ranked = sorted(flips, key=lambda r: abs(r["new_score"] - r["old_score"]), reverse=True)
    examples = [
        {"ticker": r["ticker"], "bar_ts": r["bar_ts"], "flip": f"{r['old_action']}->{r['new_action']}",
         "old_score": r["old_score"], "new_score": r["new_score"], "fwd": r["fwd"],
         "drivers": [d["signal"] for d in r.get("drivers", [])]}
        for r in ranked[:MAX_FLIP_EXAMPLES]
    ]
    return {"total": len(flips), "of": len(rows), "counts": dict(counts), "examples": examples}


def cutover_check(production: dict, graded: dict, min_buys: int = MIN_SHADOW_BUYS) -> dict:
    """P6 gate. Returns ready plus the reason each failed criterion failed."""
    reasons: list[str] = []
    if graded["buy"]["n"] < min_buys:
        reasons.append(f"only {graded['buy']['n']} graded BUY calls (need {min_buys})")
    if graded["buy"]["hit_rate"] is None or production["buy"]["hit_rate"] is None:
        reasons.append("hit rate unavailable for a ranker")
    elif graded["buy"]["hit_rate"] < production["buy"]["hit_rate"]:
        reasons.append(f"graded BUY hit rate {graded['buy']['hit_rate']} < "
                       f"production {production['buy']['hit_rate']}")
    if graded["spearman"] is None or production["spearman"] is None:
        reasons.append("rank correlation unavailable")
    elif graded["spearman"] < production["spearman"]:
        reasons.append(f"graded rank correlation {graded['spearman']} < "
                       f"production {production['spearman']}")
    return {"ready": not reasons, "reasons": reasons}


def compare(rows: list[dict]) -> dict[str, Any]:
    """The full shadow comparison for joined rows (see module docstring)."""
    production = ranker_stats(rows, "old_action", "old_score")
    graded = ranker_stats(rows, "new_action", "new_score")
    base_up = sum(1 for r in rows if r["fwd"] > 0) / len(rows) if rows else None
    return {
        "bars": len(rows),
        "baseline_up": None if base_up is None else round(base_up, 4),
        "production": production,
        "graded": graded,
        "flips": flip_table(rows),
        "cutover": cutover_check(production, graded),
    }


# ----------------------------------------------------------------------------- fit K


@dataclass(frozen=True)
class FitBar:
    """One historical bar: its signals, what happened next, and which side of the split."""

    signals: list
    fwd: float
    is_fit: bool


def _spearman(scores: list[float], fwds: list[float]) -> float:
    if len(scores) < 3:
        return float("nan")
    return float(pd.Series(scores).corr(pd.Series(fwds), method="spearman"))


def fit_kind_multipliers(bars: list[FitBar], evidence, grid: dict[str, tuple[float, ...]] = K_GRID,
                         regime: str | None = None) -> dict[str, Any]:
    """Grid-search T, S, P to maximize fit-set rank correlation; report the holdout.

    X is held at 1.0 (it anchors the scale) and C at 0.0 (context never votes).
    The held-out correlation of the chosen K is reported next to that of the
    starting K, so an improvement that does not carry over is visible.
    """
    from signals_app.scoring.graded import GradedConfluenceRanker

    def corr_for(kinds: dict[str, float], fit: bool) -> float:
        ranker = GradedConfluenceRanker(replace(evidence, kind_multiplier=kinds))
        subset = [b for b in bars if b.is_fit == fit]
        scores = [ranker.rank_signals(b.signals, regime=regime).score for b in subset]
        return _spearman(scores, [b.fwd for b in subset])

    start = dict(evidence.kind_multiplier)
    best, best_fit = start, corr_for(start, True)
    for t, s, p in itertools.product(grid["T"], grid["S"], grid["P"]):
        candidate = {"X": 1.0, "T": t, "S": s, "P": p, "C": 0.0}
        fit_corr = corr_for(candidate, True)
        if fit_corr > best_fit:
            best, best_fit = candidate, fit_corr
    return {
        "K": best,
        "fit_spearman": round(best_fit, 4),
        "holdout_spearman": round(corr_for(best, False), 4),
        "start_K": start,
        "start_fit_spearman": round(corr_for(start, True), 4),
        "start_holdout_spearman": round(corr_for(start, False), 4),
        "fit_bars": sum(b.is_fit for b in bars),
        "holdout_bars": sum(not b.is_fit for b in bars),
    }


# --------------------------------------------------------------------------- Supabase


def _client():
    import httpx

    from signals_app.config import (
        SUPABASE_REQUEST_TIMEOUT_SECONDS,
        SUPABASE_SERVICE_ROLE_KEY,
        SUPABASE_URL,
    )

    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        sys.exit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set (source them from .env)")
    return httpx.Client(
        base_url=f"{SUPABASE_URL.rstrip('/')}/rest/v1",
        headers={"apikey": SUPABASE_SERVICE_ROLE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"},
        timeout=SUPABASE_REQUEST_TIMEOUT_SECONDS,
    )


def _paged(client, path: str, params: dict[str, str], page: int = 1000) -> list[dict]:
    rows: list[dict] = []
    while True:
        resp = client.get(path, params={**params, "limit": str(page), "offset": str(len(rows))})
        resp.raise_for_status()
        chunk = resp.json()
        rows += chunk
        if len(chunk) < page:
            return rows


def _key(ticker: str, bar_ts: str) -> tuple[str, pd.Timestamp]:
    return ticker, pd.to_datetime(bar_ts, utc=True)


def join_rows(shadow: list[dict], forward: list[dict]) -> list[dict]:
    """Inner-join shadow rows to forward returns on (ticker, bar_ts); skips rows with no production call."""
    fwd_by_key = {_key(f["ticker"], f["bar_ts"]): f["pct_return"] for f in forward}
    rows: list[dict] = []
    for row in shadow:
        production = (row.get("payload") or {}).get("production")
        fwd = fwd_by_key.get(_key(row["ticker"], row["bar_ts"]))
        if production is None or fwd is None:
            continue
        rows.append({"ticker": row["ticker"], "bar_ts": row["bar_ts"], "fwd": fwd,
                     "old_action": production["action"], "old_score": production["score"],
                     "new_action": row["action"], "new_score": row["score"],
                     "drivers": row["payload"].get("drivers", [])})
    return rows


def run_report(horizon: int, since: str | None, out: str | None) -> int:
    client = _client()
    filters = {"select": "ticker,bar_ts,action,score,payload", "order": "bar_ts.asc"}
    if since:
        filters["bar_ts"] = f"gte.{since}"
    shadow = _paged(client, "/confluence_shadow", filters)
    forward = _paged(client, "/forward_returns", {"select": "ticker,bar_ts,pct_return",
                                                  "horizon_days": f"eq.{horizon}"})
    report = compare(join_rows(shadow, forward))
    report["shadow_rows"], report["forward_rows"] = len(shadow), len(forward)
    text = json.dumps(report, indent=2, default=str)
    print(text)
    if out:
        Path(out).write_text(text)
    return 0 if report["cutover"]["ready"] else 1


def run_fit_k(cache: str, sample_size: int, step: int, out: str | None) -> int:
    import eval_fibonacci as ef
    import eval_signals as es

    from signals_app.detection.orchestrator import detect_all_signals
    from signals_app.indicators.compute import compute_indicators
    from signals_app.scoring.evidence import load_evidence

    bars: list[FitBar] = []
    for ticker in ef.sample_tickers(sample_size):
        raw = es._load(ticker, Path(cache))
        if raw is None or len(raw) < es.WARMUP + es.HORIZON + 1:
            continue
        df = compute_indicators(raw)
        cutoff = len(df) - es.HOLDOUT_BARS
        for bar in range(es.WARMUP, len(df) - es.HORIZON, step):
            is_fit = bar + es.HORIZON < cutoff
            if not is_fit and bar < cutoff:
                continue  # purge: its forward window straddles the split
            signals = list(detect_all_signals(df.iloc[: bar + 1], include_experimental=True))
            fwd = float(df["Close"].iloc[bar + es.HORIZON] / df["Close"].iloc[bar] - 1.0)
            bars.append(FitBar(signals, fwd, is_fit))
    result = fit_kind_multipliers(bars, load_evidence())
    text = json.dumps(result, indent=2)
    print(text)
    if out:
        Path(out).write_text(text)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    rep = sub.add_parser("report")
    rep.add_argument("--horizon", type=int, default=21)
    rep.add_argument("--since")
    rep.add_argument("--out")
    fit = sub.add_parser("fit-k")
    fit.add_argument("--cache", required=True)
    fit.add_argument("--sample-size", type=int, default=100)
    fit.add_argument("--step", type=int, default=5)
    fit.add_argument("--out")
    args = parser.parse_args()
    if args.cmd == "report":
        return run_report(args.horizon, args.since, args.out)
    return run_fit_k(args.cache, args.sample_size, args.step, args.out)


if __name__ == "__main__":
    sys.exit(main())
