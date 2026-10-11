"""Chain runner — compose scans, studies, and holdem verdicts into one thesis.

A chain is an ordered list of steps over a working set of symbols. Each step
can *enrich* every surviving symbol's row (attach signal state, a dip study,
a holdem verdict) and/or *filter* the set. Every step logs what went in, what
came out, and why each dropped symbol was dropped, so a chain's conclusion is
auditable rather than a bare ticker list.

Example: "of these semis, which are bullish on confluence, currently in a
10-day dip, and still HOLD EM — ranked by historical dip-buy edge":

    [
      {"op": "symbols",   "params": {"symbols": ["SNDK", "MU", "WDC", "NVDA"]}},
      {"op": "signals",   "params": {"min_confluence": 0.1}},
      {"op": "dip_study", "params": {"windows": [5, 10, 20], "dip_pct": 6,
                                      "in_dip_window": 10}},
      {"op": "holdem",    "params": {"keep": ["HOLD EM"],
                                      "verdict_params": {"hold_threshold": 58}}},
      {"op": "rank",      "params": {"by": "dip.10.edge_vs_baseline_pct", "top": 3}}
    ]
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Final

import pandas as pd

from signals_app import universes
from signals_app.clients.holdem import HoldemClient, HoldemUnavailable
from signals_app.config import MAX_API_BATCH_SYMBOLS
from signals_app.service import BatchResult
from signals_app.studies.dip import DipStudyParams, WindowStudy, run_dip_study
from signals_app.swing.grid import GridSpec, dip_entries, run_grid

logger = logging.getLogger(__name__)

MAX_CHAIN_STEPS: Final[int] = 12
DIP_STUDY_CONCURRENCY: Final[int] = 4
HOLDEM_CONCURRENCY: Final[int] = 4
DEFAULT_DIP_PERIOD: Final[str] = "5y"
DEFAULT_SIGNAL_PERIOD: Final[str] = "3mo"
SWING_MIN_BARS: Final[int] = 30
SWING_DEFAULT_TOP: Final[int] = 5
SWING_MAX_COMBOS: Final[int] = 20_000
SWING_SORT_COLUMNS: Final[frozenset[str]] = frozenset(
    {"avg_pct", "median_pct", "win_rate", "pct_per_bar", "compounded_pct", "n", "worst_pct"}
)

FetchDaily = Callable[[str, str], pd.DataFrame]
AnalyzeMany = Callable[..., Awaitable[BatchResult]]


class ChainError(ValueError):
    """The chain definition is invalid (unknown op, bad params, too large)."""


@dataclass(frozen=True)
class ChainStep:
    """One step: an op name plus its params."""

    op: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepLog:
    """What one step did to the working set."""

    op: str
    params: dict[str, Any]
    symbols_in: int
    symbols_out: int = 0
    dropped: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    skipped: bool = False
    elapsed_seconds: float = 0.0


Op = Callable[[dict[str, Any], list[str], dict, StepLog], Awaitable[list[str]]]


@dataclass
class ChainResult:
    """Final surviving symbols, their accumulated rows, and the step audit."""

    symbols: list[str]
    rows: dict[str, dict[str, Any]]
    steps: list[StepLog]

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict, rows ordered like ``symbols``."""
        return {
            "symbols": self.symbols,
            "rows": [{"symbol": s, **self.rows.get(s, {})} for s in self.symbols],
            "steps": [asdict(s) for s in self.steps],
        }


def _compact_window(w: WindowStudy) -> dict[str, Any]:
    return {
        "n_dips": w.n_dips,
        "low_sample": w.low_sample,
        "median_trigger_to_trough_bars": w.median_trigger_to_trough_bars,
        "median_depth_pct": w.median_depth_pct,
        "recovery_rate": w.recovery_rate,
        "median_recovery_bars": w.median_recovery_bars,
        "best_entry_delay": w.best_entry_delay,
        "best_entry_mean_return_pct": w.best_entry_mean_return_pct,
        "edge_vs_baseline_pct": w.edge_vs_baseline_pct,
        "in_dip": w.current.in_dip if w.current else False,
        "drawdown_pct": w.current.drawdown_pct if w.current else None,
        "bars_since_trigger": w.current.bars_since_trigger if w.current else None,
    }


def _lookup(row: dict[str, Any], path: str) -> Any:
    node: Any = row
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _exit_axis(
    params: dict[str, Any], key: str, default: tuple[float | None, ...]
) -> tuple[float | None, ...]:
    """A take-profit/stop list from JSON, where ``null`` means "no such exit"."""
    raw = params.get(key, default)
    return tuple(None if v is None else float(v) for v in raw)


def _require_known(op: str, params: dict[str, Any], known: set[str]) -> None:
    unknown = set(params) - known
    if unknown:
        raise ChainError(f"{op}: unknown params {sorted(unknown)}; known: {sorted(known)}")


OP_DOCS: Final[dict[str, str]] = {
    "symbols": "Set the working set. params: symbols (list) and/or universe (saved universe name).",
    "signals": (
        "Run the rule-based signal pipeline (no LLM). params: period, min_confluence, "
        "max_confluence, bias ('bullish'|'bearish'). Attaches row.signal."
    ),
    "dip_study": (
        "Dip-buy timing study on daily history. params: period (default 5y), any "
        "DipStudyParams field (windows, dip_pct, max_entry_delay, horizon_days, "
        "max_recovery_bars), in_dip_window (keep only symbols currently dipping on that "
        "window), min_dips. Attaches row.dip.<window>."
    ),
    "swing_grid": (
        "Buy-the-dip x exit-rule grid per symbol (swing lab). params: period, windows, dip_pcts, "
        "take_profits, stops, max_holds (null = none), min_trades, robust_only, sort "
        "(avg_pct|median_pct|win_rate|pct_per_bar|compounded_pct|n|worst_pct), top, "
        "min_best_avg_pct (drop symbols whose best combo is below it). Attaches row.swing."
    ),
    "holdem": (
        "Hold Em / Fold Em verdict via HOLDEM_API_URL. params: period, keep (list of "
        "verdicts to keep), verdict_params (holdem threshold overrides). Attaches row.holdem."
    ),
    "rank": (
        "Sort by a dotted row path. params: by (e.g. dip.10.edge_vs_baseline_pct), "
        "descending, top."
    ),
}


class ChainRunner:
    """Executes a chain. Dependencies are injected so tests run offline."""

    def __init__(
        self,
        *,
        fetch_daily: FetchDaily,
        analyze_many: AnalyzeMany,
        holdem: HoldemClient | None,
    ) -> None:
        self._fetch_daily = fetch_daily
        self._analyze_many = analyze_many
        self._holdem = holdem
        self._ops: dict[str, Op] = {
            "symbols": self._op_symbols,
            "signals": self._op_signals,
            "dip_study": self._op_dip_study,
            "swing_grid": self._op_swing_grid,
            "holdem": self._op_holdem,
            "rank": self._op_rank,
        }

    async def run(self, steps: Sequence[ChainStep]) -> ChainResult:
        """Run ``steps`` in order. Raises :class:`ChainError` on a bad definition."""
        if not steps:
            raise ChainError("a chain needs at least one step")
        if len(steps) > MAX_CHAIN_STEPS:
            raise ChainError(f"a chain is capped at {MAX_CHAIN_STEPS} steps")
        if steps[0].op != "symbols":
            raise ChainError("the first step must be 'symbols'")
        for step in steps:
            if step.op not in self._ops:
                raise ChainError(f"unknown op {step.op!r}; known: {sorted(self._ops)}")

        symbols: list[str] = []
        rows: dict[str, dict[str, Any]] = {}
        logs: list[StepLog] = []
        for step in steps:
            log = StepLog(op=step.op, params=dict(step.params), symbols_in=len(symbols))
            started = time.perf_counter()
            symbols = await self._ops[step.op](step.params, symbols, rows, log)
            log.symbols_out = len(symbols)
            log.elapsed_seconds = round(time.perf_counter() - started, 3)
            logs.append(log)
            logger.info(
                "chain step=%s in=%d out=%d dropped=%d",
                step.op, log.symbols_in, log.symbols_out, len(log.dropped),
            )
        return ChainResult(symbols=symbols, rows={s: rows.get(s, {}) for s in symbols}, steps=logs)

    async def _op_symbols(
        self, params: dict[str, Any], _: list[str], rows: dict, log: StepLog
    ) -> list[str]:
        _require_known("symbols", params, {"symbols", "universe"})
        picked: list[str] = [s.strip().upper() for s in params.get("symbols", []) if s.strip()]
        if params.get("universe"):
            try:
                picked.extend(universes.load_universe(params["universe"]).tickers)
            except universes.UniverseError as exc:
                raise ChainError(f"symbols: {exc}") from exc
        deduped = list(dict.fromkeys(picked))
        if not deduped:
            raise ChainError("symbols: no symbols given")
        if len(deduped) > MAX_API_BATCH_SYMBOLS:
            raise ChainError(f"symbols: capped at {MAX_API_BATCH_SYMBOLS} per chain")
        for s in deduped:
            rows.setdefault(s, {})
        return deduped

    async def _op_signals(
        self, params: dict[str, Any], symbols: list[str], rows: dict, log: StepLog
    ) -> list[str]:
        _require_known("signals", params, {"period", "min_confluence", "max_confluence", "bias"})
        batch = await self._analyze_many(
            symbols, params.get("period", DEFAULT_SIGNAL_PERIOD), no_llm=True
        )
        for failure in batch.failed:
            log.dropped[failure.symbol] = f"{failure.error_type}: {failure.message}"
        lo, hi = params.get("min_confluence"), params.get("max_confluence")
        bias = params.get("bias")
        kept: set[str] = set()
        for out in batch.ok:
            state = out.state
            score = state.confluence_score if state else None
            rows.setdefault(out.ticker, {})["signal"] = {
                "direction": out.signal.direction.value,
                "confidence": out.signal.confidence,
                "confluence_score": score,
                "bias": state.bias if state else None,
                "action": state.action if state else None,
                "close": state.close if state else None,
                "rsi": state.rsi if state else None,
            }
            if lo is not None and (score is None or score < lo):
                log.dropped[out.ticker] = f"confluence {score} < {lo}"
            elif hi is not None and (score is None or score > hi):
                log.dropped[out.ticker] = f"confluence {score} > {hi}"
            elif bias and (state is None or (state.bias or "").lower() != bias.lower()):
                log.dropped[out.ticker] = f"bias {state.bias if state else None} != {bias}"
            else:
                kept.add(out.ticker)
        return [s for s in symbols if s in kept]

    async def _op_dip_study(
        self, params: dict[str, Any], symbols: list[str], rows: dict, log: StepLog
    ) -> list[str]:
        local = {"period", "in_dip_window", "min_dips"}
        study_keys = set(DipStudyParams.__dataclass_fields__)
        _require_known("dip_study", params, local | study_keys)
        try:
            study_params = DipStudyParams.from_overrides(
                {k: v for k, v in params.items() if k in study_keys}
            )
        except (TypeError, ValueError) as exc:
            raise ChainError(f"dip_study: {exc}") from exc
        in_dip_window = params.get("in_dip_window")
        if in_dip_window is not None and in_dip_window not in study_params.windows:
            raise ChainError("dip_study: in_dip_window must be one of windows")
        min_dips = int(params.get("min_dips", 0))
        period = params.get("period", DEFAULT_DIP_PERIOD)
        log.params["resolved"] = {**asdict(study_params), "windows": list(study_params.windows)}

        gate = asyncio.Semaphore(DIP_STUDY_CONCURRENCY)

        async def one(symbol: str) -> tuple[str, dict[str, Any] | str]:
            async with gate:
                try:
                    df = await asyncio.to_thread(self._fetch_daily, symbol, period)
                    result = run_dip_study(symbol, df, study_params)
                except ValueError as exc:
                    return symbol, str(exc)
            return symbol, {str(w.window): _compact_window(w) for w in result.windows}

        kept: list[str] = []
        for symbol, outcome in await asyncio.gather(*(one(s) for s in symbols)):
            if isinstance(outcome, str):
                log.dropped[symbol] = outcome
                continue
            rows[symbol]["dip"] = outcome
            focus = outcome[str(in_dip_window)] if in_dip_window is not None else None
            if focus is not None and not focus["in_dip"]:
                log.dropped[symbol] = f"not in a {in_dip_window}-day dip"
            elif min_dips and min(w["n_dips"] for w in outcome.values()) < min_dips:
                log.dropped[symbol] = f"fewer than {min_dips} dips on some window"
            else:
                kept.append(symbol)
        return [s for s in symbols if s in set(kept)]

    async def _op_swing_grid(
        self, params: dict[str, Any], symbols: list[str], rows: dict, log: StepLog
    ) -> list[str]:
        _require_known(
            "swing_grid", params,
            {"period", "windows", "dip_pcts", "take_profits", "stops", "max_holds",
             "min_trades", "robust_only", "sort", "top", "min_best_avg_pct"},
        )
        sort = params.get("sort", "avg_pct")
        if sort not in SWING_SORT_COLUMNS:
            raise ChainError(f"swing_grid: sort must be one of {sorted(SWING_SORT_COLUMNS)}")
        try:
            windows = tuple(int(w) for w in params.get("windows", (5, 10, 20, 50)))
            dip_pcts = tuple(float(p) for p in params.get("dip_pcts", (5, 8, 12, 15)))
            defaults = GridSpec(entries={})
            spec = GridSpec(
                entries=dip_entries(windows, dip_pcts),
                take_profits=_exit_axis(params, "take_profits", defaults.take_profits),
                stops=_exit_axis(params, "stops", defaults.stops),
                max_holds=tuple(int(h) for h in params.get("max_holds", defaults.max_holds)),
                min_trades=int(params.get("min_trades", defaults.min_trades)),
            )
            spec.exit_rules()  # validates every exit combination up front
        except (TypeError, ValueError) as exc:
            raise ChainError(f"swing_grid: {exc}") from exc
        combos = len(spec.entries) * len(spec.exit_rules())
        if combos > SWING_MAX_COMBOS:
            raise ChainError(f"swing_grid: {combos} combos exceeds the {SWING_MAX_COMBOS} cap")
        robust_only = bool(params.get("robust_only", True))
        top = int(params.get("top", SWING_DEFAULT_TOP))
        min_best = params.get("min_best_avg_pct")
        period = params.get("period", DEFAULT_DIP_PERIOD)
        log.params["resolved"] = {"combos": combos, "robust_only": robust_only, "sort": sort}

        gate = asyncio.Semaphore(DIP_STUDY_CONCURRENCY)

        async def one(symbol: str) -> tuple[str, dict[str, Any] | str]:
            async with gate:
                try:
                    df = await asyncio.to_thread(self._fetch_daily, symbol, period)
                    close = df["Close"].astype(float).dropna().to_numpy()
                except (KeyError, ValueError) as exc:
                    return symbol, str(exc)
                if len(close) < SWING_MIN_BARS:
                    return symbol, f"only {len(close)} daily bars"
                table = await asyncio.to_thread(run_grid, {symbol: close}, spec)
            if table.empty:
                return symbol, "no entry x exit combo reached min_trades"
            if robust_only:
                table = table[table["robust"]]
                if table.empty:
                    return symbol, "no robust combo"
            best = table.sort_values(sort, ascending=False, na_position="last").head(top)
            return symbol, {
                "bars": len(close),
                "combos_tested": combos,
                "combos_kept": len(table),
                "best": best.drop(columns=["symbol"]).to_dict(orient="records"),
            }

        kept: set[str] = set()
        for symbol, outcome in await asyncio.gather(*(one(s) for s in symbols)):
            if isinstance(outcome, str):
                log.dropped[symbol] = outcome
                continue
            rows[symbol]["swing"] = outcome
            top_avg = outcome["best"][0]["avg_pct"] if outcome["best"] else None
            if min_best is not None and (top_avg is None or top_avg < float(min_best)):
                log.dropped[symbol] = f"best swing avg {top_avg} below {min_best}"
            else:
                kept.add(symbol)
        return [s for s in symbols if s in kept]

    async def _op_holdem(
        self, params: dict[str, Any], symbols: list[str], rows: dict, log: StepLog
    ) -> list[str]:
        _require_known("holdem", params, {"period", "keep", "verdict_params"})
        if self._holdem is None:
            log.skipped = True
            log.warnings.append("HOLDEM_API_URL is unset; holdem step skipped, nothing filtered")
            logger.warning("chain holdem step skipped: HOLDEM_API_URL unset")
            return symbols
        keep = {v.upper() for v in params.get("keep") or []}
        gate = asyncio.Semaphore(HOLDEM_CONCURRENCY)

        async def one(symbol: str) -> tuple[str, dict[str, Any] | str]:
            async with gate:
                try:
                    v = await self._holdem.verdict(
                        symbol,
                        period=params.get("period", DEFAULT_SIGNAL_PERIOD),
                        params=params.get("verdict_params"),
                    )
                except HoldemUnavailable as exc:
                    return symbol, str(exc)
            return symbol, v

        kept: set[str] = set()
        for symbol, outcome in await asyncio.gather(*(one(s) for s in symbols)):
            if isinstance(outcome, str):
                log.dropped[symbol] = outcome
                continue
            rows[symbol]["holdem"] = {
                k: outcome.get(k)
                for k in (
                    "verdict", "confidence", "bias", "risk_level", "entry", "stop",
                    "target", "risk_reward", "summary", "params_used",
                )
            }
            verdict = str(outcome.get("verdict", "")).upper()
            if keep and verdict not in keep:
                log.dropped[symbol] = f"verdict {verdict} not in {sorted(keep)}"
            else:
                kept.add(symbol)
        return [s for s in symbols if s in kept]

    async def _op_rank(
        self, params: dict[str, Any], symbols: list[str], rows: dict, log: StepLog
    ) -> list[str]:
        _require_known("rank", params, {"by", "descending", "top"})
        by = params.get("by")
        if not by:
            raise ChainError("rank: 'by' is required")
        descending = bool(params.get("descending", True))
        present = [s for s in symbols if isinstance(_lookup(rows[s], by), (int, float))]
        missing = [s for s in symbols if s not in set(present)]
        if missing:
            log.warnings.append(f"{len(missing)} symbol(s) have no numeric {by}; ranked last")
        present.sort(key=lambda s: _lookup(rows[s], by), reverse=descending)
        ordered = present + missing
        top = params.get("top")
        if top is not None:
            for s in ordered[int(top):]:
                log.dropped[s] = f"outside top {top} by {by}"
            ordered = ordered[: int(top)]
        return ordered
