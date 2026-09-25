#!/usr/bin/env python3
"""Weekly live-vs-backtest IC drift check (docs/scoring-2x-plan.md P7).

For published signals old enough to have a realized 20-day outcome, computes the
rank IC of ``p_outperform`` against realized excess return per week, records it
in ``scorer_ic_history`` next to the model's backtest IC, and exits 1 when live
IC has been below half the backtest IC for four straight weeks (so the GitHub
Actions run fails and notifies).

Caveat: only *published* names carry a p_outperform, so the live cross-section
is truncated to what cleared the gate — a conservative (range-restricted) IC.
"""
from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root / "src"))
sys.path.insert(0, str(_project_root))

from signals_app.config import get_settings  # noqa: E402
from signals_app.data.fetcher import DataFetcher  # noqa: E402
from signals_app.db.scorer_store import ScorerStore  # noqa: E402
from signals_app.scoring.drift import DRIFT_WEEKS, drift_alert, weekly_live_ic  # noqa: E402
from signals_app.scoring.model import load_scorer_from_supabase  # noqa: E402

logger = logging.getLogger(__name__)

HORIZON_DAYS = 20
CALENDAR_MARGIN_DAYS = 32  # 20 trading days ~ 28 calendar days, plus slack
BENCHMARK_SYMBOL = "SPY"


def realized_excess(signals: pd.DataFrame, horizon: int = HORIZON_DAYS) -> pd.Series:
    """Forward excess return over SPY for each (ticker, bar_ts); NaN when not yet known."""
    fetcher = DataFetcher(settings=get_settings())
    bench = fetcher.fetch_daily_history(BENCHMARK_SYMBOL, "2y")["Close"]
    out = pd.Series(float("nan"), index=signals.index)
    for ticker, group in signals.groupby("ticker"):
        try:
            close = fetcher.fetch_daily_history(str(ticker), "2y")["Close"]
        except Exception as exc:  # noqa: BLE001
            logger.warning("drift: %s skipped: %s", ticker, exc)
            continue
        fwd = close.shift(-horizon) / close - 1.0
        bench_aligned = bench.reindex(close.index, method="ffill")
        excess = fwd - (bench_aligned.shift(-horizon) / bench_aligned - 1.0)
        excess.index = excess.index.tz_localize(None).normalize() if excess.index.tz is not None else excess.index.normalize()
        for idx, ts in group["bar_ts"].items():
            day = pd.Timestamp(ts).tz_localize(None).normalize() if pd.Timestamp(ts).tzinfo else pd.Timestamp(ts).normalize()
            out.loc[idx] = excess.get(day, float("nan"))
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    model = load_scorer_from_supabase(HORIZON_DAYS)
    if model is None:
        print("No active scorer model — nothing to monitor.")
        return 0
    backtest_ic = float(model.metrics.get("oof", {}).get("rank_ic", float("nan")))
    if backtest_ic != backtest_ic or backtest_ic <= 0:
        print(f"Active model {model.model_version} has no positive backtest IC — cannot judge drift.")
        return 0

    cutoff = (datetime.now(UTC) - timedelta(days=CALENDAR_MARGIN_DAYS)).isoformat()
    with ScorerStore() as store:
        rows = [r for r in store.fetch_scored_signals(cutoff) if r.get("model_version") == model.model_version]
        if not rows:
            print("No resolved scored signals yet.")
            return 0
        frame = pd.DataFrame(rows)
        frame["realized_excess"] = realized_excess(frame)
        weekly = weekly_live_ic(frame)
        for row in weekly.itertuples():
            store.append_ic({
                "week_start": row.week_start.date().isoformat(),
                "model_version": model.model_version,
                "live_ic": float(row.live_ic),
                "backtest_ic": backtest_ic,
                "n_dates": int(row.n_dates),
                "n_names": int(row.n_names),
            })
        history = store.fetch_ic_history(model.model_version, limit=DRIFT_WEEKS)

    for h in history:
        print(f"week {h['week_start']}: live IC {h['live_ic']} vs backtest {h['backtest_ic']:.4f}")
    if drift_alert(history):
        print(f"::error::Scorer drift: live 20d IC below half of backtest ({backtest_ic:.4f}) for {DRIFT_WEEKS} straight weeks")
        return 1
    print("No drift.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
