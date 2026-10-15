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

# FIB-ICHIMOKU-MA.md §12.6/§12.7: per-interval horizon and warmup. Weekly
# bars need far fewer bars to warm up ATR(14)/Volume_MA(20) (14/20 *weeks*,
# not days) and MTF2's own spec is a 13-bar (13-week) forward horizon on
# weekly bars, vs the daily default's 21-day horizon.
HORIZON_BY_INTERVAL: dict[str, int] = {"1d": HORIZON, "1wk": 13}
WARMUP_BY_INTERVAL: dict[str, int] = {"1d": WARMUP, "1wk": 60}

logger = logging.getLogger("eval_fibonacci")


def resample_to_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """M1 (FIB-ICHIMOKU-MA.md §12): resample daily OHLCV to completed weekly
    bars only (W-FRI grouping: first Open, max High, min Low, last Close,
    summed Volume).

    Drops the final resampled week when it is still forming — the underlying
    daily data doesn't yet reach that week's Friday close. Using a forming
    week's bar is the same causality violation as D6's negative shift: it
    would let a not-yet-final week's structure move on data unavailable at
    the time.

    Args:
        df: Daily OHLCV, oldest first, DatetimeIndex.

    Returns:
        Weekly OHLCV, oldest first, indexed by each week's Friday label.
    """
    weekly = (
        df.resample("W-FRI")
        .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
        .dropna(subset=["Open"])
    )
    if weekly.empty:
        return weekly
    last_daily_date = df.index[-1]
    if last_daily_date < weekly.index[-1]:
        weekly = weekly.iloc[:-1]  # forming week — the daily data doesn't reach its Friday yet
    return weekly


def retracement_depths(df: pd.DataFrame) -> list[float]:
    """Causal per-bar retracement depth of the nearest confirmed leg, 0..1+
    (0 = leg's far end, 1 = leg's origin; > 1 means price has broken past the
    origin). Used by the MTF5 excess-mass test — see ``excess_mass_near``.

    Mirrors ``evaluate_ticker``'s causal-pivot-window pattern: at bar i, only
    pivots inside [start+PIVOT_WINDOW, i-PIVOT_WINDOW] are visible, so this
    never uses a pivot the live detector couldn't see yet at that bar.

    Args:
        df: Indicator-computed OHLCV (needs ATR), oldest first.

    Returns:
        One depth per bar where a leg was active and ATR was available;
        bars with no active leg contribute nothing.
    """
    import signals_app.indicators.fibonacci as fibmath
    from signals_app.indicators.fibonacci import recent_legs
    from signals_app.indicators.pivots import PIVOT_WINDOW

    original_precompute = fibmath.precompute_pivots
    all_pivots = original_precompute(df, max_levels=len(df))
    view = {"start": 0, "end": 0}

    def causal_pivots(_df: pd.DataFrame, max_levels: int = 20, **_: object) -> list:
        lo, hi = view["start"] + PIVOT_WINDOW, view["end"] - PIVOT_WINDOW
        visible = [p for p in all_pivots if lo <= p.bar_index <= hi]
        return visible[-max_levels:]

    depths: list[float] = []
    close = df["Close"].to_numpy()
    atr = df["ATR"].to_numpy() if "ATR" in df.columns else None
    if atr is None:
        return depths
    fibmath.precompute_pivots = causal_pivots  # type: ignore[assignment]
    try:
        for i in range(len(df)):
            a = atr[i]
            if not math.isfinite(a) or a <= 0:
                continue
            view["start"], view["end"] = 0, i
            legs = recent_legs(df.iloc[: i + 1], a)
            if not legs:
                continue
            leg = legs[0]
            if leg.range <= 0:
                continue
            price = close[i]
            depth = (leg.high - price) / leg.range if leg.is_up else (price - leg.low) / leg.range
            if math.isfinite(depth):
                depths.append(depth)
    finally:
        fibmath.precompute_pivots = original_precompute  # never leak the monkeypatch
    return depths


def excess_mass_near(depths: list[float], center: float = 0.618, half_width: float = 0.066) -> dict:
    """MTF5 (FIB-ICHIMOKU-MA.md §12.7 step 2 / SA14's L1 test, split by
    timeframe): observed mass of retracement depths in
    ``[center - half_width, center + half_width]`` (0.55-0.681, covering the
    0.618 golden-pocket-adjacent band) minus the mass a uniform distribution
    over [0, 1] would put there. A positive excess means depths cluster near
    0.618 more than chance; not proof by itself (see docs/fibonacci-signal-
    evaluation-2026-09-26.md on why the first study's edge didn't hold up),
    but the input SA14/L1 needs.

    Args:
        depths: Output of ``retracement_depths`` (may include values > 1).
        center: Ratio to test excess mass around (0.618 = golden pocket).
        half_width: Half the band width (default: matches GOLDEN_POCKET
            0.618-0.65 plus symmetric slack).

    Returns:
        Dict with n, observed band fraction, uniform expectation, and
        excess (observed - expected). n=0 when no depths were measured.
    """
    in_domain = [d for d in depths if 0.0 <= d <= 1.0]
    n = len(in_domain)
    if n == 0:
        return {"n": 0, "observed": float("nan"), "expected": float("nan"), "excess": float("nan")}
    lo, hi = max(0.0, center - half_width), min(1.0, center + half_width)
    observed = sum(1 for d in in_domain if lo <= d <= hi) / n
    expected = hi - lo  # uniform density over [0, 1]
    return {"n": n, "observed": observed, "expected": expected, "excess": observed - expected}


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
    from signals_app.data.fetcher import DataFetcher

    try:
        df = DataFetcher().fetch_daily_history(ticker, PERIOD)  # Alpaca first
    except ValueError:
        return None
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    df.to_pickle(path)
    return df


def evaluate_ticker(args: tuple[str, str, int, str]) -> TickerResult | None:
    ticker, cache_dir, window, interval = args
    import signals_app.indicators.fibonacci as fibmath
    from signals_app.detection.fibonacci import FibonacciDetector
    from signals_app.indicators.compute import compute_indicators
    from signals_app.indicators.pivots import PIVOT_WINDOW, precompute_pivots

    warmup = WARMUP_BY_INTERVAL[interval]
    horizon = HORIZON_BY_INTERVAL[interval]

    raw = load_ohlcv(ticker, Path(cache_dir))
    if raw is None:
        return None
    if interval == "1wk":
        raw = resample_to_weekly(raw)  # M1: completed weeks only
    if len(raw) < warmup + horizon + 1:
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
    for i in range(warmup, len(df) - horizon):
        fwd = close[i + horizon] / close[i] - 1.0
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
            next_allowed = i + horizon
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


def evaluate_ticker_mtf5(args: tuple[str, str, str]) -> dict[str, float] | None:
    """MTF5 worker: retracement-depth excess mass near 0.618 for one ticker
    at one interval (daily or weekly). Standalone (picklable) function for
    ``multiprocessing.Pool``, mirroring ``evaluate_ticker``'s pattern.
    """
    ticker, cache_dir, interval = args
    from signals_app.indicators.compute import compute_indicators

    raw = load_ohlcv(ticker, Path(cache_dir))
    if raw is None:
        return None
    if interval == "1wk":
        raw = resample_to_weekly(raw)
    warmup = WARMUP_BY_INTERVAL[interval]
    if len(raw) < warmup + 1:
        return None
    df = compute_indicators(raw).reset_index(drop=True)
    depths = retracement_depths(df.iloc[warmup:].reset_index(drop=True))
    return excess_mass_near(depths)


def run_mtf5(tickers: list[str], cache: Path, workers: int) -> dict[str, dict]:
    """MTF5 (FIB-ICHIMOKU-MA.md §12.7 step 2): excess-mass test at 0.618 on
    weekly vs daily retracement depths, same tickers, same cache.

    Small-sample by design when ``tickers`` is short — see the CLI's
    ``--mtf5`` docstring on honestly reporting sample size against the
    doc's full-scale power requirement.
    """
    results: dict[str, dict] = {}
    with mp.get_context("spawn").Pool(workers) as pool:
        for interval in ("1d", "1wk"):
            per_ticker = [
                r
                for r in pool.map(
                    evaluate_ticker_mtf5, [(t, str(cache), interval) for t in tickers]
                )
                if r and r["n"] > 0
            ]
            total_n = sum(r["n"] for r in per_ticker)
            if total_n == 0:
                results[interval] = {"n": 0, "tickers": 0}
                continue
            pooled_observed = sum(r["observed"] * r["n"] for r in per_ticker) / total_n
            pooled_expected = per_ticker[0]["expected"]  # same band width every ticker
            results[interval] = {
                "n": total_n,
                "tickers": len(per_ticker),
                "observed": pooled_observed,
                "expected": pooled_expected,
                "excess": pooled_observed - pooled_expected,
            }
    return results


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
    parser.add_argument(
        "--interval", choices=("1d", "1wk"), default="1d",
        help="MTF2 (FIB-ICHIMOKU-MA.md §12.7 step 2): '1wk' resamples cached daily "
             "bars to completed weekly bars (M1) and runs the unchanged default "
             "detector against a 13-bar weekly horizon and weekly baseline, instead "
             "of the daily default.",
    )
    parser.add_argument(
        "--mtf5", action="store_true",
        help="MTF5 (FIB-ICHIMOKU-MA.md §12.7 step 2): run the retracement-depth "
             "excess-mass test at 0.618 on both daily and weekly bars for the same "
             "tickers, instead of the hit-rate evaluation. Ignores --interval.",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("signals_app").setLevel(logging.WARNING)

    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    tickers = sample_tickers(args.sample_size, args.seed)

    if args.mtf5:
        summary = {
            "mtf5_tickers": len(tickers),
            "by_interval": run_mtf5(tickers, cache, args.workers),
        }
        print(json.dumps(summary, indent=2))
        if args.out:
            Path(args.out).write_text(json.dumps(summary, indent=2))
        return

    # spawn, not fork: fork hangs on macOS once numpy/BLAS threads exist.
    with mp.get_context("spawn").Pool(args.workers) as pool:
        results = [r for r in pool.map(
            evaluate_ticker,
            [(t, str(cache), args.window, args.interval) for t in tickers],
        ) if r]

    half = len(tickers) // 2
    first = set(tickers[:half])
    summary = {
        "interval": args.interval,
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
