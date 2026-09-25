#!/usr/bin/env python3
"""Train the learned scorer (docs/scoring-2x-plan.md P3/P4) and optionally publish it.

Builds a point-in-time panel over the universe (features at every ``--step``-th
bar plus forward excess-return labels), runs purged walk-forward CV for the
requested feature sets, adopts the richer rung only when it beats the simpler
one by more than a standard error, calibrates with isotonic regression, scores
the final holdout once, and writes the artifact + a markdown report.

The artifact is only written / published when it clears the plan's absolute
ship bar (IC >= 0.02 at 20d, t >= 2, non-negative in every regime, reliability
within 5pp) — pass --force to override, which the report will say.

Exit codes: 0 written/published; 3 trained but not written (ship bar missed or too few
symbols); anything else is a real failure.

Usage:
    python scripts/train_scorer.py --limit 60                      # smoke run
    python scripts/train_scorer.py --asset-type Equity --publish   # weekly job
    python scripts/train_scorer.py --stack                         # also train the timeframe meta-model
"""
from __future__ import annotations

import argparse
import logging
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

_script_dir = Path(__file__).resolve().parent
_project_root = _script_dir.parent
sys.path.insert(0, str(_project_root / "src"))
sys.path.insert(0, str(_project_root))

from backtests.dataset import build_symbol_panel  # noqa: E402
from backtests.stacking import (  # noqa: E402
    attach_interval_probabilities,
    build_interval_panel,
    interval_oof,
    stack_beats_base,
)
from backtests.train import TrainResult, adopt_richer_rung, train_scorer  # noqa: E402
from signals_app.config import get_settings  # noqa: E402
from signals_app.data.fetcher import DataFetcher  # noqa: E402
from signals_app.scoring.model import DEFAULT_MODEL_PATH  # noqa: E402
from signals_app.scoring.mtf import STACK_FEATURES  # noqa: E402
from signals_app.scoring.regime import regime_series  # noqa: E402

logger = logging.getLogger(__name__)

BENCHMARK_SYMBOL = "SPY"
DEFAULT_SEED = _project_root / "seed" / "universe_symbols.csv"
STACK_META_PATH = _project_root / "calibration" / "scorer_stack_meta.json"
MIN_SYMBOLS_FOR_PUBLISH = 40
EXIT_NOT_PUBLISHED = 3  # ship bar missed / too few symbols: expected, not an error


def _symbol_worker(args: tuple) -> dict[str, pd.DataFrame]:
    """Fetch one symbol and build its daily (+ optional weekly/monthly) panels."""
    symbol, benchmark, regimes, horizons, step, period, stack = args
    try:
        ohlcv = DataFetcher(settings=get_settings()).fetch_daily_history(symbol, period)
        panels = {"daily": build_symbol_panel(symbol, ohlcv, benchmark, regimes, horizons=horizons, step=step)}
        if stack:
            for interval in ("weekly", "monthly"):
                panels[interval] = build_interval_panel(symbol, interval, ohlcv, benchmark, regimes)
        return panels
    except Exception as exc:  # noqa: BLE001 — one bad symbol must not sink the run
        logger.warning("train: %s skipped: %s", symbol, exc)
        return {}


def gather_panels(
    symbols: list[str], benchmark: pd.DataFrame, regimes: pd.Series, horizons: tuple[int, ...],
    step: int, period: str, workers: int, stack: bool,
) -> dict[str, pd.DataFrame]:
    jobs = [(s, benchmark, regimes, horizons, step, period, stack) for s in symbols]
    collected: dict[str, list[pd.DataFrame]] = {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for done, panels in enumerate(pool.map(_symbol_worker, jobs), 1):
            for interval, frame in panels.items():
                if not frame.empty:
                    collected.setdefault(interval, []).append(frame)
            if done % 25 == 0:
                logger.info("train: %d/%d symbols built", done, len(jobs))
    return {k: pd.concat(v, ignore_index=True) for k, v in collected.items()}


def load_symbols(seed: Path, limit: int | None, asset_type: str | None) -> list[str]:
    frame = pd.read_csv(seed)
    if asset_type:
        frame = frame[frame["asset_type"] == asset_type]
    symbols = sorted(frame["ticker"].astype(str).str.upper().unique())
    return symbols[:limit] if limit else symbols


def render_report(results: dict[str, TrainResult], chosen: str, horizon: int, n_symbols: int, forced: bool) -> str:
    lines = [
        f"# Scorer training report — {datetime.now(UTC):%Y-%m-%d}",
        "",
        f"Horizon {horizon} bars · {n_symbols} symbols · chosen feature set **{chosen}**"
        + (" · ⚠️ published with --force despite missing the ship bar" if forced else ""),
        "",
        "| Feature set | OOF rank IC | t-stat | Decile spread | Monotone | Holdout IC | Reliability gap | Ship bar |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, r in results.items():
        o, h = r.oof_report, r.holdout_report
        lines.append(
            f"| {name} | {o.rank_ic:.4f} | {o.ic_t_stat:.2f} | {o.decile_spread:.4f} | {o.monotone_deciles} | "
            f"{'n/a' if h is None else f'{h.rank_ic:.4f}'} | {r.holdout_reliability_gap:.3f} | "
            f"{'met' if r.ship_bar_met else 'MISSED'} |"
        )
    for name, r in results.items():
        lines += ["", f"## {name} — per-regime rank IC", "", "| Regime | Rank IC | t | Dates |", "|---|---|---|---|"]
        lines += [f"| {x.regime} | {x.rank_ic:.4f} | {x.ic_t_stat:.2f} | {x.n_dates} |" for x in r.regime_ic.itertuples()]
        if r.ship_bar_failures:
            lines += ["", "Ship-bar failures:"] + [f"- {f}" for f in r.ship_bar_failures]
    return "\n".join(lines) + "\n"


def train_stack(panels: dict[str, pd.DataFrame], horizon: int, step: int, n_splits: int) -> None:
    """Train + report the timeframe meta-model on out-of-fold interval probabilities."""
    oofs = {i: interval_oof(panels[i], i, step if i == "daily" else 1, n_splits) for i in ("daily", "weekly", "monthly") if i in panels}
    stacked = attach_interval_probabilities(panels["daily"], oofs)
    base = train_scorer(panels["daily"], horizon, "rung2", step, n_splits)
    meta = train_scorer(stacked, horizon, step=step, n_splits=n_splits, features=STACK_FEATURES, model_version="stack-meta")
    failures = stack_beats_base(meta.oof_report, base.oof_report, meta.regime_ic, base.regime_ic)
    print(f"stack OOF IC {meta.oof_report.rank_ic:.4f} vs base {base.oof_report.rank_ic:.4f}")
    if failures:
        print("stack NOT adopted:", *failures, sep="\n  - ")
        return
    meta.scorer.save(STACK_META_PATH)
    print(f"stack meta-model written to {STACK_META_PATH}")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--asset-type", default="Equity")
    ap.add_argument("--period", default="10y")
    ap.add_argument("--horizon", type=int, default=20, choices=(5, 20, 60))
    ap.add_argument("--step", type=int, default=3, help="sample every Nth bar")
    ap.add_argument("--splits", type=int, default=5)
    ap.add_argument("--holdout-months", type=int, default=12)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", type=Path, default=DEFAULT_MODEL_PATH)
    ap.add_argument("--report-dir", type=Path, default=_project_root / "docs")
    ap.add_argument("--cache", type=Path, help="CSV.gz panel cache: read if present, else written")
    ap.add_argument("--publish", action="store_true", help="also activate the model in Supabase")
    ap.add_argument("--force", action="store_true", help="write/publish even if the ship bar is missed")
    ap.add_argument("--stack", action="store_true", help="also train the timeframe meta-model (P6)")
    args = ap.parse_args()

    symbols = load_symbols(args.seed, args.limit, args.asset_type)
    fetcher = DataFetcher(settings=get_settings())
    benchmark = fetcher.fetch_daily_history(BENCHMARK_SYMBOL, args.period)
    regimes = regime_series(benchmark)

    if args.cache and args.cache.exists() and not args.stack:
        panels = {"daily": pd.read_csv(args.cache, parse_dates=["date"])}
    else:
        panels = gather_panels(symbols, benchmark, regimes, (5, 20, 60), args.step, args.period, args.workers, args.stack)
        if args.cache and "daily" in panels:
            panels["daily"].to_csv(args.cache, index=False)
    daily = panels["daily"]
    n_symbols = int(daily["symbol"].nunique())
    print(f"panel: {len(daily)} rows over {n_symbols} symbols")

    results = {
        name: train_scorer(daily, args.horizon, name, args.step, args.splits, args.holdout_months)
        for name in ("rung1", "rung2")
    }
    chosen = "rung2" if adopt_richer_rung(results["rung1"].oof_report, results["rung2"].oof_report) else "rung1"
    winner = results[chosen]
    forced = args.force and not winner.ship_bar_met

    report = render_report(results, chosen, args.horizon, n_symbols, forced)
    report_path = args.report_dir / f"scorer-report-{datetime.now(UTC):%Y%m%d}.md"
    report_path.write_text(report)
    print(report)
    print(f"report: {report_path}")

    if args.stack:
        train_stack(panels, args.horizon, args.step, args.splits)

    if not winner.ship_bar_met and not args.force:
        print("Ship bar missed — model NOT written. Re-run with --force to override.")
        return EXIT_NOT_PUBLISHED
    winner.scorer.save(args.out)
    print(f"model written to {args.out} ({winner.scorer.model_version})")

    if args.publish:
        if n_symbols < MIN_SYMBOLS_FOR_PUBLISH and not args.force:
            print(f"Refusing to publish a model trained on {n_symbols} symbols (< {MIN_SYMBOLS_FOR_PUBLISH}).")
            return EXIT_NOT_PUBLISHED
        from signals_app.db.scorer_store import ScorerStore

        with ScorerStore() as store:
            store.publish_model(winner.scorer)
        print("published and activated in Supabase")
    return 0


if __name__ == "__main__":
    sys.exit(main())
