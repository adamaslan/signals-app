#!/usr/bin/env python3
"""Evaluate the default FibonacciDetector signal against the 21-day baseline.

Runs the detector exactly as shipped, bar by bar and causally, over a fixed
random sample of the seed universe, and reports the hit-rate edge of
``FIB GOLDEN POCKET HOLD`` over the unconditional baseline, pooled and per
ticker half. See docs/fibonacci-signal-evaluation-2026-09-26.md.

``--window N`` limits what the detector sees at each bar to the last N bars,
the way the scheduled scan does (``--period 3mo`` is about 63 daily bars).
Indicators are always computed on the full history: ATR and Volume_MA_20 are
14/20-bar windows, identical to a short fetch once warmed up.

Pivots are computed once per ticker and filtered per bar. That matches calling
``precompute_pivots`` on each prefix/window: a pivot at bar j needs bars
j-3..j+3, so it is visible at bar i exactly when start+3 <= j <= i-3.

Usage:
    python scripts/eval_fibonacci.py --cache /tmp/fibcache --out result.json
    python scripts/eval_fibonacci.py --cache /tmp/fibcache --window 63
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import multiprocessing as mp
import random
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

SIGNAL = "FIB GOLDEN POCKET HOLD"
HORIZON = 21
WARMUP = 200
SAMPLE_SIZE = 200
SAMPLE_SEED = 20260926
PERIOD = "5y"
SEED_CSV = Path(__file__).resolve().parent.parent / "seed" / "universe_symbols.csv"

logger = logging.getLogger("eval_fibonacci")


@dataclass(frozen=True)
class TickerResult:
    ticker: str
    events: list[float]  # forward returns at each non-overlapping event
    baseline_up: int
    baseline_n: int
    baseline_ret_sum: float


def sample_tickers(size: int = SAMPLE_SIZE, seed: int = SAMPLE_SEED) -> list[str]:
    """Fixed random sample of the seed universe; ``size`` 0 means every ticker."""
    tickers = sorted(pd.read_csv(SEED_CSV)["ticker"].dropna().astype(str).unique())
    if size <= 0 or size >= len(tickers):
        return tickers
    return sorted(random.Random(seed).sample(tickers, size))


def load_ohlcv(ticker: str, cache: Path) -> pd.DataFrame | None:
    path = cache / f"{ticker}.pkl"
    if path.exists():
        return pd.read_pickle(path)
    import yfinance as yf

    df = yf.Ticker(ticker).history(period=PERIOD, interval="1d", auto_adjust=True)
    if df is None or df.empty:
        return None
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    df.to_pickle(path)
    return df


def evaluate_ticker(args: tuple[str, str, int]) -> TickerResult | None:
    ticker, cache_dir, window = args
    import signals_app.indicators.fibonacci as fibmath
    from signals_app.detection.fibonacci import FibonacciDetector
    from signals_app.indicators.compute import compute_indicators
    from signals_app.indicators.pivots import PIVOT_WINDOW, precompute_pivots

    raw = load_ohlcv(ticker, Path(cache_dir))
    if raw is None or len(raw) < WARMUP + HORIZON + 1:
        return None
    df = compute_indicators(raw).reset_index(drop=True)
    all_pivots = precompute_pivots(df, max_levels=len(df))
    view = {"start": 0, "end": 0}

    def causal_pivots(_df: pd.DataFrame, max_levels: int = 20, **_: object) -> list:
        lo, hi = view["start"] + PIVOT_WINDOW, view["end"] - PIVOT_WINDOW
        visible = [p for p in all_pivots if lo <= p.bar_index <= hi]
        return visible[-max_levels:]

    fibmath.precompute_pivots = causal_pivots  # type: ignore[assignment]
    detector = FibonacciDetector()
    close = df["Close"].to_numpy()

    events: list[float] = []
    next_allowed = 0
    up = n = 0
    ret_sum = 0.0
    for i in range(WARMUP, len(df) - HORIZON):
        fwd = close[i + HORIZON] / close[i] - 1.0
        if not math.isfinite(fwd):
            continue
        n += 1
        up += fwd > 0
        ret_sum += fwd
        if i < next_allowed:
            continue
        start = max(0, i - window + 1) if window else 0
        view["start"], view["end"] = start, i
        signals = detector.detect(df.iloc[start : i + 1])
        if any(s.signal == SIGNAL for s in signals):
            events.append(fwd)
            next_allowed = i + HORIZON
    return TickerResult(ticker, events, up, n, ret_sum)


def summarise(results: list[TickerResult]) -> dict[str, float | int]:
    events = [r for res in results for r in res.events]
    base_n = sum(r.baseline_n for r in results)
    base_p = sum(r.baseline_up for r in results) / base_n if base_n else float("nan")
    base_ret = sum(r.baseline_ret_sum for r in results) / base_n if base_n else float("nan")
    n = len(events)
    if n == 0:
        return {"n": 0, "tickers": 0, "baseline": base_p}
    hit = sum(r > 0 for r in events) / n
    se = math.sqrt(base_p * (1 - base_p) / n)
    return {
        "n": n,
        "tickers": sum(1 for r in results if r.events),
        "hit_rate": hit,
        "baseline": base_p,
        "edge_pp": 100 * (hit - base_p),
        "z": (hit - base_p) / se,
        "excess_return_pct": 100 * (sum(events) / n - base_ret),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache", required=True, help="Directory for cached OHLCV pickle files")
    parser.add_argument("--window", type=int, default=0,
                        help="Bars the detector sees per evaluation bar (0 = full history)")
    parser.add_argument("--sample-size", type=int, default=SAMPLE_SIZE,
                        help="Tickers drawn from the seed universe (0 = all)")
    parser.add_argument("--seed", type=int, default=SAMPLE_SEED, help="Sampling seed")
    parser.add_argument("--workers", type=int, default=max(1, mp.cpu_count() - 1))
    parser.add_argument("--out", help="Write the summary JSON here")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("signals_app").setLevel(logging.WARNING)

    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    tickers = sample_tickers(args.sample_size, args.seed)
    # spawn, not fork: fork hangs on macOS once numpy/BLAS threads exist.
    with mp.get_context("spawn").Pool(args.workers) as pool:
        results = [r for r in pool.map(
            evaluate_ticker, [(t, str(cache), args.window) for t in tickers]) if r]

    half = len(tickers) // 2
    first = set(tickers[:half])
    summary = {
        "window": args.window or "full",
        "tickers_evaluated": len(results),
        "pooled": summarise(results),
        "half_a": summarise([r for r in results if r.ticker in first]),
        "half_b": summarise([r for r in results if r.ticker not in first]),
    }
    print(json.dumps(summary, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
