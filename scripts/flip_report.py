"""Flip report: which bars change BUY/HOLD/SELL between two detector/ranker versions.

Run the *same* script against two source trees, then diff:

    PYTHONPATH=<old-tree>/src python scripts/flip_report.py dump old.json
    PYTHONPATH=<new-tree>/src python scripts/flip_report.py dump new.json
    python scripts/flip_report.py diff old.json new.json

Data is a deterministic synthetic basket by default (reproducible, offline).
Pass ``--csv-dir DIR`` with ``<TICKER>.csv`` OHLCV files to replay real bars.
Read-only: nothing is written except the JSON you name.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import signals_app
from signals_app.detection.orchestrator import detect_all_signals
from signals_app.indicators.compute import compute_indicators
from signals_app.scoring.confluence import ConfluenceRanker

WARMUP_BARS = 210
STEP = 5
HORIZON = 21


def _synthetic(seed: int, n: int = 420) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(rng.normal(4e-4, 3e-4), rng.uniform(0.008, 0.03), n)))
    spread = np.abs(rng.normal(0, 0.006, n))
    open_ = close * (1 + rng.normal(0, 0.004, n))
    volume = rng.integers(1_000_000, 8_000_000, n).astype(float)
    return pd.DataFrame(
        {
            "Open": open_,
            "High": np.maximum(close, open_) * (1 + spread),
            "Low": np.minimum(close, open_) * (1 - spread),
            "Close": close,
            "Volume": volume,
        },
        index=pd.date_range("2024-01-01", periods=n, freq="B"),
    )


def _baskets(csv_dir: str | None, n_synthetic: int) -> dict[str, pd.DataFrame]:
    if csv_dir:
        return {
            p.stem: pd.read_csv(p, index_col=0, parse_dates=True)
            for p in sorted(Path(csv_dir).glob("*.csv"))
        }
    return {f"SYN{seed:03d}": _synthetic(seed) for seed in range(n_synthetic)}


def dump(out: str, csv_dir: str | None, n_synthetic: int) -> None:
    rows: dict[str, dict[str, object]] = {}
    ranker = ConfluenceRanker()
    for ticker, ohlcv in _baskets(csv_dir, n_synthetic).items():
        full = compute_indicators(ohlcv)
        close = full["Close"].to_numpy()
        for end in range(WARMUP_BARS, len(full) + 1, STEP):
            result = ranker.rank_signals(list(detect_all_signals(full.iloc[:end])))
            last = end - 1  # the bar the signals describe
            fwd = float(close[last + HORIZON] / close[last] - 1.0) if last + HORIZON < len(full) else None
            rows[f"{ticker}@{end}"] = {"action": result.action, "score": result.score, "fwd": fwd}
    Path(out).write_text(json.dumps({"source": signals_app.__file__, "rows": rows}))
    print(f"{len(rows)} bars from {signals_app.__file__}")


def print_hit_rates(old: dict, new: dict, keys: list[str]) -> None:
    """BUY/SELL hit rate (21-day forward return in the call's direction) for each version.

    Only bars with a realised forward return count, and a call is a hit when the
    return went its way. Bars are overlapping and drawn from a small sample, so
    treat the gap as a sanity check on direction, not as evidence of an edge.
    """
    known = [k for k in keys if old[k].get("fwd") is not None]
    base_up = sum(1 for k in known if old[k]["fwd"] > 0) / len(known) if known else float("nan")
    print(f"forward-return bars: {len(known)}   baseline up-rate: {base_up:.3f}")
    for action, wins in (("BUY", lambda r: r > 0), ("SELL", lambda r: r < 0)):
        for label, rows in (("old", old), ("new", new)):
            picked = [rows[k]["fwd"] for k in known if rows[k]["action"] == action]
            rate = sum(1 for r in picked if wins(r)) / len(picked) if picked else float("nan")
            print(f"  {action:4} {label}: n={len(picked):5d}  hit-rate={rate:.3f}")


def diff(old_path: str, new_path: str) -> int:
    old, new = (json.loads(Path(p).read_text())["rows"] for p in (old_path, new_path))
    keys = sorted(old.keys() & new.keys())
    flips = [(k, old[k]["action"], new[k]["action"]) for k in keys if old[k]["action"] != new[k]["action"]]
    shifts = [new[k]["score"] - old[k]["score"] for k in keys]
    print(f"bars compared: {len(keys)}   action flips: {len(flips)}")
    print(f"mean score shift: {np.mean(shifts):+.4f}   max |shift|: {np.max(np.abs(shifts)):.4f}")
    for transition in sorted({(a, b) for _, a, b in flips}):
        count = sum(1 for _, a, b in flips if (a, b) == transition)
        print(f"  {transition[0]:>4} -> {transition[1]:<4} {count}")
    print_hit_rates(old, new, keys)
    for key, before, after in flips[:25]:
        print(f"  {key}: {before} -> {after} (score {old[key]['score']:+.3f} -> {new[key]['score']:+.3f})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump")
    d.add_argument("out")
    d.add_argument("--csv-dir")
    d.add_argument("--synthetic", type=int, default=40)
    f = sub.add_parser("diff")
    f.add_argument("old")
    f.add_argument("new")
    args = parser.parse_args()
    if args.cmd == "dump":
        dump(args.out, args.csv_dir, args.synthetic)
        return 0
    return diff(args.old, args.new)


if __name__ == "__main__":
    sys.exit(main())
