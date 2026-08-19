#!/usr/bin/env python3
"""Closes the calibration loop (Phase 7 of docs/backend-state-and-supabase-plan.md).

ConfluenceRanker.rank_signals() already accepts strength_hit_rates, and
scripts/scan_universe.py's publication gate can already pass one in — the
missing piece was durable, shared storage: the original scripts/calibrate.py
writes a local JSON file that doesn't survive a container restart, so the
mechanism existed but never actually calibrated anything running in CI.

This script:
  1. Computes forward_returns for every detector_hits bar old enough to
     have a realized horizon_days-ahead return.
  2. Joins detector_hits against forward_returns -> hit-rate per strength,
     per category, per confluence_band.
  3. Writes a new calibration generation (is_active=false), then activates
     it only after every row is written — a failed run never leaves a
     partial generation live.

Usage:
    python scripts/calibrate_supabase.py
    python scripts/calibrate_supabase.py --horizon-days 5
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

_script_dir = Path(__file__).resolve().parent
_project_root = _script_dir.parent
sys.path.insert(0, str(_project_root / "src"))
sys.path.insert(0, str(_project_root))

from signals_app.config import BACKTEST_FORWARD_HORIZON_DAYS, get_settings  # noqa: E402
from signals_app.data.fetcher import DataFetcher  # noqa: E402
from signals_app.db.calibration_store import (  # noqa: E402
    CalibrationStore,
    ForwardReturnRow,
    score_hits_against_returns,
)

logger = logging.getLogger(__name__)

# Calendar-day margin over horizon_days trading days, so a bar is only
# considered "old enough to score" once its forward bar has definitely
# happened (weekends/holidays mean trading days != calendar days).
CALENDAR_DAY_MARGIN = 3


def _to_utc_iso(ts: pd.Timestamp | str) -> str:
    """Normalize a pandas Timestamp or an ISO-8601 string to a UTC isoformat
    string, so timestamps that entered Postgres with one tz offset and come
    back with another (Postgres always normalizes to UTC on storage) still
    compare equal.
    """
    parsed = pd.Timestamp(ts)
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize("UTC")
    else:
        parsed = parsed.tz_convert("UTC")
    return str(parsed.isoformat())


def compute_and_write_forward_returns(
    store: CalibrationStore, horizon_days: int
) -> dict[tuple[str, str], float]:
    """Fetch unresolved detector_hits, compute their forward returns from
    yfinance (one fetch per distinct ticker, not per bar), write them, and
    return the full (ticker, bar_ts) -> pct_return map for this horizon
    (existing + newly computed) for the caller to join against.
    """
    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(days=horizon_days + CALENDAR_DAY_MARGIN)
    hits = store.fetch_unresolved_detector_hits(horizon_days, cutoff.isoformat())

    already_resolved = {
        (r.ticker, r.bar_ts): r.pct_return for r in store.fetch_forward_returns(horizon_days)
    }

    # Group unresolved (ticker, bar_ts) pairs by ticker so each ticker is
    # fetched once, not once per bar.
    tickers_needed: dict[str, set[str]] = {}
    for hit in hits:
        if (hit.ticker, hit.bar_ts) in already_resolved:
            continue
        tickers_needed.setdefault(hit.ticker, set()).add(hit.bar_ts)

    new_rows: list[ForwardReturnRow] = []
    for ticker, bar_timestamps in tickers_needed.items():
        try:
            fetcher = DataFetcher(settings=settings)
            df = fetcher.fetch(ticker, "5y").df
        except Exception as exc:
            logger.warning("calibrate: failed to fetch %s for forward returns: %s", ticker, exc)
            continue

        # Postgres/PostgREST normalizes any timezone offset to UTC on
        # storage (e.g. writer sends "...-04:00", detector_hits.bar_ts reads
        # back as "...+00:00" for the same instant) — comparing raw
        # isoformat() strings would silently fail to match. Key both sides
        # on the UTC-normalized instant instead.
        index_pos = {_to_utc_iso(ts): pos for pos, ts in enumerate(df.index)}
        for bar_ts in bar_timestamps:
            pos = index_pos.get(_to_utc_iso(bar_ts))
            if pos is None or pos + horizon_days >= len(df):
                continue
            close = float(df.iloc[pos]["Close"])
            forward_close = float(df.iloc[pos + horizon_days]["Close"])
            # NaN-safe: bool(nan) is True, so `if close` would let a NaN
            # close silently through.
            if close != close or forward_close != forward_close or close == 0.0:
                continue
            pct_return = (forward_close - close) / close
            new_rows.append(
                ForwardReturnRow(
                    ticker=ticker, bar_ts=bar_ts, horizon_days=horizon_days, pct_return=pct_return
                )
            )

    store.write_forward_returns(new_rows)
    logger.info("calibrate: computed %d new forward_returns rows", len(new_rows))

    for row in new_rows:
        already_resolved[(row.ticker, row.bar_ts)] = row.pct_return
    return already_resolved


def run_calibration(horizon_days: int = BACKTEST_FORWARD_HORIZON_DAYS) -> int:
    """Run the full calibration pass. Returns the number of calibration
    rows written (0 if no bucket cleared CALIBRATION_MIN_BUCKET_SIZE).
    """
    with CalibrationStore() as store:
        returns = compute_and_write_forward_returns(store, horizon_days)

        cutoff = datetime.now(UTC) - timedelta(days=horizon_days + CALENDAR_DAY_MARGIN)
        hits = store.fetch_unresolved_detector_hits(horizon_days, cutoff.isoformat(), limit=20000)

        buckets = score_hits_against_returns(hits, returns)
        written: int = store.write_calibration_generation(horizon_days, buckets)

        if written > 0:
            store.activate_latest_generation(horizon_days)

        return written


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--horizon-days", type=int, default=BACKTEST_FORWARD_HORIZON_DAYS)
    args = parser.parse_args()

    written = run_calibration(horizon_days=args.horizon_days)
    print(f"Wrote {written} calibration rows for horizon_days={args.horizon_days}")


if __name__ == "__main__":
    main()
