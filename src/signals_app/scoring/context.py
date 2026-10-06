"""Risk context and location: what sizes and places a call without voting on it.

States predict *risk* even though they carry no direction edge (below the
200-day SMA, 21-day volatility is 10.2% vs 8.5% and a >10% drawdown is 17.1%
likely vs 11.4%; FIB-ICHIMOKU-MA.md §4.3). So they are reported here as
context for position size and stop width, and the graded ranker never lets
them vote. Proximity to a level is likewise reported as *location*: where to
enter, where the stop goes, where target 1 is.

Everything is point-in-time (bars <= t only) and ATR-normalized.
"""
from __future__ import annotations

import math
from typing import Any, Final

import pandas as pd

from signals_app.indicators.fibonacci import RETRACEMENTS, recent_legs

ATR_PERCENTILE_WINDOW: Final[int] = 252
HIGH_VOL_ATR_PERCENTILE: Final[float] = 0.80
NEAR_LEVEL_ATR: Final[float] = 1.0
MIN_ATR_PERCENTILE_BARS: Final[int] = 60


def _sf(val: object) -> float | None:
    try:
        v = float(val)  # type: ignore[arg-type]
        return None if (math.isnan(v) or math.isinf(v)) else v
    except (TypeError, ValueError):
        return None


def risk_context(df: pd.DataFrame) -> dict[str, Any]:
    """Standing-condition risk readings for the last bar.

    Returns:
        Dict with ``below_sma200``, ``sma200_dist_atr``, ``atr_pct`` (ATR as a
        share of price), ``atr_percentile`` (vs the trailing year) and
        ``high_vol``. Entries are None when the inputs are unavailable.
    """
    out: dict[str, Any] = {"below_sma200": None, "sma200_dist_atr": None, "atr_pct": None,
                           "atr_percentile": None, "high_vol": None}
    if df.empty or "Close" not in df.columns:
        return out
    close = _sf(df["Close"].iloc[-1])
    atr = _sf(df["ATR"].iloc[-1]) if "ATR" in df.columns else None
    sma200 = _sf(df["SMA_200"].iloc[-1]) if "SMA_200" in df.columns else None
    if close is None:
        return out
    if sma200 is not None:
        out["below_sma200"] = close < sma200
        if atr:
            out["sma200_dist_atr"] = round((close - sma200) / atr, 3)
    if atr and close:
        out["atr_pct"] = round(atr / close, 5)
        ratio = (df["ATR"] / df["Close"]).iloc[-ATR_PERCENTILE_WINDOW:].dropna()
        if len(ratio) >= MIN_ATR_PERCENTILE_BARS:
            percentile = float((ratio <= ratio.iloc[-1]).mean())
            out["atr_percentile"] = round(percentile, 3)
            out["high_vol"] = percentile >= HIGH_VOL_ATR_PERCENTILE
    return out


def _levels(df: pd.DataFrame, atr: float) -> dict[str, float]:
    last = df.iloc[-1]
    levels: dict[str, float] = {}
    named = {"kijun": "Ichimoku_Kijun", "sma50": "SMA_50", "sma200": "SMA_200"}
    for name, column in named.items():
        value = _sf(last[column]) if column in df.columns else None
        if value is not None:
            levels[name] = value
    span_a = _sf(last["Ichimoku_SpanA"]) if "Ichimoku_SpanA" in df.columns else None
    span_b = _sf(last["Ichimoku_SpanB"]) if "Ichimoku_SpanB" in df.columns else None
    if span_a is not None and span_b is not None:
        levels["cloud_top"], levels["cloud_bottom"] = max(span_a, span_b), min(span_a, span_b)
    legs = recent_legs(df, atr) if len(df) >= 30 else []
    if legs:
        for ratio in RETRACEMENTS:
            levels[f"fib_{ratio}"] = legs[0].retracement(ratio)
    return levels


def location(df: pd.DataFrame) -> dict[str, Any]:
    """Where price sits in the level field (fib, Kijun, cloud edges, SMA-50/200).

    Returns:
        Dict with ``nearest_support`` / ``nearest_resistance`` (name, price,
        distance in ATR) and ``levels_near`` (how many levels lie within one
        ATR, a cluster count). Empty-valued when there is no usable ATR.
    """
    out: dict[str, Any] = {"nearest_support": None, "nearest_resistance": None, "levels_near": 0}
    if df.empty or not {"Close", "ATR"} <= set(df.columns):
        return out
    close, atr = _sf(df["Close"].iloc[-1]), _sf(df["ATR"].iloc[-1])
    if close is None or not atr:
        return out
    levels = _levels(df, atr)
    below = {n: p for n, p in levels.items() if p <= close}
    above = {n: p for n, p in levels.items() if p > close}

    def describe(name: str, price: float) -> dict[str, Any]:
        return {"name": name, "price": round(price, 4), "dist_atr": round((price - close) / atr, 3)}

    if below:
        name = max(below, key=lambda n: below[n])
        out["nearest_support"] = describe(name, below[name])
    if above:
        name = min(above, key=lambda n: above[n])
        out["nearest_resistance"] = describe(name, above[name])
    out["levels_near"] = sum(1 for p in levels.values() if abs(p - close) <= NEAR_LEVEL_ATR * atr)
    return out
