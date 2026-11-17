"""Tie-safe crossover detection shared by every trend detector.

The old ``prev <= prev_slow and now > slow`` pattern re-fires when the fast line
leaves a tie on the *same* side it was on before the tie (Tenkan and Kijun are
both range midpoints and tie on ~3% of bars). The sign-flip rule used here
carries the last non-zero sign across ties, so only a genuine change of side
counts. See harness/FIB-ICHIMOKU-MA.md §5.1 (defect D4).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

CROSS_UP = 1
CROSS_DOWN = -1
NO_CROSS = 0


def _carried_sign(fast: pd.Series, slow: pd.Series) -> pd.Series:
    """Return sign(fast - slow) with ties carrying the last non-zero sign.

    NaN wherever either input is NaN, and NaN until the first non-zero sign.
    """
    diff = fast - slow
    sign = np.sign(diff)
    carried = sign.where(sign != 0).ffill()
    return carried.where(diff.notna())


def sign_cross(fast: pd.Series, slow: pd.Series) -> pd.Series:
    """Per-bar crossover events of ``fast`` over ``slow``.

    Let ``s[i] = sign(fast[i] - slow[i])``, carrying the last non-zero sign
    through exact ties. A cross up fires at ``i`` iff ``s[i-1] < 0 and
    s[i] > 0``; a cross down is the mirror. Bars with NaN inputs never fire.
    Causal: the value at bar ``i`` depends only on bars ``0..i``.

    Args:
        fast: Fast line (e.g. SMA 50, Tenkan, Close).
        slow: Slow line aligned to the same index.

    Returns:
        Integer Series: ``CROSS_UP`` (1), ``CROSS_DOWN`` (-1) or ``NO_CROSS`` (0).
    """
    sign = _carried_sign(fast, slow)
    prev = sign.shift(1)
    up = (prev < 0) & (sign > 0)
    down = (prev > 0) & (sign < 0)
    return up.astype(int) - down.astype(int)


def cross_at_last_bar(df: pd.DataFrame, fast_col: str, slow_col: str) -> int:
    """Return the cross event on the final bar of ``df`` for two columns.

    Returns ``NO_CROSS`` when either column is missing or the frame is too short.
    """
    if len(df) < 2 or fast_col not in df.columns or slow_col not in df.columns:
        return NO_CROSS
    return int(sign_cross(df[fast_col], df[slow_col]).iloc[-1])
