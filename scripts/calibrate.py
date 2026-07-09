#!/usr/bin/env python3
"""Calibration CLI — backtests a basket of symbols and writes the strength
hit-rate table that the live /signals/{symbol} route uses to calibrate
confidence_label (see scoring/confluence.py's strength_hit_rates param).

Usage:
    python scripts/calibrate.py AAPL MSFT GOOGL SPY QQQ
    python scripts/calibrate.py AAPL MSFT --period 2y --horizon-days 5
    python scripts/calibrate.py AAPL MSFT --output ./calibration/strength_hit_rates.json

Run this periodically (weekly/monthly) as data accumulates — it is NOT run
automatically per-request, since a full historical scan is too slow for a
live API call.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_script_dir = Path(__file__).resolve().parent
_project_root = _script_dir.parent
sys.path.insert(0, str(_project_root / "src"))
sys.path.insert(0, str(_project_root))

from backtests.engine import merge_hit_rate_buckets, score_historical_signals
from signals_app.config import (
    BACKTEST_FORWARD_HORIZON_DAYS,
    CALIBRATION_FILE,
    DEFAULT_PERIOD,
    MIN_HISTORICAL_LOOKBACK,
    get_settings,
)
from signals_app.data.fetcher import DataFetcher
from signals_app.detection.historical import scan_historical
from signals_app.indicators.compute import compute_indicators
from signals_app.scoring.calibration import derive_strength_hit_rates, save_strength_hit_rates

logger = logging.getLogger(__name__)


def run_calibration(
    symbols: list[str],
    period: str,
    horizon_days: int,
    output_path: str,
) -> dict[str, float]:
    """Backtest every symbol and write a merged strength hit-rate table.

    Args:
        symbols: Tickers to backtest.
        period: yfinance period string — long enough to clear warmup + horizon.
        horizon_days: Forward-return horizon in trading days.
        output_path: Where to write the resulting JSON calibration file.

    Returns:
        The merged, size-filtered strength hit-rate map that was written.
    """
    settings = get_settings()
    by_strength_lists = []

    for symbol in symbols:
        symbol = symbol.upper().strip()
        try:
            df_raw = DataFetcher(settings=settings).fetch(symbol, period).df
            if len(df_raw) <= MIN_HISTORICAL_LOOKBACK + horizon_days:
                logger.warning("calibrate: skipping %s — insufficient bars (%d)", symbol, len(df_raw))
                continue
            df = compute_indicators(df_raw)
            bars = scan_historical(df)
            result = score_historical_signals(df, bars, horizon_days=horizon_days)
            by_strength_lists.append(result["by_strength"])
            logger.info("calibrate: %s — scanned %d bars", symbol, len(bars))
        except Exception as exc:
            logger.warning("calibrate: %s failed, skipping: %s", symbol, exc)

    merged = merge_hit_rate_buckets(by_strength_lists)
    rates = derive_strength_hit_rates(merged)
    save_strength_hit_rates(rates, path=output_path)
    return rates


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("symbols", nargs="+", help="Ticker symbols to backtest")
    parser.add_argument("--period", default="2y", help=f"yfinance period (default: 2y; app default: {DEFAULT_PERIOD})")
    parser.add_argument("--horizon-days", type=int, default=BACKTEST_FORWARD_HORIZON_DAYS)
    parser.add_argument("--output", default=CALIBRATION_FILE)
    args = parser.parse_args()

    rates = run_calibration(args.symbols, args.period, args.horizon_days, args.output)
    print(f"Wrote {len(rates)} calibrated strength buckets to {args.output}")
    for key, rate in sorted(rates.items()):
        print(f"  {key}: {rate:.2%}")


if __name__ == "__main__":
    main()
