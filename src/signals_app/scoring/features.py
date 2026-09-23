"""Feature construction shared by training and the live scan (plan §5a).

One code path builds features for a historical bar and for today's bar, so the
model cannot be trained on something the scanner cannot reproduce.
"""
from __future__ import annotations

import math
from typing import Final

import numpy as np
import pandas as pd

from signals_app.detection.base import MutableSignal
from signals_app.scoring.confluence import FamilyConfluenceRanker
from signals_app.scoring.families import FAMILIES
from signals_app.scoring.regime import REGIMES

FIRING_FEATURES: Final[tuple[str, ...]] = tuple(f"fam_{f}" for f in FAMILIES)
CONTINUOUS_FEATURES: Final[tuple[str, ...]] = (
    "rsi_c",
    "macd_hist_atr",
    "dist_sma50_atr",
    "dist_sma200_atr",
    "adx_signed",
    "bb_pct_c",
    "cmf",
    "rel_ret20",
)
REGIME_FEATURES: Final[tuple[str, ...]] = tuple(f"regime_{r}" for r in REGIMES)
INTERACTION_FEATURES: Final[tuple[str, ...]] = tuple(
    f"{fam}__{reg}" for fam in FIRING_FEATURES for reg in REGIME_FEATURES
)

# Rung 1: detector firings only. Rung 2: + underlying values, regime and
# family x regime interactions (plan §5b).
FEATURE_SETS: Final[dict[str, tuple[str, ...]]] = {
    "rung1": FIRING_FEATURES,
    "rung2": FIRING_FEATURES + CONTINUOUS_FEATURES + REGIME_FEATURES + INTERACTION_FEATURES,
}

RETURN_LOOKBACK: Final[int] = 20
ADX_SCALE: Final[float] = 50.0


def continuous_features_frame(
    df: pd.DataFrame, benchmark_close: pd.Series | None = None
) -> pd.DataFrame:
    """Vectorized ATR-normalised, scale-free features for every bar of ``df``.

    ``df`` must be ``compute_indicators`` output. Only ratios / ATR-scaled
    values are produced, never raw price levels (plan §9, adjusted-close risk).
    """
    close = df["Close"].astype(float)
    atr = df["ATR"].astype(float).replace(0.0, np.nan)
    out = pd.DataFrame(index=df.index)
    out["rsi_c"] = (df["RSI"] - 50.0) / 50.0
    out["macd_hist_atr"] = df["MACD_Hist"] / atr
    out["dist_sma50_atr"] = (close - df["SMA_50"]) / atr
    out["dist_sma200_atr"] = (close - df["SMA_200"]) / atr
    out["adx_signed"] = (df["ADX"] / ADX_SCALE) * np.sign(df["Plus_DI"] - df["Minus_DI"])
    out["bb_pct_c"] = df["BB_Pct"] - 0.5
    out["cmf"] = df["CMF"]
    own_ret = close.pct_change(RETURN_LOOKBACK)
    if benchmark_close is not None:
        bench = benchmark_close.reindex(df.index, method="ffill")
        out["rel_ret20"] = own_ret - bench.pct_change(RETURN_LOOKBACK)
    else:
        out["rel_ret20"] = np.nan
    return out.replace([np.inf, -np.inf], np.nan)


def signal_features(signals: list[MutableSignal]) -> dict[str, float]:
    """Family net votes for one bar, with NO regime gate (the model learns it)."""
    result = FamilyConfluenceRanker().rank_signals(signals, regime=None)
    return {f"fam_{f}": result.families.get(f, 0.0) for f in FAMILIES}


def build_feature_row(
    signals: list[MutableSignal],
    continuous_row: pd.Series | dict[str, float] | None,
    regime: str | None,
) -> dict[str, float]:
    """Full rung-2 feature dict (rung 1 is a subset).

    Missing continuous values are NaN (imputed by the model's standardiser);
    an unknown regime leaves every regime indicator 0.
    """
    row: dict[str, float] = dict(signal_features(signals))
    for name in CONTINUOUS_FEATURES:
        value = float("nan") if continuous_row is None else continuous_row.get(name, float("nan"))
        row[name] = float(value) if value is not None and not math.isnan(value) else float("nan")
    for r in REGIMES:
        row[f"regime_{r}"] = 1.0 if regime == r else 0.0
    for fam in FIRING_FEATURES:
        for r in REGIMES:
            row[f"{fam}__regime_{r}"] = row[fam] * row[f"regime_{r}"]
    return row
