#!/usr/bin/env python3
"""Evaluate any (detector, signal) pair against the unconditional baseline.

Generalises ``scripts/eval_fibonacci.py`` (PR #38) per FIB-ICHIMOKU-MA.md §8
row 3. For each series it reports the hit-rate edge over the same tickers'
unconditional rate (or over a reference series), with a two-way block
bootstrap: tickers and calendar months are resampled independently, so both
cross-sectional and time clustering widen the interval.

Events are non-overlapping per ticker (one per forward horizon), detected bar
by bar and causally. ``--window N`` limits what a detector sees to the last N
bars, as the scheduled scan does; series needing more bars than the window
(SMA-200, the cloud) cannot fire in production at that window and are
reported as not computable rather than silently evaluated on extra history.

Pre-registered hypotheses (goal and kill lines fixed before any run, spec
§6.4): H1, H3, H4 run here. H2 needs the §6.2 level field, which does not
exist in signals-app, so it is reported as not run.

Usage:
    python scripts/eval_detector.py --cache /tmp/evalcache --prefetch
    python scripts/eval_detector.py --cache /tmp/evalcache --run H1 H3 H4 \\
        --window 0 --window 63 --report-dir docs
    python scripts/eval_detector.py --cache /tmp/evalcache \\
        --detector ExpandedMACrossDetector --signal "GOLDEN CROSS" --side bull
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib
import json
import logging
import math
import multiprocessing as mp
import pkgutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eval_fibonacci as ef  # noqa: E402  (shared universe + cache helpers)

HORIZON = 21
WARMUP = 200
CLOUD_WARMUP_BARS = 78
BOOTSTRAP_DRAWS = 2000
BOOTSTRAP_SEED = 20261003
SIGNIFICANCE = 0.05
MIN_EVENTS_FOR_VERDICT = 30
PREFETCH_CHUNK = 100

logger = logging.getLogger("eval_detector")


@dataclass(frozen=True)
class RawSignal:
    """A base event: a detector label, or a column rule (``detector`` is None)."""

    name: str
    side: int  # +1 success = forward return > 0, -1 success = forward return < 0
    min_bars: int
    detector: str | None = None
    signal: str | None = None
    needs_pivots: bool = False


@dataclass(frozen=True)
class Series:
    """One evaluated event series: a raw signal, optionally filtered."""

    name: str
    raw: str
    filter_name: str | None = None
    reference: str | None = None  # compare to this series instead of the baseline
    min_bars: int = 0


@dataclass(frozen=True)
class Hypothesis:
    key: str
    title: str
    series: str
    goal_pp: float
    kill_pp: float
    kill_is_strict: bool  # H3/H4 kill is "< 1.0"; H1 kill is "<= 0.5"
    compares_to: str


RAW_SIGNALS: dict[str, RawSignal] = {
    "fib_hold": RawSignal("fib_hold", +1, 0, "FibonacciDetector", "FIB GOLDEN POCKET HOLD", True),
    "golden_cross": RawSignal("golden_cross", +1, WARMUP + 1, "ExpandedMACrossDetector", "GOLDEN CROSS"),
    "kumo_breakdown": RawSignal("kumo_breakdown", -1, CLOUD_WARMUP_BARS),
}

SERIES: dict[str, Series] = {
    "fib_hold": Series("fib_hold", "fib_hold"),
    "H1": Series("H1", "fib_hold", "above_trend_and_cloud", reference="fib_hold", min_bars=WARMUP + 1),
    "H3": Series("H3", "kumo_breakdown"),
    "H4": Series("H4", "golden_cross"),
}

HYPOTHESES: dict[str, Hypothesis] = {
    "H1": Hypothesis(
        "H1", "Golden-pocket hold with close > SMA-200 and above the cloud beats the plain hold",
        "H1", 1.5, 0.5, False, "the plain FIB GOLDEN POCKET HOLD on the same tickers",
    ),
    "H3": Hypothesis(
        "H3", "Kumo breakdown (bear) has an edge",
        "H3", 2.0, 1.0, True, "the unconditional down-rate of the same tickers",
    ),
    "H4": Hypothesis(
        "H4", "Golden cross, single emission, has an edge",
        "H4", 2.0, 1.0, True, "the unconditional up-rate of the same tickers",
    ),
}
NOT_RUN = {"H2": "needs the §6.2 multi-source level field, which signals-app does not implement"}


def above_trend_and_cloud(df: pd.DataFrame) -> np.ndarray:
    """Close above SMA-200 and above the cloud (``Ichimoku_CloudPos`` == +1)."""
    return ((df["Close"] > df["SMA_200"]) & (df["Ichimoku_CloudPos"] == 1.0)).to_numpy()


FILTERS = {"above_trend_and_cloud": above_trend_and_cloud}


def kumo_breakdown_fires(df: pd.DataFrame) -> np.ndarray:
    """Price crosses below the cloud: CloudPos is -1 now and was not -1 before."""
    pos = df["Ichimoku_CloudPos"]
    return ((pos == -1.0) & (pos.shift(1) >= 0.0)).to_numpy()


def find_detector_class(name: str) -> type:
    """Locate a detector class by name anywhere under ``signals_app.detection``."""
    package = importlib.import_module("signals_app.detection")
    for info in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
        module = importlib.import_module(info.name)
        candidate = getattr(module, name, None)
        if isinstance(candidate, type):
            return candidate
    raise SystemExit(f"detector class {name!r} not found under signals_app.detection")


def detector_fires(df: pd.DataFrame, raw: RawSignal, window: int) -> np.ndarray:
    """Run a detector bar by bar over causal views; True where ``raw.signal`` fires."""
    import signals_app.indicators.fibonacci as fibmath
    from signals_app.indicators.pivots import PIVOT_WINDOW, precompute_pivots

    detector = find_detector_class(raw.detector)()
    fires = np.zeros(len(df), dtype=bool)
    view = {"start": 0, "end": 0}
    original = fibmath.precompute_pivots
    if raw.needs_pivots:
        all_pivots = precompute_pivots(df, max_levels=len(df))

        def causal_pivots(_df: pd.DataFrame, max_levels: int = 20, **_: object) -> list:
            lo, hi = view["start"] + PIVOT_WINDOW, view["end"] - PIVOT_WINDOW
            return [p for p in all_pivots if lo <= p.bar_index <= hi][-max_levels:]

        fibmath.precompute_pivots = causal_pivots  # type: ignore[assignment]
    try:
        for i in range(WARMUP, len(df) - HORIZON):
            start = max(0, i - window + 1) if window else 0
            view["start"], view["end"] = start, i
            fires[i] = any(s.signal == raw.signal for s in detector.detect(df.iloc[start : i + 1]))
    finally:
        fibmath.precompute_pivots = original  # never leak the monkeypatch
    return fires


def gate_non_overlapping(fires: np.ndarray, first: int, last: int) -> np.ndarray:
    """Indices of fires kept so that no two are closer than one horizon."""
    kept: list[int] = []
    next_allowed = first
    for i in range(first, last):
        if i >= next_allowed and fires[i]:
            kept.append(i)
            next_allowed = i + HORIZON
    return np.asarray(kept, dtype=np.int64)


@dataclass
class TickerData:
    ticker: str
    month: np.ndarray  # month id per evaluated bar
    fwd: np.ndarray  # forward return per evaluated bar
    events: dict[str, np.ndarray] = field(default_factory=dict)  # series -> row indices


def evaluate_ticker(args: tuple[str, str, int, tuple[str, ...]]) -> TickerData | None:
    ticker, cache_dir, window, series_names = args
    from signals_app.indicators.compute import compute_indicators

    raw_df = ef.load_ohlcv(ticker, Path(cache_dir))
    if raw_df is None or len(raw_df) < WARMUP + HORIZON + 1:
        return None
    months = (raw_df.index.year * 12 + raw_df.index.month).to_numpy()
    df = compute_indicators(raw_df).reset_index(drop=True)
    close = df["Close"].to_numpy()
    first, last = WARMUP, len(df) - HORIZON
    fwd_all = np.full(len(df), np.nan)
    fwd_all[:last] = close[HORIZON:] / close[:last] - 1.0
    rows = np.arange(first, last)
    valid = np.isfinite(fwd_all[rows])
    rows = rows[valid]
    row_of_bar = np.full(len(df), -1, dtype=np.int64)
    row_of_bar[rows] = np.arange(len(rows))

    raw_cache: dict[str, np.ndarray] = {}
    events: dict[str, np.ndarray] = {}
    for name in series_names:
        spec = SERIES[name]
        raw = RAW_SIGNALS[spec.raw]
        if spec.raw not in raw_cache:
            raw_cache[spec.raw] = (
                kumo_breakdown_fires(df) if raw.detector is None else detector_fires(df, raw, window)
            )
        fires = raw_cache[spec.raw].copy()
        if spec.filter_name:
            fires &= FILTERS[spec.filter_name](df)
        fires &= np.isfinite(fwd_all)
        kept = gate_non_overlapping(fires, first, last)
        events[name] = row_of_bar[kept]
    return TickerData(ticker, months[rows], fwd_all[rows], events)


def series_computable(spec: Series, window: int) -> bool:
    need = max(spec.min_bars, RAW_SIGNALS[spec.raw].min_bars)
    return window == 0 or window >= need


@dataclass(frozen=True)
class Cells:
    """Per-(ticker, month) counts, the unit the bootstrap resamples."""

    ticker_idx: np.ndarray
    month_idx: np.ndarray
    base_n: np.ndarray
    base_up: np.ndarray
    base_down: np.ndarray
    ev_n: dict[str, np.ndarray]
    ev_up: dict[str, np.ndarray]
    ev_down: dict[str, np.ndarray]
    n_tickers: int
    n_months: int


def build_cells(data: list[TickerData], series_names: list[str]) -> Cells:
    month_ids = sorted({int(m) for d in data for m in np.unique(d.month)})
    month_pos = {m: k for k, m in enumerate(month_ids)}
    n_months = len(month_ids)
    n_cells = len(data) * n_months

    def zeros() -> np.ndarray:
        return np.zeros(n_cells)

    base_n, base_up, base_down = zeros(), zeros(), zeros()
    ev_n = {s: zeros() for s in series_names}
    ev_up = {s: zeros() for s in series_names}
    ev_down = {s: zeros() for s in series_names}
    for t, d in enumerate(data):
        cell = t * n_months + np.array([month_pos[int(m)] for m in d.month], dtype=np.int64)
        np.add.at(base_n, cell, 1.0)
        np.add.at(base_up, cell, (d.fwd > 0).astype(float))
        np.add.at(base_down, cell, (d.fwd < 0).astype(float))
        for s in series_names:
            idx = d.events.get(s, np.empty(0, dtype=np.int64))
            if idx.size == 0:
                continue
            np.add.at(ev_n[s], cell[idx], 1.0)
            np.add.at(ev_up[s], cell[idx], (d.fwd[idx] > 0).astype(float))
            np.add.at(ev_down[s], cell[idx], (d.fwd[idx] < 0).astype(float))
    ticker_idx = np.repeat(np.arange(len(data)), n_months)
    month_idx = np.tile(np.arange(n_months), len(data))
    return Cells(ticker_idx, month_idx, base_n, base_up, base_down, ev_n, ev_up, ev_down, len(data), n_months)


def _hit(num: np.ndarray, den: np.ndarray, weights: np.ndarray) -> float:
    total = float((den * weights).sum())
    return float((num * weights).sum()) / total if total > 0 else float("nan")


def edge_pp(cells: Cells, series: str, side: int, reference: str | None, weights: np.ndarray) -> float:
    """Hit-rate edge in percentage points, weighted by ``weights`` per cell."""
    own = cells.ev_up[series] if side > 0 else cells.ev_down[series]
    own_hit = _hit(own, cells.ev_n[series], weights)
    if reference is None:
        ref = cells.base_up if side > 0 else cells.base_down
        ref_hit = _hit(ref, cells.base_n, weights)
    else:
        ref = cells.ev_up[reference] if side > 0 else cells.ev_down[reference]
        ref_hit = _hit(ref, cells.ev_n[reference], weights)
    return 100.0 * (own_hit - ref_hit)


def two_way_bootstrap(
    cells: Cells, series: str, side: int, reference: str | None,
    draws: int = BOOTSTRAP_DRAWS, seed: int = BOOTSTRAP_SEED,
) -> dict[str, float]:
    """Resample tickers and months independently; each cell's weight is the
    product of its ticker's and its month's draw counts."""
    rng = np.random.default_rng(seed)
    edges = np.empty(draws)
    for k in range(draws):
        w_ticker = np.bincount(rng.integers(0, cells.n_tickers, cells.n_tickers), minlength=cells.n_tickers)
        w_month = np.bincount(rng.integers(0, cells.n_months, cells.n_months), minlength=cells.n_months)
        edges[k] = edge_pp(cells, series, side, reference, w_ticker[cells.ticker_idx] * w_month[cells.month_idx].astype(float))
    edges = edges[np.isfinite(edges)]
    point = edge_pp(cells, series, side, reference, np.ones(len(cells.base_n)))
    if edges.size == 0:
        return {"edge_pp": point, "p_one_sided": float("nan"), "ci_low": float("nan"), "ci_high": float("nan")}
    return {
        "edge_pp": point,
        "p_one_sided": (1 + float((edges <= 0).sum())) / (edges.size + 1),
        "ci_low": float(np.percentile(edges, 2.5)),
        "ci_high": float(np.percentile(edges, 97.5)),
    }


def verdict(h: Hypothesis, edge: float, p: float, n: int) -> str:
    if n < MIN_EVENTS_FOR_VERDICT or not math.isfinite(edge):
        return "INCONCLUSIVE (too few events)"
    if edge >= h.goal_pp and p < SIGNIFICANCE:
        return "PROMOTE"
    killed = edge < h.kill_pp if h.kill_is_strict else edge <= h.kill_pp
    return "KILL" if killed else "INCONCLUSIVE"


def summarise_series(cells: Cells, spec: Series, side: int, draws: int) -> dict:
    n = int(cells.ev_n[spec.name].sum())
    tickers = int(sum(1 for t in range(cells.n_tickers)
                      if cells.ev_n[spec.name][t * cells.n_months:(t + 1) * cells.n_months].sum() > 0))
    if n == 0:
        return {"n": 0, "tickers": 0}
    own = cells.ev_up[spec.name] if side > 0 else cells.ev_down[spec.name]
    ones = np.ones(len(cells.base_n))
    ref_series = spec.reference
    out = {
        "n": n,
        "tickers": tickers,
        "hit_rate": _hit(own, cells.ev_n[spec.name], ones),
        "baseline": _hit(cells.base_up if side > 0 else cells.base_down, cells.base_n, ones),
        "compared_to": ref_series or "baseline",
    }
    if ref_series:
        out["reference_n"] = int(cells.ev_n[ref_series].sum())
    out.update(two_way_bootstrap(cells, spec.name, side, ref_series, draws))
    return out


def run_window(
    tickers: list[str], cache: Path, window: int, names: list[str], workers: int, draws: int,
) -> dict:
    runnable = [n for n in names if series_computable(SERIES[n], window)]
    skipped = {n: f"needs >= {max(SERIES[n].min_bars, RAW_SIGNALS[SERIES[n].raw].min_bars)} bars; window is {window}"
               for n in names if n not in runnable}
    if not runnable:
        return {"window": window or "full", "series": {}, "not_computable": skipped}
    with mp.get_context("spawn").Pool(workers) as pool:
        data = [d for d in pool.map(evaluate_ticker, [(t, str(cache), window, tuple(runnable)) for t in tickers]) if d]
    cells = build_cells(data, runnable)
    summary = {
        n: summarise_series(cells, SERIES[n], RAW_SIGNALS[SERIES[n].raw].side, draws) for n in runnable
    }
    return {
        "window": window or "full",
        "tickers_evaluated": len(data),
        "tickers_requested": len(tickers),
        "series": summary,
        "not_computable": skipped,
    }


def prefetch(tickers: list[str], cache: Path) -> tuple[int, list[str]]:
    """Batch-download 5y daily bars into the cache; returns (cached, missing)."""
    import yfinance as yf

    missing: list[str] = []
    todo = [t for t in tickers if not (cache / f"{t}.pkl").exists()]
    for start in range(0, len(todo), PREFETCH_CHUNK):
        chunk = todo[start : start + PREFETCH_CHUNK]
        frame = yf.download(chunk, period=ef.PERIOD, interval="1d", auto_adjust=True,
                            group_by="ticker", threads=True, progress=False)
        for t in chunk:
            try:
                sub = frame[t][["Open", "High", "Low", "Close", "Volume"]].dropna()
            except KeyError:
                sub = pd.DataFrame()
            if len(sub) < WARMUP + HORIZON + 1:
                missing.append(t)
                continue
            sub.to_pickle(cache / f"{t}.pkl")
        logger.info("prefetch %d/%d", min(start + PREFETCH_CHUNK, len(todo)), len(todo))
    return len(tickers) - len(missing), missing


def render_report(h: Hypothesis, result_by_window: list[dict], meta: dict) -> str:
    spec = SERIES[h.series]
    side = RAW_SIGNALS[spec.raw].side
    lines = [
        f"# {h.key}: {h.title}",
        "",
        f"Run {meta['date']} by `scripts/eval_detector.py` (code `{meta['code_version']}`). "
        f"Pre-registered before the run (FIB-ICHIMOKU-MA.md §6.4): goal **+{h.goal_pp} pts**, "
        f"kill line **{'<' if h.kill_is_strict else '<='} +{h.kill_pp} pts**, compared with {h.compares_to}.",
        "",
        f"Side: {'bullish (forward 21-bar return > 0)' if side > 0 else 'bearish (forward 21-bar return < 0)'}. "
        "Events are non-overlapping per ticker. Interval: two-way block bootstrap "
        f"({meta['draws']} draws) over tickers and calendar months; p is one-sided for edge > 0.",
        "",
        "| Window | Events | Tickers | Hit rate | Compared to | Edge (pts) | 95% CI | p | Verdict |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for res in result_by_window:
        row = res["series"].get(h.series)
        if row is None:
            reason = res["not_computable"].get(h.series, "not run")
            lines.append(f"| {res['window']} | - | - | - | - | - | - | - | not computable: {reason} |")
            continue
        if row["n"] == 0:
            lines.append(f"| {res['window']} | 0 | 0 | - | - | - | - | - | INCONCLUSIVE (no events) |")
            continue
        lines.append(
            f"| {res['window']} | {row['n']} | {row['tickers']} | {row['hit_rate']:.1%} "
            f"| {row['compared_to']} ({row['baseline']:.1%} base) | {row['edge_pp']:+.2f} "
            f"| [{row['ci_low']:+.2f}, {row['ci_high']:+.2f}] | {row['p_one_sided']:.3f} "
            f"| {verdict(h, row['edge_pp'], row['p_one_sided'], row['n'])} |"
        )
    first = result_by_window[0]
    lines += [
        "",
        f"Universe: {first.get('tickers_evaluated', 0)} of {first.get('tickers_requested', 0)} requested tickers "
        f"had enough 5y daily history (yfinance, auto-adjusted).",
        "",
        "## Read this as",
        "",
        "- A verdict is about this universe, this 5-year window and this horizon only. 5 years of a mostly rising "
        "market makes any long signal's baseline high and any short signal's low.",
        "- Hit rate ignores magnitude. A signal can hit more often and still lose money.",
        "- The full-history row lets indicators warm up on all prior bars. The 63-bar row, where computable, "
        "is the production scan's view.",
        "- Survivorship: the universe is today's ticker list, so delisted names are absent.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache", required=True, help="Directory for cached OHLCV pickle files")
    parser.add_argument("--prefetch", action="store_true", help="Download the universe into --cache, then exit")
    parser.add_argument("--run", nargs="*", default=None, choices=sorted(HYPOTHESES), help="Hypotheses to run")
    parser.add_argument("--detector", help="Custom evaluation: detector class name")
    parser.add_argument("--signal", help="Custom evaluation: exact signal label")
    parser.add_argument("--side", choices=("bull", "bear"), default="bull")
    parser.add_argument("--window", type=int, action="append", help="Bars the detector sees (repeatable; 0 = full)")
    parser.add_argument("--sample-size", type=int, default=0, help="Tickers drawn from the universe (0 = all)")
    parser.add_argument("--seed", type=int, default=ef.SAMPLE_SEED)
    parser.add_argument("--workers", type=int, default=max(1, mp.cpu_count() - 1))
    parser.add_argument("--draws", type=int, default=BOOTSTRAP_DRAWS)
    parser.add_argument("--out", help="Write the raw JSON here")
    parser.add_argument("--report-dir", help="Write one markdown report per hypothesis here")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("signals_app").setLevel(logging.WARNING)

    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    tickers = ef.sample_tickers(args.sample_size, args.seed)
    if args.prefetch:
        cached, missing = prefetch(tickers, cache)
        print(json.dumps({"requested": len(tickers), "cached": cached, "missing": missing}, indent=2))
        return

    names: list[str] = []
    if args.detector:
        if not args.signal:
            parser.error("--detector needs --signal")
        RAW_SIGNALS["custom"] = RawSignal("custom", +1 if args.side == "bull" else -1, 0, args.detector, args.signal,
                                          needs_pivots=args.detector == "FibonacciDetector")
        SERIES["custom"] = Series("custom", "custom")
        names.append("custom")
    selected = args.run if args.run is not None else ([] if args.detector else sorted(HYPOTHESES))
    for key in selected:
        for series in (SERIES[HYPOTHESES[key].series].reference, HYPOTHESES[key].series):
            if series and series not in names:
                names.append(series)

    windows = args.window if args.window else [0]
    results = [run_window(tickers, cache, w, names, args.workers, args.draws) for w in windows]
    from signals_app.config import SIGNALS_APP_CODE_VERSION

    meta = {"date": dt.date.today().isoformat(), "code_version": SIGNALS_APP_CODE_VERSION, "draws": args.draws}
    payload = {"meta": meta, "not_run": {k: NOT_RUN[k] for k in NOT_RUN}, "results": results}
    print(json.dumps(payload, indent=2, default=float))
    if args.out:
        Path(args.out).write_text(json.dumps(payload, indent=2, default=float))
    if args.report_dir:
        out_dir = Path(args.report_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for key in selected:
            path = out_dir / f"eval-{key.lower()}-{meta['date']}.md"
            path.write_text(render_report(HYPOTHESES[key], results, meta))
            logger.info("wrote %s", path)


if __name__ == "__main__":
    main()
