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
    as_json: Annotated[bool, typer.Option("--json", help="Emit JSON to stdout only.")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Run the full L1–L5 pipeline for one or more symbols.

    One symbol → single ``SignalOutput``. Several symbols → a batch: partial
    success exits 6, and ``--no-llm`` is forced on for a batch (cost safety,
    §3.4).
    """
    _configure_logging(quiet, verbose)

    if len(symbols) == 1:
        result = _run(service.analyze(symbols[0], period, no_llm=no_llm))
        if as_json:
            _print_json(result)
        else:
            _render_signal(result)
        raise typer.Exit(Exit.OK)

    # Batch path — force no_llm regardless of the flag.
    if not no_llm:
        _err.print("[yellow]note:[/yellow] batch analyze is rule-based (--no-llm forced)")
    batch = _run(service.analyze_many(symbols, period, no_llm=True))

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
