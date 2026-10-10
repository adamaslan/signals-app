"""Grid search over entry triggers × exit rules, returned as a queryable table.

``run_grid`` returns a pandas DataFrame with one row per (symbol, entry, exit)
combination, so any question becomes a ``DataFrame.query`` string:

    df = run_grid({"SNDK": close}, GridSpec(entries=dip_entries((5, 10), (8, 12, 15))))
    query(df, "robust and n >= 12", sort="pct_per_bar", top=10)
"""
from __future__ import annotations

import itertools
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from signals_app.swing.forward import conditional_forward
from signals_app.swing.trades import ExitRule, simulate, summarize
from signals_app.swing.triggers import Trigger, dip, parse_trigger, runup

DEFAULT_MIN_TRADES = 8


@dataclass(frozen=True)
class GridSpec:
    """Entries are ``label -> trigger``; exits are crossed from the three lists."""

    entries: Mapping[str, Trigger]
    take_profits: tuple[float | None, ...] = (5, 8, 10, 15, 20, 30, None)
    stops: tuple[float | None, ...] = (None, 8, 12, 20)
    max_holds: tuple[int, ...] = (10, 20, 40)
    min_trades: int = DEFAULT_MIN_TRADES

    def exit_rules(self) -> list[ExitRule]:
        return [
            ExitRule(take_profit_pct=tp, stop_pct=sl, max_hold=h)
            for tp, sl, h in itertools.product(self.take_profits, self.stops, self.max_holds)
        ]


def dip_entries(windows: Iterable[int], pcts: Iterable[float]) -> dict[str, Trigger]:
    """``dip(w, p)`` for every window × percent, labelled by its expression."""
    return {f"dip({w},{p:g})": dip(w, p) for w, p in itertools.product(windows, pcts)}


def runup_entries(windows: Iterable[int], pcts: Iterable[float]) -> dict[str, Trigger]:
    """``runup(w, p)`` for every window × percent, labelled by its expression."""
    return {f"runup({w},{p:g})": runup(w, p) for w, p in itertools.product(windows, pcts)}


def expression_entries(exprs: Iterable[str]) -> dict[str, Trigger]:
    """Arbitrary trigger expressions (see :func:`parse_trigger`), labelled as written."""
    return {e.strip(): parse_trigger(e) for e in exprs}


def run_grid(closes: Mapping[str, np.ndarray], spec: GridSpec) -> pd.DataFrame:
    """Simulate every entry × exit on every symbol. Rows below ``min_trades`` are dropped."""
    rules = spec.exit_rules()
    rows: list[dict] = []
    for symbol, close in closes.items():
        close = np.asarray(close, dtype=float)
        for label, trigger in spec.entries.items():
            mask = trigger(close)
            if mask.sum() == 0:
                continue
            for rule in rules:
                trades = simulate(close, mask, rule)
                if len(trades) < spec.min_trades:
                    continue
                rows.append({
                    "symbol": symbol,
                    "entry": label,
                    "take_profit": rule.take_profit_pct,
                    "stop": rule.stop_pct,
                    "max_hold": rule.max_hold,
                    **asdict(summarize(trades, len(close))),
                })
    return pd.DataFrame(rows)


@dataclass(frozen=True)
class ForwardSpec:
    """Condition labels -> triggers, evaluated at each horizon."""

    conditions: Mapping[str, Trigger]
    horizons: tuple[int, ...] = (5, 10, 20)
    min_bars: int = DEFAULT_MIN_TRADES


def run_forward(closes: Mapping[str, np.ndarray], spec: ForwardSpec) -> pd.DataFrame:
    """Conditional forward-return table: one row per symbol × condition × horizon."""
    rows: list[dict] = []
    for symbol, close in closes.items():
        close = np.asarray(close, dtype=float)
        for label, trigger in spec.conditions.items():
            mask = trigger(close)
            for h in spec.horizons:
                stats = conditional_forward(close, mask, h)
                if stats.n_bars < spec.min_bars:
                    continue
                rows.append({"symbol": symbol, "condition": label, **asdict(stats)})
    return pd.DataFrame(rows)


def query(
    df: pd.DataFrame,
    where: str | None = None,
    *,
    sort: str | None = None,
    ascending: bool = False,
    top: int | None = None,
) -> pd.DataFrame:
    """Filter with a ``DataFrame.query`` string, sort by a column, keep ``top`` rows."""
    out = df
    if where and not out.empty:
        out = out.query(where)
    if sort and not out.empty:
        if sort not in out.columns:
            raise ValueError(f"unknown sort column {sort!r}; columns: {list(out.columns)}")
        out = out.sort_values(sort, ascending=ascending, na_position="last")
    if top is not None:
        out = out.head(top)
    return out.reset_index(drop=True)
