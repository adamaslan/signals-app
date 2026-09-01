# 2026-08-30 — `service.py` seam + `signals` CLI (platform steps 1–3)

**Commit**: `9c86e60` (PR #21). First 3 of the 7 steps in
`docs/signals-app-docs/signals-as-api-cli-mcp.md`.

## What changed

### One service module, thin adapters

`src/signals_app/service.py` is now the single seam every consumer goes
through. It is framework-agnostic — typed args in, Pydantic/dataclass out,
no `HTTPException` / `argparse` / `print`. It raises `SignalsError`
subclasses that each adapter translates:

| Exception | Meaning | HTTP | CLI exit |
|---|---|---|---|
| `SymbolNotFound` | provider returned no data for the ticker | 404 | 3 |
| `InsufficientData` | not enough bars for the requested window | 400 | 4 |
| `InvalidPeriod` | period not in `VALID_PERIODS` | 400 | 2 |
| `UpstreamUnavailable` | yfinance / LLM / DB unreachable or errored | 503 | 5 |

Functions: `analyze`, `analyze_many`, `backtest`, `backtest_many`,
`history`, `detectors`, `health`. This was **mostly moving code** out of
`api/routes.py` — `analyze()` is the old `get_signals` body minus the
`HTTPException`s.

**Partial batch is a return value, not an exception.** `analyze_many` /
`backtest_many` return `BatchResult{ok[], failed[]}` /
`UniverseBacktestResult` and never raise for one bad symbol. `backtest_many`
merges per-symbol buckets with `backtests.engine.merge_hit_rate_buckets`
(sum hits / sum totals — the weighted merge, not a mean of per-symbol
rates).

### `api/routes.py` rewired

The routes are now pure translation: parse request → one `service` call →
`_raise_http` maps the domain exception to a status. No pipeline logic left
in the module. Route bodies are unchanged — `tests/test_api_service_parity.py`
pins `GET /signals/{sym}` to be byte-identical to
`service.analyze(...).model_dump(mode="json")`.

### `signals` CLI (Typer + Rich)

`src/signals_app/cli/main.py`. Subcommands `analyze` / `backtest` /
`history` / `detectors` / `health` / `serve`. Conventions:

- `--json` — the payload to **stdout only**; all logs to stderr, so output
  pipes cleanly into `jq`.
- `--quiet` / `--verbose` — log level only, never changes the data.
- Batch `analyze` forces `--no-llm` (LLM synthesis costs money per symbol).
- Exit codes `0/1/2/3/4/5/6` (6 = batch partial success).

### Entry-point rename

`signals-analyze` historically **booted a web server** despite its name.
Now:

- `signals` → the real CLI (`signals_app.cli.main:app`)
- `signals-analyze` → a deprecation shim that prints a warning and forwards
  to `signals serve` — kept one release cycle so `run_local.sh` doesn't
  break.

`typer` + `rich` added to `pyproject.toml` and `environment.yml`. The
top-level `backtests` package is now installed (previously import-path-only)
so the console script can `import backtests.engine`.

## Layering rule

> No adapter imports `signals_app.detection` / `.scoring` / `.synthesis` /
> `.indicators` / `.data` directly — adapters import `signals_app.service`
> only.

Enforced by `tests/test_layering.py` (AST scan of the adapter modules).
This is what stops the "two assemblies of the same layers" drift
(`scan_universe.py` vs the API path) from recurring — that script becomes an
adapter in step 4.

## Steps 4–7 (same PR, later commits)

- **4 — scan seam.** The scan pipeline moved out of `scripts/scan_universe.py`
  into `src/signals_app/scanner.py`; that script is now `main()` + argparse
  only, re-exporting the names its tests use. `service.scan()` wraps
  `scanner.scan_universe` via `asyncio.to_thread`, adds a `ScanProgress`
  callback, returns a typed `ScanResult`. `signals scan` subcommand. The
  Actions workflow runs the script unchanged.
- **5 — MCP server.** `src/signals_app/mcp/server.py` — 9 read-only tools, 3
  resources, 2 prompts, each a `service` call + a provenance/disclaimer
  wrapper (COMPLIANCE.md §6 verbatim). Write tools gated behind
  `SIGNALS_MCP_ALLOW_WRITES=1`. `signals mcp` (stdio / `--http`). `.mcp.json`
  checked in. Compatible with MCP SDK 1.x (`FastMCP`) and 2.x (`MCPServer`).
- **6 — universes + presets.** `src/signals_app/universes.py` — local ticker
  baskets as CSV in `~/.signals/universes/` plus the browser exchange JSON.
  `signals universe {list,create,show,delete,run,backtest,export,import}`.
  `signals scan --universe NAME` and `--preset {bullish-2wk,best1}`. Only the
  two simple screens are presets; `scan_21_day_ds.py` / `scan_optimal_monthly.py`
  grew their own calibration and stay in `scripts/` with a deprecation header.
- **7 — cost gate.** `_cost_gate()` blocks a >25 LLM-call command without
  `--yes`; `--estimate` on `analyze` / `scan` prints the count and exits.
  `tests/test_cli_contract.py` pins the exit-code contract and `--json`
  purity against the real binary.
