"""Swing lab — generic building blocks for "when to buy / when to sell" studies.

* :mod:`.triggers` — condition masks (``dip``, ``runup``, ``below_sma``, ...)
  and a tiny expression parser (``"dip(5,12) & below_sma(50)"``).
* :mod:`.trades`   — non-overlapping trade simulation for an :class:`ExitRule`
  (take-profit / stop / max hold) and :class:`TradeStats` with a
  first-half/second-half robustness split.
* :mod:`.forward`  — forward returns on condition bars vs. the baseline.
* :mod:`.grid`     — entry × exit grids and condition × horizon tables as
  DataFrames, plus :func:`query` for ``where``/``sort``/``top``.

All of it is pure and runs on a numpy close array; fetching lives in the CLI
(``signals swing-grid`` / ``swing-test`` / ``swing-forward``).
"""
from signals_app.swing.forward import ForwardStats, conditional_forward, forward_returns
from signals_app.swing.grid import (
    ForwardSpec,
    GridSpec,
    dip_entries,
    expression_entries,
    query,
    run_forward,
    run_grid,
    runup_entries,
)
from signals_app.swing.trades import ExitRule, Trade, TradeStats, simulate, summarize
from signals_app.swing.triggers import (
    above_sma,
    all_of,
    any_of,
    below_sma,
    dip,
    parse_trigger,
    runup,
)

__all__ = [
    "ExitRule", "ForwardSpec", "ForwardStats", "GridSpec", "Trade", "TradeStats",
    "above_sma", "all_of", "any_of", "below_sma", "conditional_forward", "dip",
    "dip_entries", "expression_entries", "forward_returns", "parse_trigger", "query",
    "run_forward", "run_grid", "runup", "runup_entries", "simulate", "summarize",
]
