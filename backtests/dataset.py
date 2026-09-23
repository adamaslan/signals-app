"""Point-in-time training panel: features at each sampled bar plus forward labels.

Built from ``compute_indicators`` output (one vectorised pass per symbol) and
the same detectors the live scan runs, evaluated only on the sampled bars.
Labels are forward *excess* returns against a benchmark (plan §3): raw and
volatility-scaled, at several horizons.
"""
from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd

from backtests.engine import excess_target
from signals_app.config import MIN_HISTORICAL_LOOKBACK
from signals_app.detection.base import MutableSignal
from signals_app.detection.orchestrator import get_default_detectors
from signals_app.indicators.compute import compute_indicators
from signals_app.scoring.features import build_feature_row, continuous_features_frame

logger = logging.getLogger(__name__)

DEFAULT_HORIZONS = (5, 20, 60)
DEFAULT_SAMPLE_STEP = 3
VOL_LOOKBACK = 20


def _signals_at(df: pd.DataFrame, pos: int, detectors: list) -> list[MutableSignal]:
    window = df.iloc[: pos + 1]
    found: list[MutableSignal] = []
    for detector in detectors:
        try:
            found.extend(detector.detect(window))
        except Exception as exc:  # noqa: BLE001 — one bad detector must not sink the symbol
            logger.debug("detector %s failed at %s: %s", detector.__class__.__name__, window.index[-1], exc)
    return found


def build_symbol_panel(
    symbol: str,
    ohlcv: pd.DataFrame,
    benchmark: pd.DataFrame,
    regimes: pd.Series,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    step: int = DEFAULT_SAMPLE_STEP,
    min_lookback: int = MIN_HISTORICAL_LOOKBACK,
    detectors: list | None = None,
) -> pd.DataFrame:
    """One row per sampled bar for one symbol.

    Args:
        symbol: Ticker (stored in the ``symbol`` column).
        ohlcv: Daily OHLCV, oldest-first.
        benchmark: Benchmark OHLCV (e.g. SPY) covering the same dates.
        regimes: ``scoring.regime.regime_series`` of the benchmark.
        horizons: Forward horizons in bars for the labels.
        step: Sample every ``step``-th bar (detector calls dominate runtime).
        min_lookback: Warm-up bars skipped before the first sample.
        detectors: Override the default detector set.

    Returns:
        Frame with ``date``, ``symbol``, ``regime``, every rung-2 feature, and
        ``fwd_excess_{h}`` / ``target_scaled_{h}`` for each horizon (NaN where the
        horizon runs past the data). Empty when there is too little history.
    """
    if len(ohlcv) <= min_lookback:
        return pd.DataFrame()
    detectors = detectors if detectors is not None else get_default_detectors()
    df = compute_indicators(ohlcv)
    close = df["Close"].astype(float)
    bench_close = benchmark["Close"].astype(float).reindex(df.index, method="ffill")
    cont = continuous_features_frame(df, benchmark["Close"].astype(float))
    vol = close.pct_change().rolling(VOL_LOOKBACK, min_periods=VOL_LOOKBACK).std()
    regime_at = regimes.reindex(df.index, method="ffill")

    labels: dict[str, pd.Series] = {}
    for h in horizons:
        fwd = close.shift(-h) / close - 1.0
        bench_fwd = bench_close.shift(-h) / bench_close - 1.0
        labels[f"fwd_excess_{h}"] = fwd - bench_fwd

    rows: list[dict] = []
    for pos in range(min_lookback, len(df), max(step, 1)):
        regime = regime_at.iloc[pos]
        regime = regime if isinstance(regime, str) else None
        row = build_feature_row(_signals_at(df, pos, detectors), cont.iloc[pos], regime)
        row.update({"date": df.index[pos], "symbol": symbol, "regime": regime})
        for h in horizons:
            excess = labels[f"fwd_excess_{h}"].iloc[pos]
            row[f"fwd_excess_{h}"] = excess
            scaled = None if math.isnan(excess) else excess_target(float(excess), float(vol.iloc[pos]), h)
            row[f"target_scaled_{h}"] = np.nan if scaled is None else scaled
        rows.append(row)
    return pd.DataFrame(rows)


def horizon_panel(panel: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Rename one horizon's labels to the harness columns and drop unlabeled rows."""
    out = panel.rename(columns={f"fwd_excess_{horizon}": "fwd_excess"})
    keep = [c for c in out.columns if not c.startswith(("fwd_excess_", "target_scaled_"))]
    return out[keep].dropna(subset=["fwd_excess"]).reset_index(drop=True)
