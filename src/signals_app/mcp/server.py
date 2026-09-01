"""MCP server — exposes the signals engine to Claude and other agents.

Design doc §4. Every tool is a ``signals_app.service`` function with a
decorator and a provenance/disclaimer wrapper. Deliberately small — nine
read-only tools, three resources, two prompts (§4.3). The write operations
(``scan``, ``calibrate``) are **not** exposed unless
``SIGNALS_MCP_ALLOW_WRITES=1`` (§4.3, and not implemented in v1).

Transports (§4.6)::

    signals mcp                    # stdio — Claude Desktop / Claude Code
    signals mcp --http --port 3333 # streamable HTTP — remote / shared

Secrets: the server reads the same ``.env`` the rest of the app uses. It
**never** accepts credentials as tool arguments — a tool arg is model context
and gets logged.
"""
from __future__ import annotations

import dataclasses
import logging
import os
from typing import Any

from signals_app import service

logger = logging.getLogger(__name__)

# --- MCP SDK: 2.x renamed FastMCP → MCPServer; 1.x has FastMCP. Optional dep:
# importing this module must not hard-fail when `mcp` isn't installed — the
# `signals mcp` command errors at call time instead (see build_server).
_Server: Any = None
_MCP_IMPORT_ERROR: str | None = None
try:
    try:  # 2.x
        from mcp.server.mcpserver import MCPServer as _Server
    except ModuleNotFoundError:  # 1.x
        from mcp.server.fastmcp import FastMCP as _Server
except ImportError as exc:  # `mcp` not installed at all
    _MCP_IMPORT_ERROR = str(exc)


# COMPLIANCE.md §6 short-form — the source of truth for this wording. Do not
# paraphrase it here; if it changes there, change it here.
DISCLAIMER = (
    "Not financial advice. Signals are automated, generalized, educational "
    "information — not a recommendation, solicitation, or personalized advice. "
    "Markets are risky; you can lose money. Past performance does not guarantee "
    "future results. Data may be delayed or inaccurate. Do your own research "
    "and consult a licensed professional before investing."
)

# §4.6 — write tools are off unless this is explicitly set.
ALLOW_WRITES = os.getenv("SIGNALS_MCP_ALLOW_WRITES") == "1"

# §4.5.6 — bounded batch sizes; return a clean error, never a timeout.
MAX_ANALYZE_SYMBOLS = 25
MAX_BACKTEST_UNIVERSE = 500


def _jsonable(obj: Any) -> Any:
    """Recursively turn dataclasses / Pydantic models into plain JSON types."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    return obj


def _with_provenance(payload: dict[str, Any]) -> dict[str, Any]:
    """Attach the standing disclaimer to any analytical result (§4.5.3).

    Provenance fields (``bar_ts``, ``created_at``, ``code_version``,
    ``ai_degraded``, ``data_quality_score``) already live inside the
    ``SignalOutput`` payload the service returns — this only adds the
    disclaimer envelope so a model has no way to read the number without it.
    """
    return {"disclaimer": DISCLAIMER, **payload}


def build_server() -> Any:
    """Construct and return the configured MCP server instance.

    Raises:
        RuntimeError: The ``mcp`` package is not installed (``pip install mcp``).
    """
    if _Server is None:
        raise RuntimeError(
            f"the 'mcp' package is required for the signals MCP server "
            f"({_MCP_IMPORT_ERROR}). Install it: pip install 'mcp>=1.2'"
        )
    mcp = _Server(
        "signals",
        instructions=(
            "Technical-analysis signals over one engine. Every analytical "
            "result is educational information, not investment advice "
            "(see the `disclaimer` field). Prefer `analyze_symbols` "
            "(rule-based, free) for baskets; `analyze_symbol` costs an LLM "
            "call unless no_llm=true."
        ),
    )

    # -- tools (§4.3) -------------------------------------------------------

    @mcp.tool()
    async def analyze_symbol(symbol: str, period: str = "3mo", no_llm: bool = False) -> dict:
        """Run the full technical-analysis pipeline on one ticker.

        Returns a directional signal (strong_buy…strong_sell) with a
        confidence score, weighted supporting AND counter evidence, and
        data-quality flags. Use no_llm=true for a free, rule-based-only
        result. Costs one LLM call otherwise.
        """
        out = await service.analyze(symbol, period, no_llm=no_llm)
        return _with_provenance(_jsonable(out))

    @mcp.tool()
    async def analyze_symbols(symbols: list[str], period: str = "3mo") -> dict:
        """Rule-based signals for up to 25 tickers at once. Free.

        Partial success is returned, never raised: `ok` holds the signals
        that computed, `failed` names the symbols that didn't and why.
        """
        if len(symbols) > MAX_ANALYZE_SYMBOLS:
            return _with_provenance(
                {
                    "error": "too_many_symbols",
                    "message": (
                        f"analyze_symbols accepts at most {MAX_ANALYZE_SYMBOLS} symbols, "
                        f"got {len(symbols)} — split the request"
                    ),
                    "ok": [],
                    "failed": [],
                }
            )
        batch = await service.analyze_many(symbols, period, no_llm=True)
        return _with_provenance(
            {
                "ok": [_jsonable(s) for s in batch.ok],
                "failed": [
                    {"symbol": f.symbol, "error_type": f.error_type, "message": f.message}
                    for f in batch.failed
                ],
            }
        )

    @mcp.tool()
    async def backtest_symbol(symbol: str, period: str = "2y", horizon_days: int = 20) -> dict:
        """Historical hit-rate for one ticker, by strength and by category. Free.

        Answers "does a HIGH-confidence label actually mean a higher
        hit-rate" for this symbol over the given window.
        """
        res = await service.backtest(symbol, period, horizon_days)
        return _with_provenance(_jsonable(res))

    @mcp.tool()
    async def backtest_universe(
        symbols: list[str], period: str = "2y", horizon_days: int = 20
    ) -> dict:
        """Merged hit-rate across a basket (≤500 tickers), by strength/category. Free.

        The merge is weighted (sum hits / sum totals), not a mean of
        per-symbol rates.
        """
        if len(symbols) > MAX_BACKTEST_UNIVERSE:
            return _with_provenance(
                {
                    "error": "too_many_symbols",
                    "message": (
                        f"backtest_universe accepts at most {MAX_BACKTEST_UNIVERSE} symbols, "
                        f"got {len(symbols)} — split the request"
                    ),
                    "by_category": [],
                    "by_strength": [],
                }
            )
        res = await service.backtest_many(symbols, period, horizon_days)
        return _with_provenance(_jsonable(res))

    @mcp.tool()
    async def get_signal_history(symbol: str, limit: int = 50) -> dict:
        """Past analysis runs for a ticker from the database, newest first. Free."""
        rows = await service.history(symbol, limit=limit)
        return {"ticker": symbol.upper(), "runs": [r.to_dict() for r in rows]}

    @mcp.tool()
    async def list_detectors() -> dict:
        """Every registered detector: name, category, description, calibrated hit-rate. Free."""
        infos = await service.detectors()
        return {"detectors": [_jsonable(d) for d in infos]}

    @mcp.tool()
    async def get_calibration() -> dict:
        """The active strength→hit-rate calibration table, if one exists. Free."""
        from signals_app.scoring.calibration import load_strength_hit_rates

        table = load_strength_hit_rates() or {}
        return {
            "calibration": table,
            "note": "empty means no calibration run yet — uncalibrated defaults are in use",
        }

    @mcp.tool()
    async def list_universe() -> dict:
        """Which tickers the scheduled scanner actually covers. Free.

        Reads the seed CSV the GitHub Actions scan runs against — lets you
        check coverage before analyzing instead of discovering a gap by
        failing.
        """
        from pathlib import Path

        from signals_app import scanner

        seed = Path(__file__).resolve().parents[3] / "seed" / "universe_symbols.csv"
        try:
            tickers = scanner.load_symbols_from_csv(str(seed))
        except OSError as exc:
            return {"error": f"seed universe unreadable: {exc}", "tickers": []}
        return {"count": len(tickers), "tickers": tickers}

    @mcp.tool()
    async def engine_health() -> dict:
        """Upstream reachability (yfinance) + configured LLM provider. Free."""
        return _jsonable(await service.health())

    # -- write tools, only if explicitly enabled (§4.6) -------------------
    if ALLOW_WRITES:
        logger.warning("SIGNALS_MCP_ALLOW_WRITES=1 — exposing write tools (scan)")

        @mcp.tool()
        async def run_scan(
            symbols: list[str] | None = None, period: str = "3mo", dry_run: bool = True
        ) -> dict:
            """Run the universe scan. WRITES to Supabase unless dry_run=true. Costs LLM calls."""
            res = await service.scan(symbols or None, period=period, dry_run=dry_run)
            return _jsonable(res)

    # -- resources (§4.4) ------------------------------------------------

    @mcp.resource("signals://universe")
    async def universe_resource() -> str:
        """The scanned ticker universe as CSV."""
        u = await list_universe()
        return "ticker\n" + "\n".join(u.get("tickers", []))

    @mcp.resource("signals://calibration/active")
    async def calibration_resource() -> str:
        """Active calibration table: measured hit-rate per strength bucket."""
        import json

        c = await get_calibration()
        return json.dumps(c["calibration"], indent=2, sort_keys=True)

    @mcp.resource("signals://schema")
    async def schema_resource() -> str:
        """SignalOutput JSON schema + what the key fields mean."""
        import json

        from signals_app.schemas.signal_output import SignalOutput

        return json.dumps(
            {
                "schema": SignalOutput.model_json_schema(),
                "field_notes": {
                    "data_quality_score": (
                        "0–1 guard on the input OHLCV; < 0.7 means stale/gappy data"
                    ),
                    "signal.ai_degraded": (
                        "true → the LLM call failed and a rule-based fallback was used"
                    ),
                    "code_version": (
                        "which detection/scoring code produced this signal (provenance)"
                    ),
                    "signal.confidence": (
                        "probabilistic estimate, not a promise; > 0.6 requires a "
                        "counter-evidence item"
                    ),
                },
            },
            indent=2,
        )

    # -- prompts (§4.4) ------------------------------------------------

    @mcp.prompt()
    def analyze_basket(tickers: str) -> str:
        """Analyze a basket: coverage → rule-based signals → backtest → divergences."""
        return (
            f"For the basket [{tickers}]:\n"
            "1. Call list_universe and note which of these the scanner covers.\n"
            "2. Call analyze_symbols (rule-based, free) for all of them.\n"
            "3. Call backtest_universe for the same set.\n"
            "4. Summarize: which names have the strongest signal, how that "
            "signal's strength bucket has historically performed, and any "
            "short-vs-long-term divergence. Restate the disclaimer."
        )

    @mcp.prompt()
    def explain_signal(symbol: str) -> str:
        """Explain a signal honestly: evidence, counter-evidence, data quality, hit-rate."""
        return (
            f"Call analyze_symbol('{symbol}') and get_calibration. Then explain "
            "the signal honestly: the supporting evidence AND the counter-"
            "evidence with their weights, the data_quality_score and what "
            "lowered it, the calibrated hit-rate for this confidence bucket "
            "(with n), and what observation would falsify the call. End with "
            "the disclaimer."
        )

    return mcp


def run(transport: str = "stdio", *, host: str = "127.0.0.1", port: int = 3333) -> None:
    """Run the server on the given transport (``stdio`` or ``streamable-http``)."""
    mcp = build_server()
    if transport == "stdio":
        mcp.run("stdio")
    else:
        # settings vary a little across SDK versions; set what exists.
        for attr, val in (("host", host), ("port", port)):
            try:
                setattr(mcp.settings, attr, val)
            except Exception:  # noqa: BLE001
                pass
        mcp.run("streamable-http")


if __name__ == "__main__":
    run()
