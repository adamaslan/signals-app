"""Trade simulation and statistics for a trigger + exit rule.

Model (deliberately simple and stated in every result): enter at the close of
a trigger bar; exit at the first later close that reaches the take-profit or
the stop, else at the close ``max_hold`` bars later. Trades never overlap —
after an exit, the next entry is the next trigger bar strictly after it.
Close-only: no intraday highs/lows, fees, slippage, or gap modelling, so real
stop losses can be worse than reported.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np

# A trade-rule result needs at least this many trades in each half of the
# history (and a positive average in both) to count as "robust".
ROBUST_MIN_TRADES_PER_HALF: Final[int] = 3


@dataclass(frozen=True)
class ExitRule:
    """How a position is closed. ``None`` disables a take-profit or stop."""

    take_profit_pct: float | None = None
    stop_pct: float | None = None
    max_hold: int = 20

    def __post_init__(self) -> None:
        if self.max_hold < 1:
            raise ValueError("max_hold must be >= 1")
        if self.take_profit_pct is not None and self.take_profit_pct <= 0:
            raise ValueError("take_profit_pct must be > 0")
        if self.stop_pct is not None and self.stop_pct <= 0:
            raise ValueError("stop_pct must be > 0")


@dataclass(frozen=True)
class Trade:
    """One round trip. Indices are bar positions in the close series."""

    entry_idx: int
    exit_idx: int
    entry_price: float
    exit_price: float
    exit_reason: str  # "take_profit" | "stop" | "max_hold" | "end_of_data"

    @property
    def return_pct(self) -> float:
        return (self.exit_price / self.entry_price - 1) * 100

    @property
    def bars_held(self) -> int:
        return self.exit_idx - self.entry_idx


@dataclass(frozen=True)
class TradeStats:
    """Aggregate of a trade list, with a first-half/second-half split."""

    n: int
    avg_pct: float | None
    median_pct: float | None
    win_rate: float | None
    worst_pct: float | None
    best_pct: float | None
    avg_bars: float | None
    pct_per_bar: float | None
    compounded_pct: float | None
    avg_first_half_pct: float | None
    avg_second_half_pct: float | None
    n_first_half: int
    n_second_half: int
    robust: bool


def simulate(close: np.ndarray, entries: np.ndarray, rule: ExitRule) -> list[Trade]:
    """Walk the series and take every non-overlapping trade the mask allows."""
    close = np.asarray(close, dtype=float)
    entries = np.asarray(entries, dtype=bool)
    n = len(close)
    trades: list[Trade] = []
    t = 0
    while t < n - 1:
        if not entries[t]:
            t += 1
            continue
        entry = close[t]
        last = min(n - 1, t + rule.max_hold)
        exit_idx, reason = last, "max_hold" if t + rule.max_hold <= n - 1 else "end_of_data"
        for j in range(t + 1, last + 1):
            change = (close[j] / entry - 1) * 100
            if rule.take_profit_pct is not None and change >= rule.take_profit_pct:
                exit_idx, reason = j, "take_profit"
                break
            if rule.stop_pct is not None and change <= -rule.stop_pct:
                exit_idx, reason = j, "stop"
                break
        trades.append(Trade(t, exit_idx, float(entry), float(close[exit_idx]), reason))
        t = exit_idx + 1
    return trades


def _mean(xs: list[float]) -> float | None:
    return float(np.mean(xs)) if xs else None


def summarize(trades: list[Trade], n_bars: int) -> TradeStats:
    """Stats for ``trades`` over a series of ``n_bars``; split at the midpoint."""
    rets = [t.return_pct for t in trades]
    half = n_bars // 2
    first = [t.return_pct for t in trades if t.entry_idx < half]
    second = [t.return_pct for t in trades if t.entry_idx >= half]
    avg_bars = _mean([float(t.bars_held) for t in trades])
    avg = _mean(rets)
    a1, a2 = _mean(first), _mean(second)
    return TradeStats(
        n=len(trades),
        avg_pct=avg,
        median_pct=float(np.median(rets)) if rets else None,
        win_rate=float(np.mean([r > 0 for r in rets])) if rets else None,
        worst_pct=min(rets) if rets else None,
        best_pct=max(rets) if rets else None,
        avg_bars=avg_bars,
        pct_per_bar=(avg / max(avg_bars, 1.0)) if avg is not None and avg_bars else None,
        compounded_pct=(float(np.prod([1 + r / 100 for r in rets])) - 1) * 100 if rets else None,
        avg_first_half_pct=a1,
        avg_second_half_pct=a2,
        n_first_half=len(first),
        n_second_half=len(second),
        robust=(
            a1 is not None and a2 is not None and a1 > 0 and a2 > 0
            and len(first) >= ROBUST_MIN_TRADES_PER_HALF
            and len(second) >= ROBUST_MIN_TRADES_PER_HALF
        ),
    )
