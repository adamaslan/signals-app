"""Offline original bar-family measurements, not ranked trading votes.

Ported from the owner's windows.py source. No provider imports or execution.
These observations overlap the nineteen standard detectors. Never sum them as
independent tests, label volatility as options-chain data, or assign edge.
"""
from __future__ import annotations
import math
from signals_app.detection.base import MutableSignal
from signals_app.detection.fibonacci import TOLERANCE_ATR, FibonacciDetector
from signals_app.indicators.fibonacci import EXTENSIONS, RETRACEMENTS, recent_legs
MIN_BARS = 30
FIB_COLS = ("High", "Low", "Open", "Close")

def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x

def fib_depth_events(df):
    if len(df) < MIN_BARS:
        return set()
    bar, prev = df.iloc[-1], df.iloc[-2]
    atr = _num(bar["ATR"])
    vals = [_num(bar[c]) for c in FIB_COLS]
    prev_close, prev_high, prev_low = _num(prev["Close"]), _num(prev["High"]), _num(prev["Low"])
    if not atr or None in vals or None in (prev_close, prev_high, prev_low):
        return set()
    high, low, open_, close = vals
    tol = TOLERANCE_ATR * atr
    events = set()
    for k, leg in enumerate(recent_legs(df, atr)):
        for ratio in RETRACEMENTS:
            level = leg.retracement(ratio)
            if FibonacciDetector._reacted(leg, level, level, tol, low, high, open_, close):
                events.add(("fib_hold", k, ratio))
            if (leg.is_up and close < level <= prev_close) or (not leg.is_up and close > level >= prev_close):
                events.add(("fib_break", k, ratio))
        for ext in EXTENSIONS:
            target = leg.extension(ext)
            if (high >= target > prev_high) if leg.is_up else (low <= target < prev_low):
                events.add(("fib_target", k, ext))
    return events

def price_cross_events(df):
    if len(df) < 2:
        return set()
    cur, prev = df.iloc[-1], df.iloc[-2]
    close, prev_close = _num(cur["Close"]), _num(prev["Close"])
    events = set()
    for line in ("Ichimoku_Kijun", "Ichimoku_Tenkan"):
        level, prev_level = _num(cur[line]), _num(prev[line])
        if None in (close, prev_close, level, prev_level):
            continue
        if prev_close <= prev_level and close > level:
            events.add(("price_cross", line, "up"))
        elif prev_close >= prev_level and close < level:
            events.add(("price_cross", line, "down"))
    return events

def _pct_rank(series):
    clean = series.dropna()
    if len(clean) < 30:
        return None
    return float((clean <= clean.iloc[-1]).mean())

def options_style_events(df):
    if len(df) < MIN_BARS:
        return set()
    close = df["Close"]
    ret = close.pct_change()
    events = set()
    rv_rank = _pct_rank(ret.rolling(20).std() * (252 ** 0.5))
    if rv_rank is not None and rv_rank >= 0.9:
        events.add(("vol", "realized_vol_top10pct"))
    if rv_rank is not None and rv_rank <= 0.1:
        events.add(("vol", "realized_vol_bottom10pct"))
    width_rank = _pct_rank((4 * close.rolling(20).std()) / close.rolling(20).mean())
    if width_rank is not None and width_rank <= 0.1:
        events.add(("vol", "bb_squeeze"))
    atr_rank = _pct_rank(df["ATR"]) if "ATR" in df else None
    if atr_rank is not None and atr_rank >= 0.9:
        events.add(("vol", "atr_top10pct"))
    rsi = _num(df["RSI"].iloc[-1]) if "RSI" in df else None
    sma200 = _num(df["SMA_200"].iloc[-1]) if "SMA_200" in df else None
    low252 = _num(close.iloc[-252:].min()) if len(close) >= 252 else None
    last = _num(close.iloc[-1])
    if None not in (rsi, sma200, last) and rsi < 30 and last > sma200:
        events.add(("dip_buy", "rsi_oversold_above_sma200"))
    if None not in (rsi, low252, last) and rsi > 70 and low252 and last / low252 > 1.5:
        events.add(("sell_winner", "rsi_overbought_50pct_above_52w_low"))
    return events

class OriginalFibDepthDetector:
    def detect(self, frame):
        return _observations("original_fib", fib_depth_events(frame))

class OriginalPriceCrossDetector:
    def detect(self, frame):
        return _observations("original_cross", price_cross_events(frame))

class OriginalVolatilityContextDetector:
    def detect(self, frame):
        return _observations("original_context", options_style_events(frame))

def _observations(family, events):
    return [MutableSignal(signal=family + ":" + ":".join(map(str, event)),
        description="Original bar-family research observation; overlapping, not validated edge or an options feed",
        strength="NEUTRAL", category="research_context") for event in sorted(events, key=repr)]

def original_bar_detectors():
    return [OriginalFibDepthDetector(), OriginalPriceCrossDetector(), OriginalVolatilityContextDetector()]
