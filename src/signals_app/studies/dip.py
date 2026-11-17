"""Dip-buy timing study — how long a ticker's dips last, and when to buy them.

For each swing lookback window ``W`` (e.g. 5/10/20/50 trading days):

1. **Trigger.** A dip triggers on the first bar whose close is at least
   ``dip_pct`` percent below the highest close of the trailing ``W`` bars.
2. **Episode.** The dip lasts until the close recovers to that reference high,
   or ``max_recovery_bars`` pass. A new dip can't trigger inside an episode, so
   one long selloff counts once instead of once per bar.
3. **Timing.** Per episode we record bars from peak to trigger, bars from
   trigger to trough (the "dip time": how long after the trigger the real low
   came), the trough depth, and bars to recover.
4. **Entry delay.** For every delay ``k`` in ``0..max_entry_delay`` we measure
   the ``horizon_days`` forward return of buying ``k`` bars after the trigger.
   ``best_entry_delay`` is the ``k`` with the highest mean return. Unlike the
   trough (which is only knowable in hindsight), this is a rule you could
   actually trade: "buy on day k after a W-day dip of dip_pct".

Everything is in-sample, measured on the ticker's own history. Small samples
are flagged (``low_sample``) rather than hidden.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from statistics import mean, median
from typing import Any, Final

import numpy as np
import pandas as pd

# Classic swing-trading lookbacks: one week, two weeks, a month, a quarter.
DEFAULT_SWING_WINDOWS: Final[tuple[int, ...]] = (5, 10, 20, 50)
DEFAULT_DIP_PCT: Final[float] = 5.0
DEFAULT_MAX_ENTRY_DELAY: Final[int] = 10
DEFAULT_HORIZON_DAYS: Final[int] = 10
DEFAULT_MAX_RECOVERY_BARS: Final[int] = 120
# Fewer episodes than this and the per-delay means are mostly noise.
MIN_DIPS_FOR_CONFIDENCE: Final[int] = 8

MAX_WINDOW: Final[int] = 250
MAX_ENTRY_DELAY_LIMIT: Final[int] = 30
MAX_HORIZON_DAYS: Final[int] = 120


@dataclass(frozen=True)
class DipStudyParams:
    """Every knob of the study. All of them can be overridden per request."""

    windows: tuple[int, ...] = DEFAULT_SWING_WINDOWS
    dip_pct: float = DEFAULT_DIP_PCT
    max_entry_delay: int = DEFAULT_MAX_ENTRY_DELAY
    horizon_days: int = DEFAULT_HORIZON_DAYS
    max_recovery_bars: int = DEFAULT_MAX_RECOVERY_BARS

    def __post_init__(self) -> None:
        if not self.windows:
            raise ValueError("windows must not be empty")
        if any(w < 2 or w > MAX_WINDOW for w in self.windows):
            raise ValueError(f"each window must be in [2, {MAX_WINDOW}]")
        if not 0.1 <= self.dip_pct <= 90.0:
            raise ValueError("dip_pct must be in [0.1, 90]")
        if not 0 <= self.max_entry_delay <= MAX_ENTRY_DELAY_LIMIT:
            raise ValueError(f"max_entry_delay must be in [0, {MAX_ENTRY_DELAY_LIMIT}]")
        if not 1 <= self.horizon_days <= MAX_HORIZON_DAYS:
            raise ValueError(f"horizon_days must be in [1, {MAX_HORIZON_DAYS}]")
        if not 1 <= self.max_recovery_bars <= 1000:
            raise ValueError("max_recovery_bars must be in [1, 1000]")

    @classmethod
    def from_overrides(cls, overrides: dict[str, Any] | None) -> DipStudyParams:
        """Build params from a partial dict, rejecting unknown keys loudly."""
        if not overrides:
            return cls()
        known = set(cls.__dataclass_fields__)
        unknown = set(overrides) - known
        if unknown:
            raise ValueError(f"unknown dip params: {sorted(unknown)}; known: {sorted(known)}")
        values = dict(overrides)
        if "windows" in values:
            values["windows"] = tuple(sorted({int(w) for w in values["windows"]}))
        return cls(**values)


@dataclass(frozen=True)
class DipEpisode:
    """One dip, from the high it fell from to the bar it recovered (if it did)."""

    trigger_date: str
    peak_to_trigger_bars: int
    trigger_to_trough_bars: int
    depth_pct: float
    recovery_bars: int | None


@dataclass(frozen=True)
class EntryDelayStat:
    """Forward return of buying ``delay`` bars after a dip triggers."""

    delay: int
    n: int
    mean_return_pct: float | None
    median_return_pct: float | None
    win_rate: float | None


@dataclass(frozen=True)
class CurrentDip:
    """Whether the ticker is in a dip *now*, on this window."""

    in_dip: bool
    drawdown_pct: float
    bars_since_trigger: int | None


@dataclass(frozen=True)
class WindowStudy:
    """Aggregate dip statistics for one lookback window."""

    window: int
    n_dips: int
    low_sample: bool
    median_trigger_to_trough_bars: float | None
    mean_trigger_to_trough_bars: float | None
    median_peak_to_trigger_bars: float | None
    median_depth_pct: float | None
    recovery_rate: float | None
    median_recovery_bars: float | None
    best_entry_delay: int | None
    best_entry_mean_return_pct: float | None
    baseline_mean_return_pct: float | None
    edge_vs_baseline_pct: float | None
    entry_delays: list[EntryDelayStat] = field(default_factory=list)
    current: CurrentDip | None = None
    episodes: list[DipEpisode] = field(default_factory=list)


@dataclass(frozen=True)
class DipStudyResult:
    """The study for one symbol, across all requested windows."""

    symbol: str
    bars: int
    start: str
    end: str
    params: DipStudyParams
    windows: list[WindowStudy]

    def to_dict(self, *, include_episodes: bool = False) -> dict[str, Any]:
        """JSON-ready dict; episode lists are dropped unless asked for."""
        payload = asdict(self)
        payload["params"]["windows"] = list(self.params.windows)
        if not include_episodes:
            for w in payload["windows"]:
                w.pop("episodes", None)
        return payload


def _pct(x: float) -> float:
    return round(float(x) * 100.0, 3)


def _round_or_none(x: float | None, digits: int = 3) -> float | None:
    return None if x is None or math.isnan(x) else round(x, digits)


def _date_str(idx: Any) -> str:
    return pd.Timestamp(idx).strftime("%Y-%m-%d")


def _find_episodes(
    close: np.ndarray, window: int, params: DipStudyParams
) -> list[tuple[int, int, int, int | None]]:
    """Return ``(peak_idx, trigger_idx, trough_idx, recovered_idx)`` per dip."""
    threshold = 1.0 - params.dip_pct / 100.0
    episodes: list[tuple[int, int, int, int | None]] = []
    n = len(close)
    t = window - 1
    while t < n:
        lo = t - window + 1
        ref_high = close[lo : t + 1].max()
        if close[t] > ref_high * threshold:
            t += 1
            continue
        # Latest bar at the high, so a flat top doesn't inflate peak-to-trigger.
        span = close[lo : t + 1]
        peak_idx = t - int(np.argmax(span[::-1]))
        end = min(n, t + 1 + params.max_recovery_bars)
        recovered_idx: int | None = None
        for j in range(t + 1, end):
            if close[j] >= ref_high:
                recovered_idx = j
                break
        search_end = recovered_idx if recovered_idx is not None else end
        trough_idx = t + int(np.argmin(close[t:search_end])) if search_end > t else t
        episodes.append((peak_idx, t, trough_idx, recovered_idx))
        t = (recovered_idx if recovered_idx is not None else end) + 1
    return episodes


def _entry_delay_stats(
    close: np.ndarray, triggers: Sequence[int], params: DipStudyParams
) -> list[EntryDelayStat]:
    stats: list[EntryDelayStat] = []
    h = params.horizon_days
    for k in range(params.max_entry_delay + 1):
        rets = [
            close[i + k + h] / close[i + k] - 1.0
            for i in triggers
            if i + k + h < len(close)
        ]
        if not rets:
            stats.append(EntryDelayStat(k, 0, None, None, None))
            continue
        stats.append(
            EntryDelayStat(
                delay=k,
                n=len(rets),
                mean_return_pct=_pct(mean(rets)),
                median_return_pct=_pct(median(rets)),
                win_rate=round(sum(r > 0 for r in rets) / len(rets), 3),
            )
        )
    return stats


def _current_state(
    close: np.ndarray,
    window: int,
    params: DipStudyParams,
    episodes: list[tuple[int, int, int, int | None]],
) -> CurrentDip:
    ref_high = close[-window:].max()
    drawdown = close[-1] / ref_high - 1.0
    last_open = next((e for e in reversed(episodes) if e[3] is None), None)
    in_dip = last_open is not None and len(close) - 1 - last_open[1] <= params.max_recovery_bars
    return CurrentDip(
        in_dip=in_dip,
        drawdown_pct=_pct(drawdown),
        bars_since_trigger=(len(close) - 1 - last_open[1]) if in_dip and last_open else None,
    )


def _study_window(
    close: np.ndarray, index: pd.Index, window: int, params: DipStudyParams
) -> WindowStudy:
    raw = _find_episodes(close, window, params)
    episodes = [
        DipEpisode(
            trigger_date=_date_str(index[trig]),
            peak_to_trigger_bars=trig - peak,
            trigger_to_trough_bars=trough - trig,
            depth_pct=_pct(close[trough] / close[peak] - 1.0),
            recovery_bars=(rec - trig) if rec is not None else None,
        )
        for peak, trig, trough, rec in raw
    ]
    delays = _entry_delay_stats(close, [e[1] for e in raw], params)
    scored = [d for d in delays if d.mean_return_pct is not None]
    best = max(scored, key=lambda d: d.mean_return_pct or -math.inf) if scored else None

    h = params.horizon_days
    baseline = close[h:] / close[:-h] - 1.0 if len(close) > h else np.array([])
    baseline_mean = _pct(float(baseline.mean())) if baseline.size else None

    recovered = [e.recovery_bars for e in episodes if e.recovery_bars is not None]
    n = len(episodes)
    return WindowStudy(
        window=window,
        n_dips=n,
        low_sample=n < MIN_DIPS_FOR_CONFIDENCE,
        median_trigger_to_trough_bars=(
            median(e.trigger_to_trough_bars for e in episodes) if n else None
        ),
        mean_trigger_to_trough_bars=(
            _round_or_none(mean(e.trigger_to_trough_bars for e in episodes), 2) if n else None
        ),
        median_peak_to_trigger_bars=(
            median(e.peak_to_trigger_bars for e in episodes) if n else None
        ),
        median_depth_pct=_round_or_none(median(e.depth_pct for e in episodes)) if n else None,
        recovery_rate=round(len(recovered) / n, 3) if n else None,
        median_recovery_bars=median(recovered) if recovered else None,
        best_entry_delay=best.delay if best else None,
        best_entry_mean_return_pct=best.mean_return_pct if best else None,
        baseline_mean_return_pct=baseline_mean,
        edge_vs_baseline_pct=(
            _round_or_none(best.mean_return_pct - baseline_mean)
            if best and best.mean_return_pct is not None and baseline_mean is not None
            else None
        ),
        entry_delays=delays,
        current=_current_state(close, window, params, raw),
        episodes=episodes,
    )


def run_dip_study(
    symbol: str, df: pd.DataFrame, params: DipStudyParams | None = None
) -> DipStudyResult:
    """Run the dip-buy timing study on a daily OHLCV frame.

    Args:
        symbol: Ticker, carried through to the result.
        df: Daily bars, oldest first, with a ``Close`` column.
        params: Study knobs; defaults to :class:`DipStudyParams` defaults.

    Returns:
        A :class:`DipStudyResult` with one :class:`WindowStudy` per window.

    Raises:
        ValueError: If there are too few bars for the largest window plus the
            forward horizon.
    """
    params = params or DipStudyParams()
    closes = df["Close"].astype(float).dropna()
    needed = max(params.windows) + params.horizon_days + 1
    if len(closes) < needed:
        raise ValueError(f"{symbol}: need at least {needed} daily bars, got {len(closes)}")
    close = closes.to_numpy()
    return DipStudyResult(
        symbol=symbol.upper(),
        bars=len(close),
        start=_date_str(closes.index[0]),
        end=_date_str(closes.index[-1]),
        params=params,
        windows=[_study_window(close, closes.index, w, params) for w in params.windows],
    )
