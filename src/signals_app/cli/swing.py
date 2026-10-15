"""``signals swing …`` — query the swing lab from the shell.

    signals swing grid SNDK --dips 5,8,12,15 --where "robust and n >= 12" --sort pct_per_bar
    signals swing grid SNDK MU --entry "dip(5,12) & below_sma(20)" --tps 5,8,none
    signals swing test SNDK --entry "dip(5,12)" --tp 5 --stop 8 --hold 10 --trades
    signals swing forward SNDK --when "runup(5,30)" --when "dip(10,15)" --horizons 5,10
    signals swing levels SNDK --dips 5,12,15

Every command takes ``--json`` (stdout only) and the table commands take
``--where`` (a pandas ``DataFrame.query`` string), ``--sort``, ``--top`` and
``--csv``. Daily bars come from ``DataFetcher.fetch_daily_history``, so the
vendor order is whatever that fetcher implements.

Exit codes follow ``signals``: 2 bad usage, 4 insufficient data, 5 upstream
unavailable.
"""
from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict
from typing import Annotated, Any

import numpy as np
import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from signals_app.swing import (
    ExitRule,
    ForwardSpec,
    GridSpec,
    dip_entries,
    expression_entries,
    parse_trigger,
    query,
    run_forward,
    run_grid,
    simulate,
    summarize,
)

EXIT_USAGE = 2
EXIT_INSUFFICIENT_DATA = 4
EXIT_UPSTREAM = 5
DEFAULT_SWING_PERIOD = "5y"
DEFAULT_TOP = 20

swing_app = typer.Typer(
    name="swing",
    help="Swing lab: when to buy / sell — grids, single rules, forward returns, levels.",
    no_args_is_help=True,
)
_out = Console()
_err = Console(stderr=True)

JsonOpt = Annotated[bool, typer.Option("--json", help="Emit JSON to stdout only.")]
PeriodOpt = Annotated[str, typer.Option(help="Daily history to load (yfinance period).")]
WhereOpt = Annotated[
    str | None, typer.Option(help='pandas query, e.g. "robust and n >= 12 and win_rate > 0.7"')
]
SortOpt = Annotated[str | None, typer.Option(help="Column to sort by (descending).")]
TopOpt = Annotated[int, typer.Option(help="Rows to show.")]
CsvOpt = Annotated[str | None, typer.Option("--csv", help="Also write the full table here.")]


def _quiet_logs() -> None:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, force=True)


def _numbers(raw: str, *, allow_none: bool = False) -> tuple[Any, ...]:
    out: list[Any] = []
    for part in raw.split(","):
        part = part.strip().lower()
        if not part:
            continue
        if part == "none":
            if not allow_none:
                raise typer.BadParameter("'none' is not allowed here")
            out.append(None)
            continue
        try:
            value = float(part)
        except ValueError as exc:
            raise typer.BadParameter(f"not a number: {part!r}") from exc
        out.append(int(value) if value.is_integer() else value)
    if not out:
        raise typer.BadParameter("empty list")
    return tuple(out)


def _load(symbols: list[str], period: str) -> dict[str, np.ndarray]:
    from signals_app.data.fetcher import DataFetcher

    fetcher = DataFetcher()
    closes: dict[str, np.ndarray] = {}
    for raw in symbols:
        symbol = raw.upper().strip()
        try:
            df = fetcher.fetch_daily_history(symbol, period)
        except Exception as exc:  # noqa: BLE001 — any fetch failure is "upstream" here
            _err.print(f"[red]{symbol}: fetch failed: {exc}[/red]")
            continue
        close = df["Close"].astype(float).dropna().to_numpy()
        if len(close) < 30:
            _err.print(f"[yellow]{symbol}: only {len(close)} daily bars, skipped[/yellow]")
            continue
        _err.print(f"{symbol}: {len(close)} daily bars")
        closes[symbol] = close
    if not closes:
        raise typer.Exit(EXIT_UPSTREAM)
    return closes


def _emit(df: pd.DataFrame, *, where: str | None, sort: str | None, top: int,
          csv: str | None, as_json: bool, title: str) -> None:
    if csv:
        df.to_csv(csv, index=False)
        _err.print(f"wrote {len(df)} rows to {csv}")
    try:
        shown = query(df, where, sort=sort, top=top)
    except Exception as exc:  # noqa: BLE001 — pandas raises many types for a bad query
        _err.print(f"[red]bad --where/--sort: {exc}[/red]")
        raise typer.Exit(EXIT_USAGE) from exc
    if as_json:
        sys.stdout.write(shown.to_json(orient="records") + "\n")
        return
    if shown.empty:
        _out.print(f"{title}: no rows match")
        return
    table = Table(title=f"{title} — {len(shown)} of {len(df)} rows")
    for col in shown.columns:
        table.add_column(str(col), justify="right")
    # itertuples on an object frame keeps ints as ints (iterrows upcasts mixed rows).
    for row in shown.astype(object).itertuples(index=False):
        table.add_row(*[
            "" if v is None or (isinstance(v, float) and np.isnan(v))
            else f"{v:.2f}" if isinstance(v, float) else str(v)
            for v in row
        ])
    _out.print(table)


GRID_COLUMNS = [
    "symbol", "entry", "take_profit", "stop", "max_hold", "n", "avg_pct", "median_pct",
    "win_rate", "worst_pct", "avg_bars", "pct_per_bar", "compounded_pct",
    "avg_first_half_pct", "avg_second_half_pct", "robust",
]


@swing_app.command("grid")
def grid(
    symbols: Annotated[list[str], typer.Argument(help="One or more tickers.")],
    period: PeriodOpt = DEFAULT_SWING_PERIOD,
    windows: Annotated[str, typer.Option(help="Dip lookback windows (bars).")] = "5,10,20,50",
    dips: Annotated[str, typer.Option(help="Dip sizes, % below the window high.")] = (
        "3,5,8,10,12,15,20"
    ),
    entry: Annotated[
        list[str] | None,
        typer.Option(help='Trigger expression(s); replaces --windows/--dips. Repeatable.'),
    ] = None,
    tps: Annotated[str, typer.Option(help="Take-profit %s; 'none' = no target.")] = (
        "5,8,10,15,20,30,none"
    ),
    stops: Annotated[str, typer.Option(help="Stop %s; 'none' = no stop.")] = "none,8,12,20",
    holds: Annotated[str, typer.Option(help="Max bars held.")] = "10,20,40",
    min_trades: Annotated[int, typer.Option(help="Drop combos with fewer trades.")] = 8,
    where: WhereOpt = "robust",
    sort: SortOpt = "avg_pct",
    top: TopOpt = DEFAULT_TOP,
    csv: CsvOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Every entry × exit rule, simulated and ranked. Answers "what worked best?"."""
    _quiet_logs()
    try:
        entries = (
            expression_entries(entry) if entry
            else dip_entries(_numbers(windows), _numbers(dips))
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    spec = GridSpec(
        entries=entries,
        take_profits=_numbers(tps, allow_none=True),
        stops=_numbers(stops, allow_none=True),
        max_holds=tuple(int(h) for h in _numbers(holds)),
        min_trades=min_trades,
    )
    df = run_grid(_load(symbols, period), spec)
    if df.empty:
        _out.print(f"no combination reached {min_trades} trades")
        raise typer.Exit(EXIT_INSUFFICIENT_DATA)
    _emit(df[GRID_COLUMNS], where=where, sort=sort, top=top, csv=csv, as_json=as_json,
          title="swing grid")


@swing_app.command("test")
def test_rule(
    symbol: Annotated[str, typer.Argument(help="Ticker.")],
    entry: Annotated[str, typer.Option(help='Trigger expression, e.g. "dip(5,12)".')],
    tp: Annotated[float | None, typer.Option(help="Take-profit %.")] = None,
    stop: Annotated[float | None, typer.Option(help="Stop %.")] = None,
    hold: Annotated[int, typer.Option(help="Max bars held.")] = 20,
    period: PeriodOpt = DEFAULT_SWING_PERIOD,
    trades: Annotated[bool, typer.Option("--trades", help="List every trade.")] = False,
    as_json: JsonOpt = False,
) -> None:
    """One entry + exit rule: stats, and optionally every trade."""
    _quiet_logs()
    try:
        trigger = parse_trigger(entry)
        rule = ExitRule(take_profit_pct=tp, stop_pct=stop, max_hold=hold)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    close = _load([symbol], period)[symbol.upper().strip()]
    result = simulate(close, trigger(close), rule)
    stats = summarize(result, len(close))
    payload = {
        "symbol": symbol.upper(), "entry": entry, "rule": asdict(rule), "stats": asdict(stats),
        "trades": [
            {**asdict(t), "return_pct": round(t.return_pct, 3), "bars_held": t.bars_held}
            for t in result
        ] if trades else None,
    }
    if as_json:
        sys.stdout.write(json.dumps(payload, default=float) + "\n")
        return
    _out.print(payload["stats"])
    if trades:
        _emit(pd.DataFrame(payload["trades"]), where=None, sort=None, top=len(result),
              csv=None, as_json=False, title=f"{symbol.upper()} trades")


@swing_app.command("forward")
def forward(
    symbols: Annotated[list[str], typer.Argument(help="One or more tickers.")],
    when: Annotated[
        list[str] | None, typer.Option(help='Condition expression(s). Repeatable.')
    ] = None,
    runup_windows: Annotated[str, typer.Option(help="If no --when: run-up windows.")] = (
        "5,10,20"
    ),
    runup_pcts: Annotated[str, typer.Option(help="If no --when: run-up %s.")] = (
        "10,20,30,40,60"
    ),
    horizons: Annotated[str, typer.Option(help="Forward horizons (bars).")] = "5,10,20",
    min_bars: Annotated[int, typer.Option(help="Drop rows with fewer condition bars.")] = 8,
    period: PeriodOpt = DEFAULT_SWING_PERIOD,
    where: WhereOpt = None,
    sort: SortOpt = None,
    top: TopOpt = 50,
    csv: CsvOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """What usually happens next after a condition — e.g. is +30% in 5 days a top?"""
    _quiet_logs()
    try:
        if when:
            conditions = expression_entries(when)
        else:
            conditions = expression_entries(
                f"runup({w},{p:g})"
                for w in _numbers(runup_windows) for p in _numbers(runup_pcts)
            )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    spec = ForwardSpec(
        conditions=conditions,
        horizons=tuple(int(h) for h in _numbers(horizons)),
        min_bars=min_bars,
    )
    df = run_forward(_load(symbols, period), spec)
    if df.empty:
        _out.print(f"no condition reached {min_bars} bars")
        raise typer.Exit(EXIT_INSUFFICIENT_DATA)
    _emit(df, where=where, sort=sort, top=top, csv=csv, as_json=as_json,
          title="conditional forward returns")


@swing_app.command("levels")
def levels(
    symbol: Annotated[str, typer.Argument(help="Ticker.")],
    windows: Annotated[str, typer.Option(help="Lookback windows (bars).")] = "5,10,20,50",
    dips: Annotated[str, typer.Option(help="Dip %s to price out.")] = "5,8,12,15",
    period: PeriodOpt = "1y",
    as_json: JsonOpt = False,
) -> None:
    """Where the ticker is now vs. each window's high/low, and the price each dip triggers at."""
    _quiet_logs()
    close = _load([symbol], period)[symbol.upper().strip()]
    last = float(close[-1])
    rows = []
    for w in _numbers(windows):
        w = int(w)
        hi, lo = float(close[-w:].max()), float(close[-w:].min())
        row: dict[str, Any] = {
            "window": w, "last": last, "high": hi, "low": lo,
            "from_high_pct": (last / hi - 1) * 100, "from_low_pct": (last / lo - 1) * 100,
        }
        for d in _numbers(dips):
            row[f"dip{d:g}_at"] = hi * (1 - d / 100)
        rows.append(row)
    df = pd.DataFrame(rows)
    _emit(df, where=None, sort=None, top=len(df), csv=None, as_json=as_json,
          title=f"{symbol.upper()} levels (last close {last:.2f})")
