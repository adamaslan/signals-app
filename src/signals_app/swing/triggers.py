"""Entry/condition triggers — boolean masks over a close series.

Every trigger is a pure function ``close -> bool mask`` (one entry per bar,
True where the condition holds on that bar's close, using only that bar and
earlier bars — no look-ahead). Triggers compose with :func:`all_of` /
:func:`any_of`, and :func:`parse_trigger` turns a small expression language
into a trigger so the CLI can take arbitrary conditions:

    dip(5, 12)                     close ≥12% below the 5-bar high close
    runup(5, 30)                   close ≥30% above the 5-bar low close
    below_sma(50) & dip(20, 8)     both
    above_sma(200, 5) | runup(10, 40)
"""
from __future__ import annotations

import re
from collections.abc import Callable
from functools import reduce
from typing import Final

import numpy as np
import pandas as pd

Trigger = Callable[[np.ndarray], np.ndarray]


def _rolling(close: np.ndarray, window: int, how: str) -> np.ndarray:
    s = pd.Series(close)
    r = s.rolling(window, min_periods=window)
    return (r.max() if how == "max" else r.min() if how == "min" else r.mean()).to_numpy()


def _check_window(window: int) -> int:
    window = int(window)
    if window < 2:
        raise ValueError("window must be >= 2")
    return window


def dip(window: int, pct: float) -> Trigger:
    """Close at least ``pct``% below the highest close of the last ``window`` bars."""
    window = _check_window(window)

    def mask(close: np.ndarray) -> np.ndarray:
        with np.errstate(invalid="ignore"):
            return close <= _rolling(close, window, "max") * (1 - pct / 100)

    return mask


def runup(window: int, pct: float) -> Trigger:
    """Close at least ``pct``% above the lowest close of the last ``window`` bars."""
    window = _check_window(window)

    def mask(close: np.ndarray) -> np.ndarray:
        with np.errstate(invalid="ignore"):
            return close >= _rolling(close, window, "min") * (1 + pct / 100)

    return mask


def below_sma(period: int, pct: float = 0.0) -> Trigger:
    """Close at least ``pct``% below its ``period``-bar simple moving average."""
    period = _check_window(period)

    def mask(close: np.ndarray) -> np.ndarray:
        with np.errstate(invalid="ignore"):
            return close <= _rolling(close, period, "mean") * (1 - pct / 100)

    return mask


def above_sma(period: int, pct: float = 0.0) -> Trigger:
    """Close at least ``pct``% above its ``period``-bar simple moving average."""
    period = _check_window(period)

    def mask(close: np.ndarray) -> np.ndarray:
        with np.errstate(invalid="ignore"):
            return close >= _rolling(close, period, "mean") * (1 + pct / 100)

    return mask


def all_of(*triggers: Trigger) -> Trigger:
    """True where every trigger is True."""
    return lambda close: reduce(np.logical_and, (t(close) for t in triggers))


def any_of(*triggers: Trigger) -> Trigger:
    """True where any trigger is True."""
    return lambda close: reduce(np.logical_or, (t(close) for t in triggers))


TRIGGERS: Final[dict[str, Callable[..., Trigger]]] = {
    "dip": dip,
    "runup": runup,
    "below_sma": below_sma,
    "above_sma": above_sma,
}

_TERM = re.compile(r"^\s*([a-z_]+)\s*\(\s*([-0-9.,\s]*)\)\s*$")


def _parse_term(text: str) -> Trigger:
    m = _TERM.match(text)
    if not m:
        raise ValueError(f"bad trigger term {text!r}; expected name(args), e.g. dip(5, 12)")
    name, raw = m.group(1), m.group(2)
    if name not in TRIGGERS:
        raise ValueError(f"unknown trigger {name!r}; known: {sorted(TRIGGERS)}")
    args = [float(a) for a in raw.split(",") if a.strip()]
    try:
        return TRIGGERS[name](*args)
    except TypeError as exc:
        raise ValueError(f"{name}: {exc}") from exc


def parse_trigger(expr: str) -> Trigger:
    """Parse ``a(..) & b(..) | c(..)``. ``&`` binds tighter than ``|``; no parens."""
    if not expr or not expr.strip():
        raise ValueError("empty trigger expression")
    ors = [all_of(*(_parse_term(t) for t in part.split("&"))) for part in expr.split("|")]
    return ors[0] if len(ors) == 1 else any_of(*ors)
