"""The ``signals`` CLI — one binary, subcommands, ``--json`` everywhere.

Replaces the ``scripts/*.py`` sprawl with a single discoverable entry point
built on Typer. Every command is a thin adapter over ``signals_app.service``
(see ``docs/signals-app-docs/signals-as-api-cli-mcp.md`` §3).

Cross-cutting conventions (§3.3), enforced here for every command:

* ``--json``   — the Pydantic payload to **stdout only**; logs go to stderr.
  This is what makes the CLI pipeable into ``jq``.
* ``--quiet`` / ``--verbose`` — log level only, never changes the data output.
* ``--no-llm`` — skip synthesis; rule-based only; free.

Exit codes (§3.3) — so CI and shell scripts can branch::

    0  success
    1  unexpected error
    2  bad usage (Typer default)
    3  symbol not found / not in universe
    4  insufficient data for the requested window
    5  upstream unavailable (yfinance / LLM / Supabase)
    6  partial success (batch: some symbols failed)
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from enum import IntEnum
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from signals_app import service
from signals_app.config import DEFAULT_PERIOD
from signals_app.service import (
    InsufficientData,
    InvalidPeriod,
    SignalsError,
    SymbolNotFound,
    UpstreamUnavailable,
)

app = typer.Typer(
    name="signals",
    help="Programmable technical-analysis signals — one engine, many surfaces.",
    no_args_is_help=True,
    add_completion=True,
)

# stdout is reserved for data (esp. with --json); everything human goes to stderr.
_out = Console()
_err = Console(stderr=True)

# §3.4 cost safety — a command that would make more than this many LLM calls
# prints the count and requires --yes.
LLM_CALL_GATE = 25


def _cost_gate(estimated_calls: int, *, yes: bool) -> None:
    """Stop before a surprising number of LLM calls unless --yes was passed.

    Raises typer.Exit(USAGE) when the gate trips without --yes.
    """
    if estimated_calls <= LLM_CALL_GATE or yes:
        return
    _err.print(
        f"[yellow]This will make ~{estimated_calls} LLM calls.[/yellow] "
        f"Re-run with [bold]--yes[/bold] to proceed (or add --no-llm / --dry-run)."
    )
    raise typer.Exit(Exit.USAGE)


# Named scan presets (design doc §3.6) — one code path, different filter params.
# Only the two *simple* screens (horizon + direction over service.scan) are
# folded in here. The two that grew their own calibration / MIN_CATEGORIES
# gating — scan_21_day_ds.py, scan_optimal_monthly.py — are NOT presets yet;
# they still live in scripts/ and need their own PR to fold safely.
SCAN_PRESETS: dict[str, dict[str, Any]] = {
    "bullish-2wk": {
        "period": "2y",
        "direction": "bullish",
        "help": "Bullish screen, 10-day (~2 trading week) horizon. Was scan_bullish_2wk.py.",
    },
    "best1": {
        "period": "3mo",
        "direction": None,
        "help": "Both-direction screen over the default period. Was best1_scan.py.",
    },
}

logger = logging.getLogger("signals_app.cli")


class Exit(IntEnum):
    """CLI exit codes — see module docstring / design doc §3.3."""

    OK = 0
    ERROR = 1
    USAGE = 2
    SYMBOL_NOT_FOUND = 3
    INSUFFICIENT_DATA = 4
    UPSTREAM_UNAVAILABLE = 5
    PARTIAL = 6


def _configure_logging(quiet: bool, verbose: bool) -> None:
    """Route logs to stderr at a level set only by ``--quiet`` / ``--verbose``.

    stdout is never touched — that keeps ``--json`` output clean.
    """
    if quiet:
        level = logging.ERROR
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        stream=sys.stderr,
        force=True,
    )


def _exit_for(exc: BaseException) -> Exit:
    """Map a service domain exception to the matching CLI exit code."""
    if isinstance(exc, SymbolNotFound):
        return Exit.SYMBOL_NOT_FOUND
    if isinstance(exc, InsufficientData):
        return Exit.INSUFFICIENT_DATA
    if isinstance(exc, InvalidPeriod):
        return Exit.USAGE
    if isinstance(exc, UpstreamUnavailable):
        return Exit.UPSTREAM_UNAVAILABLE
    return Exit.ERROR


def _fail(exc: BaseException) -> None:
    """Print a one-line error to stderr and raise ``typer.Exit`` with the code."""
    code = _exit_for(exc)
    _err.print(f"[red]error:[/red] {exc}")
    raise typer.Exit(code)


def _run(coro: Any) -> Any:
    """Run a service coroutine, mapping any failure to ``typer.Exit`` + a code.

    ``SignalsError`` subclasses map per :func:`_exit_for`; anything else is an
    unexpected error → exit 1. ``typer.Exit`` raised elsewhere is re-raised
    untouched so command-level exit codes (0, 6) still work.
    """
    try:
        return asyncio.run(coro)
    except typer.Exit:
        raise
    except SignalsError as exc:
        _fail(exc)
    except Exception as exc:  # noqa: BLE001 — last-resort: one line to stderr, exit 1
        _fail(exc)


def _print_json(payload: Any) -> None:
    """Serialize ``payload`` as JSON to **stdout only** (the ``--json`` contract).

    Accepts a Pydantic model, a dataclass, or anything ``json.dumps`` handles.
    """
    if hasattr(payload, "model_dump"):
        data = payload.model_dump(mode="json")
    else:
        data = _dataclass_to_jsonable(payload)
    # print() not Console — Rich would wrap/style; we want bytes-clean JSON.
    print(json.dumps(data, default=str, separators=(",", ":")))


def _dataclass_to_jsonable(obj: Any) -> Any:
    """Recursively convert a (possibly nested) dataclass tree to plain JSON types."""
    import dataclasses

    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {
            f.name: _dataclass_to_jsonable(getattr(obj, f.name))
            for f in dataclasses.fields(obj)
        }
    if isinstance(obj, (list, tuple)):
        return [_dataclass_to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _dataclass_to_jsonable(v) for k, v in obj.items()}
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return obj


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------


@app.command()
def analyze(
    symbols: Annotated[list[str], typer.Argument(help="One or more ticker symbols.")],
    period: Annotated[str, typer.Option(help="yfinance period string.")] = DEFAULT_PERIOD,
    no_llm: Annotated[bool, typer.Option("--no-llm", help="Skip LLM synthesis (free).")] = False,
    llm: Annotated[
        bool, typer.Option("--llm", help="Opt a batch INTO LLM synthesis (costs money).")
    ] = False,
    yes: Annotated[
        bool, typer.Option("--yes", help="Proceed past the LLM-call cost gate.")
    ] = False,
    estimate: Annotated[
        bool, typer.Option("--estimate", help="Print the LLM-call count and exit without calling.")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Emit JSON to stdout only.")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Run the full L1–L5 pipeline for one or more symbols.

    One symbol → single ``SignalOutput``. Several symbols → a batch: rule-based
    by default (free); pass ``--llm`` to synthesize, which is gated at 25 calls
    unless ``--yes``. Partial success exits 6.
    """
    _configure_logging(quiet, verbose)

    if len(symbols) == 1:
        if estimate:
            _out.print("1 LLM call (0 with --no-llm)")
            raise typer.Exit(Exit.OK)
        result = _run(service.analyze(symbols[0], period, no_llm=no_llm))
        if as_json:
            _print_json(result)
        else:
            _render_signal(result)
        raise typer.Exit(Exit.OK)

    # Batch: rule-based unless --llm. --no-llm always wins.
    batch_no_llm = no_llm or not llm
    estimated = 0 if batch_no_llm else len(set(s.upper() for s in symbols))
    if estimate:
        _out.print(f"{estimated} LLM calls for {len(set(s.upper() for s in symbols))} symbols")
        raise typer.Exit(Exit.OK)
    if batch_no_llm and not no_llm and not llm:
        _err.print("[yellow]note:[/yellow] batch analyze is rule-based — pass --llm to synthesize")
    _cost_gate(estimated, yes=yes)
    batch = _run(service.analyze_many(symbols, period, no_llm=batch_no_llm))

    if as_json:
        _print_json(
            {
                "ok": [s.model_dump(mode="json") for s in batch.ok],
                "failed": [
                    {"symbol": f.symbol, "error_type": f.error_type, "message": f.message}
                    for f in batch.failed
                ],
            }
        )
    else:
        _render_batch(batch)

    if batch.failed and batch.ok:
        raise typer.Exit(Exit.PARTIAL)
    if batch.failed and not batch.ok:
        raise typer.Exit(Exit.UPSTREAM_UNAVAILABLE)
    raise typer.Exit(Exit.OK)


# ---------------------------------------------------------------------------
# backtest
# ---------------------------------------------------------------------------


@app.command()
def backtest(
    symbols: Annotated[list[str], typer.Argument(help="One or more ticker symbols.")],
    period: Annotated[str, typer.Option(help="yfinance period string.")] = "2y",
    horizon: Annotated[int, typer.Option("--horizon", help="Forward-return horizon (days).")] = 20,
    as_json: Annotated[bool, typer.Option("--json", help="Emit JSON to stdout only.")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Historical hit-rate for one symbol, or a merged hit-rate across a basket.

    Several symbols are merged with the correct weighted merge (sum hits / sum
    totals), not a mean of per-symbol rates.
    """
    _configure_logging(quiet, verbose)

    if len(symbols) == 1:
        result = _run(service.backtest(symbols[0], period, horizon))
        if as_json:
            _print_json(result)
        else:
            _render_backtest(result)
        raise typer.Exit(Exit.OK)

    merged = _run(service.backtest_many(symbols, period, horizon))

    if as_json:
        _print_json(merged)
    else:
        _render_universe_backtest(merged)

    if merged.symbols_failed and merged.symbols_ok:
        raise typer.Exit(Exit.PARTIAL)
    if merged.symbols_failed and not merged.symbols_ok:
        raise typer.Exit(Exit.UPSTREAM_UNAVAILABLE)
    raise typer.Exit(Exit.OK)


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


@app.command()
def history(
    symbol: Annotated[str, typer.Argument(help="Ticker symbol.")],
    limit: Annotated[int, typer.Option("--limit", help="Max rows (1–200).")] = 20,
    offset: Annotated[int, typer.Option("--offset", help="Pagination offset.")] = 0,
    as_json: Annotated[bool, typer.Option("--json", help="Emit JSON to stdout only.")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Show persisted analysis runs for a ticker, newest first."""
    _configure_logging(quiet, verbose)
    rows = _run(service.history(symbol, limit=limit, offset=offset))

    if as_json:
        _print_json([r.to_dict() for r in rows])
        raise typer.Exit(Exit.OK)

    if not rows:
        _err.print(f"no runs recorded for [bold]{symbol.upper()}[/bold]")
        raise typer.Exit(Exit.OK)

    table = Table(title=f"{symbol.upper()} — {len(rows)} run(s)")
    for col in ("ts", "period", "direction", "confidence", "ai_degraded", "no_llm"):
        table.add_column(col)
    for r in rows:
        table.add_row(
            str(r.ts),
            r.period,
            str(r.direction),
            f"{r.confidence:.3f}" if r.confidence is not None else "—",
            "yes" if r.ai_degraded else "no",
            "yes" if r.no_llm else "no",
        )
    _out.print(table)
    raise typer.Exit(Exit.OK)


# ---------------------------------------------------------------------------
# detectors
# ---------------------------------------------------------------------------


@app.command()
def detectors(
    as_json: Annotated[bool, typer.Option("--json", help="Emit JSON to stdout only.")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """List every registered detector with its calibrated hit-rate."""
    _configure_logging(quiet, verbose)
    infos = _run(service.detectors())

    if as_json:
        _print_json(infos)
        raise typer.Exit(Exit.OK)

    table = Table(title=f"{len(infos)} detectors")
    for col in ("name", "category", "calibrated_hit_rate", "description"):
        table.add_column(col)
    for d in infos:
        table.add_row(
            d.name,
            d.category or "—",
            f"{d.calibrated_hit_rate:.3f}" if d.calibrated_hit_rate is not None else "—",
            d.description or "",
        )
    _out.print(table)
    raise typer.Exit(Exit.OK)


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


@app.command()
def health(
    as_json: Annotated[bool, typer.Option("--json", help="Emit JSON to stdout only.")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Report upstream reachability (yfinance) and configured providers."""
    _configure_logging(quiet, verbose)
    report = _run(service.health())

    if as_json:
        _print_json(report)
        raise typer.Exit(Exit.OK if report.ok else Exit.UPSTREAM_UNAVAILABLE)

    table = Table(title="engine health")
    table.add_column("component")
    table.add_column("status")
    for k, v in report.detail.items():
        table.add_row(k, v)
    table.add_row("code_version", report.code_version)
    _out.print(table)
    raise typer.Exit(Exit.OK if report.ok else Exit.UPSTREAM_UNAVAILABLE)


# ---------------------------------------------------------------------------
# scan — the production universe scan (design doc §3.2, build step 4)
# ---------------------------------------------------------------------------


@app.command()
def scan(
    symbols: Annotated[list[str] | None, typer.Argument(help="Tickers to scan.")] = None,
    seed: Annotated[
        str | None, typer.Option("--seed", help="CSV file with a `ticker` column.")
    ] = None,
    universe: Annotated[
        str | None,
        typer.Option("--universe", help="Name of a local universe (see `signals universe`)."),
    ] = None,
    preset: Annotated[
        str | None,
        typer.Option("--preset", help=f"Named screen: {', '.join(SCAN_PRESETS)}."),
    ] = None,
    period: Annotated[str, typer.Option(help="yfinance period string.")] = DEFAULT_PERIOD,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Gate + log only — no LLM calls, no writes.")
    ] = False,
    trigger: Annotated[
        str, typer.Option("--trigger", help="cron | manual | backfill.")
    ] = "manual",
    shard: Annotated[
        str | None, typer.Option("--shard", metavar="INDEX/TOTAL", help="e.g. 0/4.")
    ] = None,
    matrix: Annotated[
        bool, typer.Option("--matrix", help="Also build the 5-timeframe matrix for gated symbols.")
    ] = False,
    direction: Annotated[
        str | None, typer.Option("--direction", help="bullish | bearish — gate one side only.")
    ] = None,
    estimate: Annotated[
        bool,
        typer.Option(
            "--estimate",
            help="Dry-run to report the gated count (= LLM calls a real run makes), then exit.",
        ),
    ] = False,
    yes: Annotated[
        bool, typer.Option("--yes", help="Proceed past the LLM-call cost gate.")
    ] = False,
    max_concurrent: Annotated[int, typer.Option("--max-concurrent")] = 8,
    as_json: Annotated[bool, typer.Option("--json", help="Emit JSON to stdout only.")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Run the production scan over a universe and persist publishable signals.

    Same code path the GitHub Actions workflow runs. ``--dry-run`` gates and
    logs without any LLM call or DB write. ``--estimate`` measures the gated
    count with a dry run and exits. A real run that would make more than 25
    LLM calls needs ``--yes``. Exit 6 if some symbols failed but not all.
    """
    _configure_logging(quiet, verbose)

    if trigger not in ("cron", "manual", "backfill"):
        _err.print("[red]error:[/red] --trigger must be cron | manual | backfill")
        raise typer.Exit(Exit.USAGE)

    if preset is not None:
        if preset not in SCAN_PRESETS:
            _err.print(
                f"[red]error:[/red] unknown --preset {preset!r} — one of: {', '.join(SCAN_PRESETS)}"
            )
            raise typer.Exit(Exit.USAGE)
        p = SCAN_PRESETS[preset]
        # A preset supplies defaults; an explicit flag still wins.
        if period == DEFAULT_PERIOD:
            period = p["period"]
        if direction is None:
            direction = p["direction"]

    if direction is not None and direction not in ("bullish", "bearish"):
        _err.print("[red]error:[/red] --direction must be bullish | bearish")
        raise typer.Exit(Exit.USAGE)

    if universe is not None:
        from signals_app import universes

        try:
            u = universes.load_universe(universe)
        except universes.UniverseError as exc:
            _err.print(f"[red]error:[/red] {exc}")
            raise typer.Exit(Exit.SYMBOL_NOT_FOUND) from None
        symbols = list(symbols or []) + u.tickers

    shard_tuple: tuple[int, int] | None = None
    if shard is not None:
        try:
            idx, tot = (int(x) for x in shard.split("/"))
            shard_tuple = (idx, tot)
        except ValueError:
            _err.print("[red]error:[/red] --shard must be INDEX/TOTAL, e.g. 0/4")
            raise typer.Exit(Exit.USAGE) from None

    def _dry_estimate() -> Any:
        return _run(
            service.scan(
                symbols or None,
                seed=seed,
                period=period,
                dry_run=True,
                trigger=trigger,  # type: ignore[arg-type]
                shard=shard_tuple,
                max_concurrent=max_concurrent,
                direction=direction,  # type: ignore[arg-type]
            )
        )

    # --estimate: report the gated count (= LLM calls a real run makes) and exit.
    if estimate:
        est = _dry_estimate()
        if as_json:
            _print_json(
                {"symbols_total": est.symbols_total, "estimated_llm_calls": est.symbols_published}
            )
        else:
            _out.print(
                f"{est.symbols_published} of {est.symbols_total} symbols would clear the "
                f"gate → ~{est.symbols_published} LLM calls on a real run"
            )
        raise typer.Exit(Exit.OK)

    # A real (non-dry) run synthesizes every gated symbol. Measure first with a
    # dry pass and gate on --yes if that's more than 25 calls — this is the
    # §3.4 "print the count, require --yes" rule applied before any spend.
    if not dry_run and not yes:
        est = _dry_estimate()
        _cost_gate(est.symbols_published, yes=yes)

    bar = _err.status("scanning…") if not as_json else None

    def _progress(p: Any) -> None:
        if bar is not None:
            bar.update(f"scanning… {p.done}/{p.total}  {p.ticker}")

    if bar is not None:
        bar.start()
    try:
        result = _run(
            service.scan(
                symbols or None,
                seed=seed,
                period=period,
                dry_run=dry_run,
                trigger=trigger,  # type: ignore[arg-type]
                shard=shard_tuple,
                max_concurrent=max_concurrent,
                compute_matrix=matrix,
                direction=direction,  # type: ignore[arg-type]
                progress=_progress,
            )
        )
    finally:
        if bar is not None:
            bar.stop()

    if as_json:
        _print_json(result)
    else:
        _out.print(
            f"scanned {result.symbols_total} — "
            f"[green]{result.symbols_published} published[/green], "
            f"{result.symbols_ok} ok, "
            f"[red]{result.symbols_failed} failed[/red]  "
            f"({result.elapsed_seconds}s{', dry-run' if result.dry_run else ''})"
        )
        pub = [o.ticker for o in result.outcomes if o.published]
        if pub:
            _out.print("  published: " + ", ".join(pub))
        bad = [(o.ticker, o.reason) for o in result.outcomes if not o.ok]
        if bad:
            _err.print("  failed: " + ", ".join(f"{t} ({r})" for t, r in bad))

    if result.partial:
        raise typer.Exit(Exit.PARTIAL)
    if result.symbols_failed and result.symbols_failed == result.symbols_total:
        raise typer.Exit(Exit.UPSTREAM_UNAVAILABLE)
    raise typer.Exit(Exit.OK)


# ---------------------------------------------------------------------------
# serve — what `signals-analyze` used to do
# ---------------------------------------------------------------------------


@app.command()
def serve(
    port: Annotated[int, typer.Option("--port", help="Port to bind.")] = 8000,
    host: Annotated[str, typer.Option("--host", help="Host to bind.")] = "0.0.0.0",
) -> None:
    """Boot the FastAPI app with uvicorn (the old ``signals-analyze`` behavior)."""
    import uvicorn

    _err.print(f"starting signals API on http://{host}:{port}")
    uvicorn.run("signals_app.api.main:app", host=host, port=port, reload=False)


# ---------------------------------------------------------------------------
# mcp — the MCP server (design doc §4.6)
# ---------------------------------------------------------------------------


@app.command()
def mcp(
    http: Annotated[
        bool, typer.Option("--http", help="Serve over streamable HTTP instead of stdio.")
    ] = False,
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port")] = 3333,
) -> None:
    """Run the MCP server — lets Claude query the engine directly.

    Default transport is stdio (Claude Desktop / Claude Code). ``--http`` runs
    streamable HTTP for remote / shared use. Read-only by default; write tools
    require ``SIGNALS_MCP_ALLOW_WRITES=1``.
    """
    from signals_app.mcp.server import run as run_mcp

    _err.print(
        f"starting signals MCP server ({'http ' + host + ':' + str(port) if http else 'stdio'})"
    )
    run_mcp("streamable-http" if http else "stdio", host=host, port=port)


# ---------------------------------------------------------------------------
# Rich renderers (human output)
# ---------------------------------------------------------------------------


def _render_signal(out: Any) -> None:
    """Render a single ``SignalOutput`` as a Rich table + evidence list."""
    sig = out.signal
    table = Table(title=f"{out.ticker} — {sig.timeframe.value}")
    table.add_column("field")
    table.add_column("value")
    table.add_row("direction", sig.direction.value)
    table.add_row("confidence", f"{sig.confidence:.3f}")
    table.add_row("ai_degraded", "yes" if sig.ai_degraded else "no")
    table.add_row("prompt_version", sig.prompt_version)
    if out.data_quality_score is not None:
        table.add_row("data_quality", f"{out.data_quality_score:.2f}")
    if out.code_version:
        table.add_row("code_version", out.code_version)
    if out.feature_unavailable:
        table.add_row("unavailable", ", ".join(out.feature_unavailable))
    _out.print(table)

    if sig.evidence.items:
        ev = Table(title="evidence")
        for col in ("source", "weight", "counter", "summary"):
            ev.add_column(col)
        for item in sig.evidence.items:
            ev.add_row(
                item.source.value,
                f"{item.weight:.3f}",
                "counter" if item.is_counter else "",
                item.summary,
            )
        _out.print(ev)


def _render_batch(batch: Any) -> None:
    """Render a ``BatchResult`` as one summary row per symbol."""
    table = Table(title=f"batch — {len(batch.ok)} ok, {len(batch.failed)} failed")
    for col in ("symbol", "direction", "confidence", "status"):
        table.add_column(col)
    for out in batch.ok:
        table.add_row(
            out.ticker,
            out.signal.direction.value,
            f"{out.signal.confidence:.3f}",
            "ok",
        )
    for f in batch.failed:
        table.add_row(f.symbol, "—", "—", f"{f.error_type}: {f.message}")
    _out.print(table)


def _render_backtest(result: Any) -> None:
    """Render a single-symbol ``BacktestResult``."""
    _out.print(
        f"[bold]{result.symbol}[/bold]  period={result.period}  "
        f"horizon={result.horizon_days}d  bars_scanned={result.bars_scanned}"
    )
    groups = (("by strength", result.by_strength), ("by category", result.by_category))
    for title, buckets in groups:
        table = Table(title=title)
        for col in ("key", "hits", "total", "hit_rate"):
            table.add_column(col)
        for b in buckets:
            table.add_row(b.key, str(b.hits), str(b.total), f"{b.hit_rate:.3f}")
        _out.print(table)


def _render_universe_backtest(merged: Any) -> None:
    """Render a merged ``UniverseBacktestResult``."""
    _out.print(
        f"merged over {len(merged.symbols_ok)} symbol(s), "
        f"{len(merged.symbols_failed)} failed  horizon={merged.horizon_days}d"
    )
    groups = (("by strength", merged.by_strength), ("by category", merged.by_category))
    for title, buckets in groups:
        table = Table(title=title)
        for col in ("key", "hits", "total", "hit_rate"):
            table.add_column(col)
        for b in buckets:
            table.add_row(b.key, str(b.hits), str(b.total), f"{b.hit_rate:.3f}")
        _out.print(table)
    if merged.symbols_failed:
        _err.print("failed: " + ", ".join(f.symbol for f in merged.symbols_failed))


# ---------------------------------------------------------------------------
# universe — local ticker baskets in ~/.signals/universes/ (design doc §3.5)
# ---------------------------------------------------------------------------

universe_app = typer.Typer(
    name="universe",
    help="Manage local ticker baskets (CSV in ~/.signals/universes/).",
    no_args_is_help=True,
)
app.add_typer(universe_app)


@universe_app.command("list")
def universe_list(
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List local universes."""
    from signals_app import universes

    us = universes.list_universes()
    if as_json:
        _print_json([{"name": u.name, "size": u.size, "path": str(u.path)} for u in us])
        raise typer.Exit(Exit.OK)
    if not us:
        _err.print(
            "no universes yet — create one with `signals universe create`\n"
            f"(dir: {universes.universe_dir()})"
        )
        raise typer.Exit(Exit.OK)
    table = Table(title=f"{len(us)} universe(s)")
    table.add_column("name")
    table.add_column("tickers", justify="right")
    for u in us:
        table.add_row(u.name, str(u.size))
    _out.print(table)
    raise typer.Exit(Exit.OK)


@universe_app.command("create")
def universe_create(
    name: Annotated[str, typer.Argument(help="Universe name (lowercase, - and _).")],
    tickers: Annotated[list[str] | None, typer.Argument(help="Tickers, or use --from.")] = None,
    from_csv: Annotated[
        str | None, typer.Option("--from", help="CSV file with a `ticker` column.")
    ] = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing universe.")] = False,
) -> None:
    """Create a universe from tickers on the command line and/or a CSV."""
    from signals_app import universes

    syms = list(tickers or [])
    if from_csv:
        try:
            syms += universes.tickers_from_csv(from_csv)
        except OSError as exc:
            _err.print(f"[red]error:[/red] {exc}")
            raise typer.Exit(Exit.ERROR) from None
    if not syms:
        _err.print("[red]error:[/red] no tickers — pass them as arguments or use --from FILE")
        raise typer.Exit(Exit.USAGE)
    try:
        u = universes.create_universe(name, syms, overwrite=force)
    except universes.UniverseExists as exc:
        _err.print(f"[red]error:[/red] {exc} (use --force to overwrite)")
        raise typer.Exit(Exit.ERROR) from None
    except universes.InvalidUniverseName as exc:
        _err.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(Exit.USAGE) from None
    _out.print(f"created [bold]{u.name}[/bold] with {u.size} ticker(s) → {u.path}")
    raise typer.Exit(Exit.OK)


@universe_app.command("show")
def universe_show(
    name: Annotated[str, typer.Argument()],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Print a universe's tickers."""
    from signals_app import universes

    try:
        u = universes.load_universe(name)
    except universes.UniverseError as exc:
        _err.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(Exit.SYMBOL_NOT_FOUND) from None
    if as_json:
        _print_json({"name": u.name, "tickers": u.tickers})
        raise typer.Exit(Exit.OK)
    _out.print(f"[bold]{u.name}[/bold] ({u.size})\n" + "\n".join(u.tickers))
    raise typer.Exit(Exit.OK)


@universe_app.command("delete")
def universe_delete(name: Annotated[str, typer.Argument()]) -> None:
    """Delete a universe file."""
    from signals_app import universes

    try:
        universes.delete_universe(name)
    except universes.UniverseError as exc:
        _err.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(Exit.SYMBOL_NOT_FOUND) from None
    _out.print(f"deleted [bold]{name}[/bold]")
    raise typer.Exit(Exit.OK)


@universe_app.command("run")
def universe_run(
    name: Annotated[str, typer.Argument()],
    period: Annotated[str, typer.Option()] = DEFAULT_PERIOD,
    no_llm: Annotated[bool, typer.Option("--no-llm")] = True,
    as_json: Annotated[bool, typer.Option("--json")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Analyze every ticker in a universe (rule-based by default)."""
    _configure_logging(quiet, verbose)
    from signals_app import universes

    try:
        u = universes.load_universe(name)
    except universes.UniverseError as exc:
        _err.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(Exit.SYMBOL_NOT_FOUND) from None

    batch = _run(service.analyze_many(u.tickers, period, no_llm=no_llm))
    if as_json:
        _print_json(
            {
                "universe": u.name,
                "ok": [s.model_dump(mode="json") for s in batch.ok],
                "failed": [
                    {"symbol": f.symbol, "error_type": f.error_type, "message": f.message}
                    for f in batch.failed
                ],
            }
        )
    else:
        _render_batch(batch)
    if batch.failed and batch.ok:
        raise typer.Exit(Exit.PARTIAL)
    if batch.failed and not batch.ok:
        raise typer.Exit(Exit.UPSTREAM_UNAVAILABLE)
    raise typer.Exit(Exit.OK)


@universe_app.command("backtest")
def universe_backtest(
    name: Annotated[str, typer.Argument()],
    horizon: Annotated[int, typer.Option("--horizon")] = 20,
    period: Annotated[str, typer.Option()] = "2y",
    as_json: Annotated[bool, typer.Option("--json")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Merged historical hit-rate across a universe."""
    _configure_logging(quiet, verbose)
    from signals_app import universes

    try:
        u = universes.load_universe(name)
    except universes.UniverseError as exc:
        _err.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(Exit.SYMBOL_NOT_FOUND) from None

    merged = _run(service.backtest_many(u.tickers, period, horizon))
    if as_json:
        _print_json(merged)
    else:
        _render_universe_backtest(merged)
    if merged.symbols_failed and merged.symbols_ok:
        raise typer.Exit(Exit.PARTIAL)
    raise typer.Exit(Exit.OK)


@universe_app.command("export")
def universe_export(
    name: Annotated[str, typer.Argument()],
    fmt: Annotated[str, typer.Option("--format", help="csv | json.")] = "csv",
) -> None:
    """Print a universe to stdout as CSV (git-friendly) or the browser JSON."""
    from signals_app import universes

    try:
        u = universes.load_universe(name)
    except universes.UniverseError as exc:
        _err.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(Exit.SYMBOL_NOT_FOUND) from None
    if fmt == "json":
        print(universes.to_export_json(u))
    elif fmt == "csv":
        print("ticker")
        for t in u.tickers:
            print(t)
    else:
        _err.print("[red]error:[/red] --format must be csv | json")
        raise typer.Exit(Exit.USAGE)
    raise typer.Exit(Exit.OK)


@universe_app.command("import")
def universe_import(
    file: Annotated[str, typer.Argument(help="A browser-exported universe JSON file.")],
    name: Annotated[
        str | None, typer.Option("--name", help="Override the name in the file.")
    ] = None,
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Create a local universe from a browser-exported JSON file."""
    from signals_app import universes

    try:
        text = Path(file).read_text()
    except OSError as exc:
        _err.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(Exit.ERROR) from None
    try:
        parsed_name, tickers = universes.from_export_json(text)
    except universes.UniverseError as exc:
        _err.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(Exit.USAGE) from None
    try:
        u = universes.create_universe(name or parsed_name, tickers, overwrite=force)
    except universes.UniverseExists as exc:
        _err.print(f"[red]error:[/red] {exc} (use --force)")
        raise typer.Exit(Exit.ERROR) from None
    _out.print(f"imported [bold]{u.name}[/bold] — {u.size} ticker(s) → {u.path}")
    raise typer.Exit(Exit.OK)


# ---------------------------------------------------------------------------
# Deprecation shim — keep `signals-analyze` working for one release cycle
# ---------------------------------------------------------------------------


def _deprecated_analyze_shim() -> None:
    """Entry point kept for the old ``signals-analyze`` console script.

    ``signals-analyze`` historically booted uvicorn despite its name (design
    doc §1.1). It now prints a deprecation warning and forwards to
    ``signals serve`` so nobody's ``run_local.sh`` breaks.
    """
    print(
        "signals-analyze is deprecated and will be removed. It never ran an "
        "analysis — it started the web server. Use `signals serve` instead "
        "(or `signals analyze SYMBOL` for an actual analysis).",
        file=sys.stderr,
    )
    serve()


if __name__ == "__main__":
    app()
