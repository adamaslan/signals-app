"""Vectorized technical indicator computation.

One-pass function that computes all indicators needed by the detection layer.
Covers: MA/RSI/MACD/BB/Stochastic/ADX/Ichimoku/OBV/CMF/ATR and parameter grids.

All computation is pure pandas/numpy — no side effects.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from signals_app.config import (
    ADX_PERIOD,
    ATR_PERIOD,
    BOLLINGER_PERIOD,
    BOLLINGER_STD,
    CMF_PERIOD,
    ICHIMOKU_KIJUN,
    ICHIMOKU_SENKOU_B,
    ICHIMOKU_TENKAN,
    MACD_FAST,
    MACD_SIGNAL,
    MACD_SLOW,
    OBV_EMA_PERIOD,
    RSI_PERIOD,
    STOCHASTIC_D_PERIOD,
    STOCHASTIC_K_PERIOD,
)
from signals_app.indicators.grids import (
    BB_PERIODS,
    BB_STD_DEVS,
    HL_LOOKBACKS,
    MA_DIST_PERIODS,
    MA_PERIODS_EXTENDED,
    MACD_PARAMS,
    RSI_PERIODS,
    VOLUME_MA_PERIODS,
)

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS: frozenset[str] = frozenset({"Open", "High", "Low", "Close", "Volume"})


def _validate_ohlcv(df: pd.DataFrame) -> None:
    """Raise ValueError if required OHLCV columns are missing.

    Args:
        df: Input DataFrame to validate.

    Raises:
        ValueError: If any required column is absent.
    """
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"DataFrame missing required OHLCV columns: {missing}")


def _smas_series(close: pd.Series) -> dict[str, pd.Series]:
    cols: dict[str, pd.Series] = {}
    for period in MA_PERIODS_EXTENDED:
        cols[f"SMA_{period}"] = close.rolling(window=period, min_periods=1).mean()
    return cols


def _emas_series(close: pd.Series) -> dict[str, pd.Series]:
    cols: dict[str, pd.Series] = {}
    for period in (9, 12, 20, 26):
        cols[f"EMA_{period}"] = close.ewm(span=period, adjust=False).mean()
    return cols


def _rsi_series(close: pd.Series) -> dict[str, pd.Series]:
    cols: dict[str, pd.Series] = {}
    for period in RSI_PERIODS:
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0.0, float("inf"))
        rsi_vals = 100.0 - (100.0 / (1.0 + rs))
        col = "RSI" if period == RSI_PERIOD else f"RSI_{period}"
        cols[col] = rsi_vals
    return cols


def _macd_series(close: pd.Series) -> dict[str, pd.Series]:
    cols: dict[str, pd.Series] = {}
    for fast, slow, sig in MACD_PARAMS:
        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=sig, adjust=False).mean()
        hist = macd_line - signal_line
        if fast == MACD_FAST and slow == MACD_SLOW and sig == MACD_SIGNAL:
            cols["MACD"] = macd_line
            cols["MACD_Signal"] = signal_line
            cols["MACD_Hist"] = hist
        else:
            tag = f"_{fast}_{slow}_{sig}"
            cols[f"MACD{tag}"] = macd_line
            cols[f"MACD_Signal{tag}"] = signal_line
            cols[f"MACD_Hist{tag}"] = hist
    return cols


def _bollinger_series(close: pd.Series) -> dict[str, pd.Series]:
    cols: dict[str, pd.Series] = {}
    for period in BB_PERIODS:
        rolling = close.rolling(window=period)
        sma = rolling.mean()
        std = rolling.std(ddof=0)
        for sd in BB_STD_DEVS:
            upper = sma + sd * std
            lower = sma - sd * std
            band_range = upper - lower
            pct_b = (close - lower) / band_range.replace(0.0, np.nan)
            width = band_range / sma.replace(0.0, np.nan)
            if period == BOLLINGER_PERIOD and sd == BOLLINGER_STD:
                cols["BB_Upper"] = upper
                cols["BB_Lower"] = lower
                cols["BB_Width"] = width
                cols["BB_Pct"] = pct_b
            else:
                sd_tag = str(sd).replace(".", "_")
                prefix = f"BB_{period}_{sd_tag}"
                cols[f"{prefix}_Upper"] = upper
                cols[f"{prefix}_Lower"] = lower
                cols[f"{prefix}_Width"] = width
                cols[f"{prefix}_Pct"] = pct_b
    return cols


def _stochastic_series(high: pd.Series, low: pd.Series, close: pd.Series) -> dict[str, pd.Series]:
    high_n = high.rolling(window=STOCHASTIC_K_PERIOD).max()
    low_n = low.rolling(window=STOCHASTIC_K_PERIOD).min()
    hl_range = (high_n - low_n).replace(0.0, np.nan)
    k_raw = 100.0 * (close - low_n) / hl_range
    stoch_k = k_raw.rolling(window=3).mean()
    return {"Stoch_K": stoch_k, "Stoch_D": stoch_k.rolling(window=STOCHASTIC_D_PERIOD).mean()}


def _adx_series(high: pd.Series, low: pd.Series, close: pd.Series) -> dict[str, pd.Series]:
    period = ADX_PERIOD
    prev_close = close.shift(1)
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    plus_dm_s = pd.Series(
        np.where((high - prev_high) > (prev_low - low), (high - prev_high).clip(lower=0), 0.0),
        index=high.index,
    )
    minus_dm_s = pd.Series(
        np.where((prev_low - low) > (high - prev_high), (prev_low - low).clip(lower=0), 0.0),
        index=high.index,
    )
    atr_w = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    plus_di = 100.0 * plus_dm_s.ewm(alpha=1.0 / period, adjust=False).mean() / atr_w.replace(0.0, np.nan)
    minus_di = 100.0 * minus_dm_s.ewm(alpha=1.0 / period, adjust=False).mean() / atr_w.replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    return {"ADX": dx.ewm(alpha=1.0 / period, adjust=False).mean(), "Plus_DI": plus_di, "Minus_DI": minus_di}


def _atr_series(high: pd.Series, low: pd.Series, close: pd.Series) -> dict[str, pd.Series]:
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return {"ATR": tr.ewm(alpha=1.0 / ATR_PERIOD, adjust=False).mean()}


def _ichimoku_series(high: pd.Series, low: pd.Series) -> dict[str, pd.Series]:
    tenkan = (high.rolling(ICHIMOKU_TENKAN).max() + low.rolling(ICHIMOKU_TENKAN).min()) / 2.0
    kijun = (high.rolling(ICHIMOKU_KIJUN).max() + low.rolling(ICHIMOKU_KIJUN).min()) / 2.0
    span_a = ((tenkan + kijun) / 2.0).shift(ICHIMOKU_KIJUN)
    span_b = ((high.rolling(ICHIMOKU_SENKOU_B).max() + low.rolling(ICHIMOKU_SENKOU_B).min()) / 2.0).shift(ICHIMOKU_KIJUN)
    return {"Ichimoku_Tenkan": tenkan, "Ichimoku_Kijun": kijun, "Ichimoku_SpanA": span_a, "Ichimoku_SpanB": span_b}


def _obv_cmf_series(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> dict[str, pd.Series]:
    direction = np.sign(close.diff().fillna(0.0))
    obv = (volume * direction).cumsum()
    money_flow_mult = ((close - low) - (high - close)) / (high - low).replace(0.0, np.nan)
    money_flow_vol = money_flow_mult * volume
    cmf = money_flow_vol.rolling(CMF_PERIOD).sum() / volume.rolling(CMF_PERIOD).sum().replace(0.0, np.nan)
    return {"OBV": obv, "OBV_EMA": obv.ewm(span=OBV_EMA_PERIOD, adjust=False).mean(), "CMF": cmf}


def _volume_ma_series(volume: pd.Series) -> dict[str, pd.Series]:
    return {f"Volume_MA_{p}": volume.rolling(window=p, min_periods=1).mean() for p in VOLUME_MA_PERIODS}


def _hl_lookback_series(high: pd.Series, low: pd.Series, n_bars: int) -> dict[str, pd.Series]:
    cols: dict[str, pd.Series] = {}
    for lb in HL_LOOKBACKS:
        if n_bars >= lb:
            cols[f"High_{lb}b"] = high.rolling(window=lb).max()
            cols[f"Low_{lb}b"] = low.rolling(window=lb).min()
    return cols


def _ma_distance_series(close: pd.Series, existing_cols: dict[str, pd.Series]) -> dict[str, pd.Series]:
    cols: dict[str, pd.Series] = {}
    for period in MA_DIST_PERIODS:
        sma_col = f"SMA_{period}"
        if sma_col in existing_cols:
            sma = existing_cols[sma_col].replace(0.0, np.nan)
            cols[f"Dist_SMA_{period}"] = (close - sma) / sma * 100.0
    return cols


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all technical indicators in one vectorized pass.

    Inputs: standard OHLCV DataFrame (columns: Open, High, Low, Close, Volume).
    All columns are computed without in-place mutation; a single pd.concat
    at the end builds the final DataFrame to avoid pandas fragmentation.

    Indicator groups computed:
    - SMAs (5, 10, 20, 50, 100, 200)
    - EMAs (9, 12, 20, 26)
    - RSI for multiple periods (5, 10, 14, 20, 30) using Wilder's smoothing
    - MACD for multiple parameter sets
    - Bollinger Bands for all period × std-dev combinations
    - Stochastic Oscillator (%K, %D)
    - ADX, +DI, -DI
    - ATR
    - Ichimoku Cloud (Tenkan, Kijun, SpanA, SpanB)
    - OBV, OBV EMA, CMF
    - Volume MAs (5, 10, 20, 50)
    - Rolling H/L for lookback periods (20, 50, 100, 200, 252)
    - Price distance from all SMAs
    - Single-bar % price change

    Args:
        df: OHLCV DataFrame with DatetimeIndex, sorted oldest-first.

    Returns:
        New DataFrame with all indicator columns appended.

    Raises:
        ValueError: If required OHLCV columns are absent.
    """
    _validate_ohlcv(df)
    logger.debug("compute_indicators: %d bars, %d input columns", len(df), len(df.columns))

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"].astype(float)

    # Collect all new Series into a single dict, then concat once
    new_cols: dict[str, pd.Series] = {}
    new_cols.update(_smas_series(close))
    new_cols.update(_emas_series(close))
    new_cols.update(_rsi_series(close))
    new_cols.update(_macd_series(close))
    new_cols.update(_bollinger_series(close))
    new_cols.update(_stochastic_series(high, low, close))
    new_cols.update(_adx_series(high, low, close))
    new_cols.update(_atr_series(high, low, close))
    new_cols.update(_ichimoku_series(high, low))
    new_cols.update(_obv_cmf_series(high, low, close, volume))
    new_cols.update(_volume_ma_series(volume))
    new_cols.update(_hl_lookback_series(high, low, len(df)))
    new_cols.update(_ma_distance_series(close, new_cols))

    prev_close = close.shift(1)
    new_cols["Price_Change"] = (close - prev_close) / prev_close.replace(0.0, np.nan) * 100.0

    result = pd.concat([df, pd.DataFrame(new_cols, index=df.index)], axis=1)
    logger.debug("compute_indicators: done — %d output columns", len(result.columns))
    return result
