# Hybrid Claude Pipeline — build-out plan

**Written:** 2026-09-03 · **State:** design, nothing built yet · **Branch:** `claude/hybrid-financial-analysis-pipeline-1caovh`

A build-out plan for a two-tier Claude system on top of the existing signals
engine: **Claude Opus 5 explores** (writes throwaway analysis code in a sandbox,
proposes new capabilities), **Claude Haiku 4.5 executes** (picks and calls the
MCP tools that already exist), and the successful experiments get **promoted**
into permanent MCP tools by a GitHub Action that opens a PR.

The one idea worth understanding before anything else: **`signals_app.service`
is already the single seam, and the flywheel is a loop that ends by widening
it.** An experiment starts as Opus-written code in `sandbox/`, gets logged every
time it runs, and — if it keeps working — is promoted into a `service` function
plus an MCP tool, at which point Haiku can call it for a tenth of the cost and
Opus never has to write it again. Everything below follows from that ordering.

---

## Outline

1. [What already exists](#1-what-already-exists)
2. [Deltas from the source brief](#2-deltas-from-the-source-brief)
3. [The two tiers](#3-the-two-tiers)
4. [How the models reach the tools](#4-how-the-models-reach-the-tools)
5. [The tool layer](#5-the-tool-layer)
6. [The sandbox](#6-the-sandbox)
7. [The experiment log](#7-the-experiment-log)
8. [The capability flywheel](#8-the-capability-flywheel)
9. [Culture, psychology, philosophy](#9-culture-psychology-philosophy)
10. [GitHub pipeline](#10-github-pipeline)
11. [Configuration](#11-configuration)
12. [Repository map after the build](#12-repository-map-after-the-build)
13. [PR plan](#13-pr-plan)
14. [Acceptance criteria](#14-acceptance-criteria)
15. [Cost model](#15-cost-model)
16. [Risks and compliance](#16-risks-and-compliance)
17. [Runbook](#17-runbook)

---

## 1. What already exists

This is not a greenfield repo. Roughly 60% of the brief is already shipped under
different names, and the build-out is mostly *addition at the edges*.

| Brief asks for | Repo already has | Where |
|---|---|---|
| MCP tool layer | 9 read-only MCP tools + 3 resources + 2 prompts | `src/signals_app/mcp/server.py` |
| `mcp.json` registration | `.mcp.json` → `signals mcp` (stdio) | `.mcp.json` |
| `get_market_data` | `DataFetcher` + `service.analyze()` | `src/signals_app/data/fetcher.py` |
| `calculate_rsi` / `calculate_macd` | 18 detectors over a shared indicator frame | `src/signals_app/indicators/compute.py` |
| `backtest_strategy` | `service.backtest()` / `backtest_many()`, forward-horizon scoring | `backtests/engine.py` |
| Promotion feedback loop | Calibration: measured hit-rates feed back into confidence labels | `src/signals_app/scoring/calibration.py` |
| CI + cron workflows | 5 workflows (ci, signals-scan, backfill, calibrate, deploy-pages) | `.github/workflows/` |
| "one seam every consumer goes through" | `signals_app.service`, enforced by a layering test | `src/signals_app/service.py`, `tests/test_layering.py` |
| Disclaimer discipline | `COMPLIANCE.md` §6 short-form, wrapped around every MCP payload | `COMPLIANCE.md`, `mcp/server.py` |

What is genuinely missing:

- **No Anthropic provider.** LLM synthesis is OpenRouter → Gemini → rule-based
  (`src/signals_app/synthesis/mtf_llm.py`, `config.py:214–230`). There is no
  Claude call path anywhere in the codebase.
- **No router.** One model does one job (per-timeframe synthesis). Nothing
  chooses between a big model and a small one.
- **No sandbox.** Nothing executes model-written code.
- **No experiment log, no promotion.** Calibration measures *detector* quality;
  nothing measures *experiment* quality.
- **No cultural/psychological layer.** Sentiment is not in the engine at all.

So the build is: **Anthropic provider → router → sandbox → experiment log →
promotion → cultural layer**, in that order, each one a PR.

---

## 2. Deltas from the source brief

Six places where the brief's assumptions don't match this repo. Each is a
deliberate deviation, not an oversight.

| # | Brief says | We do | Why |
|---|---|---|---|
| 1 | Python 3.9+ | Python 3.12 | `pyproject.toml` already pins `requires-python = ">=3.12"`; the codebase uses `X \| None` unions throughout. Downgrading is a rewrite for no gain. |
| 2 | Mock/CSV market data | Real `DataFetcher`, with a fixture cassette mode for CI | The fetcher, the retry logic, and the data-quality scoring already exist and are tested. Mocking them would be a second, worse data path. CI gets `SIGNALS_FIXTURES=1` and reads `tests/fixtures/`. |
| 3 | New tools `get_market_data`, `calculate_rsi`, `calculate_macd` | Thin `service` functions over the existing indicator frame | Indicators are computed once per symbol into one DataFrame. Re-deriving RSI in a standalone tool would drift from what the detectors actually see. |
| 4 | `run_code` executes arbitrary Opus code with "no network access" | Same, but described honestly as a *speed bump on a laptop, a jail in CI* | POSIX `rlimit` + an isolated interpreter + a scrubbed environment + a cwd jail is real defense in depth. It is not a security boundary against hostile code. In CI the sandbox runs inside the job container with `--network none` semantics. See §6. |
| 5 | Two-tier is Opus + Haiku only | Opus 5 + Haiku 4.5, with Sonnet 5 as a configured-but-off middle rung | Router config is a table; leaving a middle rung declared costs nothing and makes the escalation ladder a config change rather than a code change. Off by default so the brief's constraint holds. |
| 6 | "Philosophical risk assessment" as a tool output | Same tool, but every payload carries the `COMPLIANCE.md` §6 disclaimer and a `heuristic: true` provenance flag | A tool that maps portfolio metrics to "Stoic" is a *framing device*, not an assessment. It ships behind the same disclaimer wrapper as everything else, and its output schema says so in-band. |

---

## 3. The two tiers

### Model IDs and prices

| Tier | Model ID | Context | Input $/1M | Output $/1M | Thinking |
|---|---|---|---|---|---|
| Exploration | `claude-opus-5` | 1M | $5.00 | $25.00 | On by default (adaptive); tune with `output_config.effort` |
| Execution | `claude-haiku-4-5` | 200K | $1.00 | $5.00 | `{type: "enabled", budget_tokens: N}` only; **`effort` errors** |
| Middle (declared, off) | `claude-sonnet-5` | 1M | $2.00 | $10.00 | Adaptive |

Two API facts that shape the code and are easy to get wrong:

- **`output_config.effort` is Opus/Sonnet-5-era only.** Sending it to
  `claude-haiku-4-5` is an error. The provider layer must build request kwargs
  per-tier, not share one dict.
- **Opus 5 thinks by default.** Omitting `thinking` runs adaptive. Do *not*
  disable it to save tokens — lower `effort` instead; disabled thinking on Opus
  5 can put a tool call into visible text instead of a `tool_use` block, which
  in an agent loop fails silently.

### Personas

Two system prompts, version-controlled as files so prompt changes show up in
diffs (`config/prompts/opus_quant_philosopher.md`,
`config/prompts/haiku_execution.md`).

**Opus — "Quant-Philosopher."** Explores. Has the sandbox and the full tool set.
Told: the engine's publication gate rejects most signals before an LLM ever sees
them, so its job is to find *new reasons to reject*, not new reasons to buy.
Told to write code that returns a JSON metrics dict, because that is what the
experiment log stores. Told that a hypothesis that fails is a successful
experiment.

**Haiku — "Fast Execution Trader."** Selects tools and extracts parameters. No
sandbox. Gets the tool list and nothing else — no market narrative, no
speculation. Told to return the tool result, not to interpret it. Told to answer
"escalate" when the request doesn't map cleanly onto a tool, which is the router's
upgrade signal.

### The router

`src/signals_app/routing/router.py`. Deterministic, cheap, and boring — it must
not call a model to decide which model to call.

```python
@dataclass(frozen=True)
class Route:
    tier: Literal["execution", "exploration"]
    model: str
    reason: str          # logged; makes routing decisions auditable
    escalated_from: str | None = None
```

Decision order (first match wins):

1. **Explicit override** — `tier=` argument or `SIGNALS_FORCE_TIER` env.
2. **Sandbox required** — the request asks for code, a novel calculation, a
   backtest of a strategy that isn't a registered detector → exploration.
3. **Tool-shaped** — the request matches a registered tool's trigger vocabulary
   (symbol + a known verb: analyze, backtest, history, health, calibration,
   universe) and is under `max_execution_tokens` → execution.
4. **Complexity heuristics** — token count, count of distinct symbols, presence
   of "why"/"compare"/"design"/"explain the interaction", multi-step phrasing.
   Above the configured threshold → exploration.
5. **Default** → execution.

Escalation, from `config/models.yaml`:

- Haiku returns `"escalate"`, or emits malformed tool arguments, or the tool call
  raises a `SignalsError` that isn't a user error → retry once on Haiku, then
  escalate to Opus with the failure transcript attached.
- `haiku_failure_streak >= N` (default 3) within a session → route the rest of
  the session to Opus and log a `router.sticky_escalation` event.

Escalation is one-way within a session. A session that has escalated never
silently drops back to Haiku — that oscillation is what makes hybrid systems
feel unreliable.

The router is pure: query in, `Route` out. Every routing decision is written to
the experiment log with its `reason`, which is what makes the acceptance test
("router picks the right tier for a set of sample queries") a plain table test.

---

## 4. How the models reach the tools

This is the design decision most likely to be gotten wrong, so it gets its own
section.

There are two ways to give Claude our MCP tools, and they are not
interchangeable:

**A. The MCP connector (`mcp_servers` + `mcp_toolset`).** Anthropic's servers
connect *outbound* to an MCP server over HTTP. Requires beta
`mcp-client-2025-11-20`, and requires both halves — `mcp_servers=[{type:"url",
url, name}]` alone is a validation error; you also need
`tools=[{type:"mcp_toolset", mcp_server_name: <same name>}]`. **This needs a
publicly reachable URL.** Our server is `signals mcp` over stdio, or
`--http --port 3333` bound to localhost. Not reachable. Not this.

**B. Host-side tools via the Tool Runner.** `client.beta.messages.tool_runner`
with `@beta_tool`-decorated Python functions. The functions call
`signals_app.service` directly, in-process. The loop, retries, and tool-result
plumbing come from the SDK; we supply the functions.

**We use B.** The reason is layering, not convenience: the MCP server and the
Claude tool runner become *two adapters over the same seam*, exactly like the
FastAPI routes and the CLI already are. One tool definition module
(`src/signals_app/agents/tools.py`) generates both — the MCP registration and
the `@beta_tool` wrappers — from one list, so a tool can never exist in one and
not the other. `tests/test_layering.py` gets a new assertion covering it.

```
                    ┌─────────────────────────┐
   Claude Code ────▶│  mcp/server.py (stdio)  │──┐
   Claude Desktop   └─────────────────────────┘  │
                                                 ├──▶ signals_app.service ──▶ engine
   app/agents ─────▶┌─────────────────────────┐  │         (the seam)
   (tool_runner)    │  agents/tools.py        │──┘
                    └─────────────────────────┘
```

Option A stays documented as the path for when the engine is exposed publicly
(a Fly/Render deployment of `signals mcp --http`), because at that point the
same tool list works unchanged.

**Prompt caching.** Render order is `tools` → `system` → `messages`. The tool
list is generated from a sorted registry and the persona files are frozen, so
the prefix is stable by construction; put a `cache_control: {"type":
"ephemeral"}` breakpoint after the system block and keep the per-request
question after it. Assert `usage.cache_read_input_tokens > 0` on the second call
of the smoke test — a zero there means someone put a timestamp in a prompt.
Caches are model-scoped, so Opus and Haiku each keep their own; that is a real
cost of the cascade and is priced in §15.

---

## 5. The tool layer

### Existing tools (unchanged, now also exposed to the tool runner)

`analyze_symbol`, `analyze_symbols`, `backtest_symbol`, `backtest_universe`,
`get_signal_history`, `list_detectors`, `get_calibration`, `list_universe`,
`engine_health`, plus `run_scan` behind `SIGNALS_MCP_ALLOW_WRITES=1`.

### New tools

Each is a `service` function first and a tool second. Bounded, fast, and
JSON-serializable.

| Tool | Signature | Returns | Notes |
|---|---|---|---|
| `get_market_data` | `(symbol, period="3mo", interval="1d")` | OHLCV rows + `data_quality` score | Thin wrapper over `DataFetcher`; capped at 2,000 rows, downsampled beyond that. Exists so a model can *see* data without inventing a fetch path. |
| `get_indicators` | `(symbol, period, names=None)` | Named indicator series (RSI, MACD, ATR, …) from the shared frame | Replaces the brief's separate `calculate_rsi` / `calculate_macd`. One call, one frame, no drift from what detectors see. |
| `search_news` | `(query, limit=20, sentiment=False)` | Headlines + optional sentiment/rhetoric scores | v1 reads a local JSONL corpus in `data/news/`. No new network dependency. Returns `source: "local_corpus"` so the model can't mistake it for live news. |
| `detect_cognitive_bias` | `(analysis_text)` | List of `{bias, evidence_span, confidence}` | Keyword + structural heuristics (recency, anchoring, confirmation, survivorship, narrative). Runs over *our own* generated analysis, which is the point — it's a self-check, not a market signal. |
| `assess_philosophical_stance` | `(portfolio \| metrics, stance=None)` | Framing text + the risk-parameter deltas that stance implies | Deterministic mapping table in `config/stances.yaml`. Carries `heuristic: true` and the §6 disclaimer. Named `assess_…` rather than the brief's `get_philosophical_risk_assessment` because it returns a framing, not a risk assessment, and the name should not overclaim. |
| `run_experiment` | `(code, inputs, timeout=30)` | `{stdout, metrics, error, duration_ms, log_id}` | **Opus only.** The sandbox front door. See §6. |
| `log_experiment_result` | `(log_id, verdict, notes)` | ack | Lets the model record its own read of a result; the promoter reads both the metrics and the verdict. |

Every tool: `strict: true` on the schema, `additionalProperties: false`,
explicit `required`. Bounded inputs (`MAX_ANALYZE_SYMBOLS = 25`,
`MAX_BACKTEST_UNIVERSE = 500` already exist — new tools follow the same pattern
and return a clean error rather than timing out). Every payload goes through
`_with_provenance()` and carries `DISCLAIMER`.

---

## 6. The sandbox

`src/signals_app/sandbox/runner.py`.

```python
def run_code(
    code: str,
    inputs: dict | None = None,
    *,
    timeout: float = 30.0,
    memory_mb: int = 512,
) -> SandboxResult
```

Mechanics:

1. Write `code` to `sandbox/run_<uuid>.py`. Never `exec()` in-process — a
   `SystemExit` or a `while True` in model-written code would take the host down.
2. Serialize `inputs` to `sandbox/input_<uuid>.json`; the script reads it from
   `argv[1]`. Results are whatever the script writes to
   `sandbox/output/<uuid>.json`, plus captured stdout.
3. `subprocess.run([sys.executable, "-I", "-S", script, input_path], ...)` —
   `-I` is isolated mode (ignores `PYTHON*` env vars and the user site dir),
   `-S` skips `site`.
4. `cwd=sandbox/output/<uuid>/`. Relative writes land in the jail.
5. **Environment scrubbed to an allowlist.** `PATH`, `HOME`, `LANG`, and
   `SIGNALS_SANDBOX=1`. Explicitly dropped: `ANTHROPIC_API_KEY`,
   `SUPABASE_*`, `OPENROUTER_API_KEY`, `GEMINI_API_KEY`, `GITHUB_TOKEN`,
   `HTTP_PROXY`, `HTTPS_PROXY`. A test asserts the deny-list is a superset of
   every key `config.py` reads.
6. `preexec_fn` sets `RLIMIT_CPU`, `RLIMIT_AS` (`memory_mb`), `RLIMIT_NOFILE`,
   `RLIMIT_FSIZE`, and `RLIMIT_NPROC` (POSIX only; on non-POSIX the runner
   raises rather than silently running unlimited).
7. Wall-clock timeout of 30s enforced by `subprocess`; on expiry, kill the
   **process group** (`start_new_session=True` + `killpg`), not just the child.
8. Every run is appended to the experiment log — success and failure alike.

**What this is not.** Stripping proxy variables is not a network jail; a
determined script can still open a socket. On a laptop this is defense in depth
against *mistakes*, which is the actual threat model — the code is written by
Claude in response to our own prompts, not by an attacker. Where the threat
model is different (CI, and any future hosted runner), the sandbox runs inside
the job container with networking disabled at the container level, and
`weekly-review.yml` / `ci.yml` set `SIGNALS_SANDBOX_REQUIRE_ISOLATION=1` so the
runner refuses to start if it can't confirm it. Say this in the runbook too;
nobody should read "sandbox" and think "safe to run untrusted code."

Reads are advisory-restricted to `data/`: the runner passes a `DATA_DIR`
constant and the persona prompt says to use it, and a post-run check flags any
absolute path outside `data/` and `sandbox/output/` that appears in the script
source. Advisory, and labeled as such.

---

## 7. The experiment log

JSONL at `logs/experiments/YYYY-MM.jsonl`, one object per run. JSONL, not
SQLite: it diffs, it appends atomically, it survives a killed process, and
`weekly-review.yml` can read a month of it without a driver.

```json
{
  "log_id": "exp_01J8...",
  "ts": "2026-09-03T18:22:04Z",
  "session_id": "sess_01J8...",
  "tier": "exploration",
  "model": "claude-opus-5",
  "route_reason": "sandbox_required",
  "prompt_sha": "9f2c...",
  "code_sha": "3ab1...",
  "code_normalized_sha": "77de...",
  "code_path": "sandbox/archive/exp_01J8.py",
  "inputs": {"symbols": ["SPY"], "period": "2y"},
  "success": true,
  "duration_ms": 4120,
  "metrics": {"hit_rate": 0.61, "n": 214, "sharpe": 0.83},
  "verdict": "promising",
  "error": null,
  "usage": {"input_tokens": 18422, "output_tokens": 2104, "cache_read_input_tokens": 17900}
}
```

`code_normalized_sha` is the load-bearing field: the code with comments,
docstrings, whitespace, and literal numerals stripped, then hashed. Two runs of
"the same idea with different parameters" collapse to one normalized hash, which
is what makes "used more than 5 times" mean anything. Raw `code_sha` is kept for
provenance.

Query helpers in `src/signals_app/experiments/store.py`:
`append()`, `iter_runs(since=)`, `group_by_normalized_sha()`,
`promotion_candidates(config)`.

---

## 8. The capability flywheel

A normalized-code-group is a **promotion candidate** when all of:

| Gate | Default | Why |
|---|---|---|
| `runs >= 5` | 5 | The brief's threshold. Enough to not be a fluke. |
| `success_rate >= 0.8` | 0.8 | Failures are informative but not promotable. |
| `distinct_inputs >= 3` | 3 | Guards against five runs on one symbol. A tool that only works on SPY isn't a tool. |
| `median_duration_ms <= 5000` | 5000 | A promoted tool is called by Haiku in a latency-sensitive loop. |
| `no_network_imports` | true | Rejects candidates importing `requests`/`urllib`/`socket`; a promoted tool fetches through `DataFetcher` or not at all. |
| `not already_promoted` | — | `logs/promoted.json` maps normalized hashes to the PR that promoted them. |

`src/signals_app/experiments/promote.py` takes a `log_id` or a normalized hash
and emits, into a working tree:

1. `src/signals_app/service.py` addition — the experiment body, reshaped into a
   typed function returning a Pydantic model. Opus does the reshaping (this is
   an exploration task, called with the candidate code and the seam's
   conventions in the prompt); the script owns the file surgery and the
   validation, never the model.
2. A tool registration entry in `agents/tools.py` (which regenerates the MCP
   registration for free — §4).
3. `tests/test_promoted_<name>.py` — parametrized over the logged inputs, with
   the logged metrics as expected values, plus a schema round-trip test.
4. A docs stanza appended to `docs/tools.md`.
5. A `logs/promoted.json` entry.

Then: branch, commit, `ruff check` + `mypy` + `pytest` on the changed paths, and
**only if green**, open a PR titled `promote: <tool_name>` with the log
excerpt, the metrics table, and the candidate's run history in the body. A red
check means the promotion script opens nothing and writes a `promotion_failed`
event to the log instead. A promotion PR is a proposal, and a human merges it —
the flywheel proposes, it does not self-modify `main`.

The loop closes here: once merged, the same computation costs a Haiku tool call
instead of an Opus sandbox session, and the router's tool-shaped branch starts
matching requests that used to escalate.

---

## 9. Culture, psychology, philosophy

Three modules, all deterministic, all cheap, all clearly labeled as heuristics.
None of them are permitted to move a signal's score on their own — they annotate
and they gate, they do not vote. That constraint is what keeps the publication
gate meaningful.

**`src/signals_app/culture/corpus.py`.** Loads short public-domain excerpts from
`data/texts/` (Marcus Aurelius, *Meditations*; Shakespeare; Plato; Mackay's
*Extraordinary Popular Delusions*, which is the on-the-nose one and earns its
place). Each file has a front-matter header with source, edition, and a
public-domain note. `analyze_text_for_market_parallels(text)` returns matched
passages plus the theme tags they carry (`crowd`, `mean_reversion`, `hubris`,
`patience`). Keyword and phrase matching over a curated tag index — no
embeddings, no model call, milliseconds.

**`src/signals_app/culture/rhetoric.py`.** Classifies a passage as
Logos/Pathos/Ethos by feature counts (numerals and causal connectives → logos;
intensifiers, second person, exclamation → pathos; authority citation and hedged
attribution → ethos). Feeds `search_news(sentiment=True)`: a headline scoring
high on pathos gets its sentiment magnitude **damped**, on the theory that
emotional intensity in financial media carries less information than its
loudness implies. The damping factor is config, and the default is documented
as an unvalidated prior — a `docs/tools.md` note says so, and it is the first
thing a backtest should be pointed at.

**`src/signals_app/culture/stances.py`.** `config/stances.yaml` maps a stance to
risk-parameter deltas used by `backtest()` and by the publication gate:

| Stance | Position size | Stop distance | Min confluence | Max concurrent | Framing |
|---|---|---|---|---|---|
| `stoic` | ×0.6 | tighter | +1 | 3 | Control what you control; drawdown is the only real input |
| `epicurean` | ×0.8 | wider | 0 | 5 | Modest, sustainable, avoids the pain of forced exits |
| `skeptic` | ×0.5 | tighter | +2 | 2 | Suspends judgment; publishes least |
| `absurdist` | ×1.0 | wider | −1 | 8 | The control arm — deliberately un-opinionated |
| `none` | ×1.0 | default | 0 | default | Default; stance off |

`absurdist` is the control arm, and its presence is the honest part of this
section: if a stance doesn't beat the control in a backtest, it is a UI flavor,
not a strategy, and `docs/tools.md` should say which is which once measured.

**`detect_cognitive_bias`** runs over our own LLM-written synthesis before
publication, not over market text. If the write-up for a signal trips two or
more bias heuristics, the publication gate holds it for review. That is the
psychology layer paying rent.

---

## 10. GitHub pipeline

| Workflow | Trigger | Does |
|---|---|---|
| `ci.yml` *(extend)* | push to `main`, PR | Add: `pytest tests/test_sandbox.py`, `tests/test_router.py`, `tests/test_promote.py`; sandbox smoke test with `SIGNALS_SANDBOX_REQUIRE_ISOLATION=1`; ruff + mypy on **new paths only, blocking** (the pre-existing findings stay advisory — see the note in `ci.yml`) |
| `agent-smoke.yml` *(new)* | PR touching `agents/`, `routing/`, `sandbox/`; manual | One Haiku tool call and one Opus sandbox run against fixtures. Skipped with a neutral status when `ANTHROPIC_API_KEY` is absent (forks) rather than failing. Asserts cache hit on the second call. |
| `weekly-review.yml` *(new)* | cron Sun 07:00 UTC; manual | Scans `logs/experiments/`, computes candidates, and either opens one `promote:` PR per candidate (max 3/week) or an issue listing near-misses with the gate they failed. No candidates → exits 0 silently. |
| `docs.yml` *(new)* | push to `main` touching `agents/tools.py`, `service.py` | Regenerates `docs/tools.md` from the tool registry and commits it if changed. Fails the build if the committed file is stale on a PR. |
| `signals-scan.yml`, `calibrate.yml`, `backfill.yml`, `deploy-pages.yml` | unchanged | — |

Secrets: `ANTHROPIC_API_KEY` (new, repo secret). The promotion workflow needs
`contents: write` and `pull-requests: write`; every other new workflow is
read-only. `weekly-review.yml` runs with a concurrency group so a manual run
can't race the cron.

`CONTRIBUTING.md` (new) documents the branch-and-PR flow, that promotion PRs are
machine-authored and reviewed like any other, and that `docs/tools.md` is
generated — edit the registry, not the doc.

---

## 11. Configuration

`config/models.yaml` — the whole routing policy in one file:

```yaml
tiers:
  execution:
    model: claude-haiku-4-5
    max_tokens: 4096
    thinking: {type: enabled, budget_tokens: 2048}   # Haiku: budget_tokens, never effort
    persona: config/prompts/haiku_execution.md
    tools: all_read_only
  exploration:
    model: claude-opus-5
    max_tokens: 32000
    thinking: {type: adaptive, display: summarized}
    output_config: {effort: high}                    # Opus: effort, never budget_tokens
    persona: config/prompts/opus_quant_philosopher.md
    tools: all
    sandbox: true
  middle:
    enabled: false
    model: claude-sonnet-5

routing:
  default_tier: execution
  complexity_token_threshold: 400
  exploration_keywords: [why, compare, design, hypothesis, novel, explain the interaction]
  sandbox_keywords: [simulate, write code, experiment, custom strategy]
  max_execution_symbols: 25

escalation:
  retry_on_same_tier: 1
  failure_streak_to_sticky: 3
  attach_failure_transcript: true

promotion:
  min_runs: 5
  min_success_rate: 0.8
  min_distinct_inputs: 3
  max_median_duration_ms: 5000
  max_prs_per_week: 3

sandbox:
  timeout_seconds: 30
  memory_mb: 512
  env_allowlist: [PATH, HOME, LANG]
```

Also: `config/stances.yaml` (§9), `config/prompts/*.md` (personas), and
`.env.example` gains `ANTHROPIC_API_KEY` and `SIGNALS_FORCE_TIER`.

Config is loaded once through `signals_app.config` alongside the existing
settings, validated at startup, and the loaded values are logged at `INFO` on
first agent call — a router whose thresholds you can't see in the logs is a
router you can't debug.

---

## 12. Repository map after the build

```
src/signals_app/
  service.py            ← unchanged seam; gains promoted functions over time
  agents/
    __init__.py
    tools.py            ← ONE registry → MCP registration + @beta_tool wrappers
    client.py           ← Anthropic client, per-tier request kwargs, caching
    session.py          ← tool_runner loop, transcript capture, usage accounting
  routing/
    router.py           ← pure: query → Route
  sandbox/
    runner.py           ← run_code, rlimits, env scrub, process-group kill
  experiments/
    store.py            ← JSONL append + queries
    promote.py          ← candidate → branch + PR
  culture/
    corpus.py  rhetoric.py  stances.py  bias.py
  mcp/server.py         ← now registers from agents/tools.py
config/
  models.yaml  stances.yaml  prompts/*.md
data/
  texts/                ← public-domain excerpts, front-matter sourced
  news/                 ← local JSONL corpus
logs/
  experiments/YYYY-MM.jsonl   promoted.json
sandbox/
  run_*.py (transient)  archive/  output/
docs/
  hybrid-claude-pipeline.md (this file)  tools.md (generated)  runbook-agents.md
```

`.gitignore` gains `sandbox/run_*.py` and `sandbox/output/`; `sandbox/archive/`
is committed, because a promotion PR that can't show the code it promoted is not
reviewable.

---

## 13. PR plan

Seven PRs. Each is independently mergeable, each ships tests, and no PR leaves
`main` in a state where CI is red or a feature is half-wired.

| PR | Title | Ships | Depends on |
|---|---|---|---|
| 1 | `feat: Anthropic provider + tool registry` | `agents/client.py`, `agents/tools.py`, MCP server registers from the registry, `ANTHROPIC_API_KEY` plumbing, layering test extension | — |
| 2 | `feat: two-tier router` | `routing/router.py`, `config/models.yaml`, personas, table-driven routing tests over ~30 sample queries | 1 |
| 3 | `feat: sandbox runner` | `sandbox/runner.py`, rlimits, env-scrub test, timeout test, process-group kill test, `run_experiment` tool | 1 |
| 4 | `feat: experiment log` | `experiments/store.py`, JSONL schema, normalized-hash logic + its tests, router and sandbox both logging | 2, 3 |
| 5 | `feat: promotion` | `experiments/promote.py`, generated tests/docs, `weekly-review.yml`, `logs/promoted.json`, an end-to-end test that promotes a fixture experiment into a temp worktree | 4 |
| 6 | `feat: cultural layer` | `culture/*`, `data/texts/`, `data/news/`, `config/stances.yaml`, the three new tools, gate integration for `detect_cognitive_bias` | 1 |
| 7 | `docs: runbook + CI wiring` | `docs/runbook-agents.md`, generated `docs/tools.md`, `docs.yml`, `agent-smoke.yml`, `CONTRIBUTING.md`, `ci.yml` extension | 1–6 |

PR 1 is the only one that must land before anything else is useful; 2/3 and 6
can proceed in parallel after it.

---

## 14. Acceptance criteria

Mapped from the brief, each one a test or a command rather than a claim.

| # | Criterion | How it's checked |
|---|---|---|
| 1 | Router picks the right tier | `tests/test_router.py` — a table of ~30 queries with expected tier and reason. Pure function, no API calls, runs in CI on every push. |
| 2 | Escalation works | `tests/test_router.py` — a fake session where Haiku returns `"escalate"` three times; assert one retry, then Opus, then sticky. |
| 3 | Tools callable from a real MCP client | `tests/test_mcp_server.py` (extended) — every registry entry appears in the server's tool list with a valid schema; plus a manual `signals mcp` + Claude Code check in the runbook. |
| 4 | Tool parity | `tests/test_layering.py` — the set of MCP tools equals the set of `@beta_tool` wrappers, both generated from the registry. |
| 5 | Sandbox executes and captures | `tests/test_sandbox.py` — a script that prints, one that writes JSON output, one that raises. |
| 6 | Sandbox enforces the timeout | `tests/test_sandbox.py` — `while True: pass` returns a timeout error in ≤ 32s and leaves no orphan process (assert on the process group). |
| 7 | Sandbox can't read secrets | `tests/test_sandbox.py` — a script dumping `os.environ` contains no key from `config.py`'s secret list. |
| 8 | Every run is logged | `tests/test_experiments.py` — success and failure both append; the normalized hash collapses parameter-only variants. |
| 9 | Promotion produces a real tool | `tests/test_promote.py` — promote a fixture experiment into a temp worktree; assert the service function imports, the tool registers, the generated test passes, and `logs/promoted.json` is updated. |
| 10 | Promotion opens a PR | `weekly-review.yml` on a seeded fixture log — asserted by a manual `workflow_dispatch` run in the runbook, since opening a PR in CI on every push is not something to automate into the test suite. |
| 11 | Actions are green | All five existing workflows plus the three new ones pass on the final PR. |
| 12 | Caching actually caches | `agent-smoke.yml` asserts `usage.cache_read_input_tokens > 0` on the second call. |
| 13 | Docs suffice for a new dev | `docs/runbook-agents.md` walked end-to-end on a clean clone: install → `.env` → one Haiku call → one Opus sandbox run → read the log. |

---

## 15. Cost model

Rough, and worth writing down because the whole two-tier design is a cost
argument.

A routine tool-shaped request on Haiku: ~6K cached input + 2K fresh + 500 output
≈ **$0.005**. The same request on Opus with thinking: ~8K input + 3K output ≈
**$0.11**. That is the ~20× gap the router exists to capture.

An exploration session — persona, tool list, a few sandbox round-trips, adaptive
thinking at `effort: high` — lands at $0.30–$1.50. At a handful of sessions a
week, exploration is tens of dollars a month, and it is the part that buys new
capability.

Two caveats: caches are model-scoped, so an escalated session pays the cache
write twice; and `effort: high` on Opus 5 is the default and is the right
setting for exploration — dropping it to `medium` for routine escalations is the
first lever to try if the bill runs hot, ahead of any model change.

The flywheel's actual return: every promotion moves one recurring computation
from the $0.11 column to the $0.005 column, permanently.

---

## 16. Risks and compliance

**The sandbox is not a jail on a laptop.** Stated plainly in §6, the runbook,
and the `run_experiment` docstring. The threat model is model mistakes, not
adversaries.

**Promotion must never self-merge.** The workflow has `pull-requests: write`,
not merge rights, and no auto-merge is enabled. A machine that widens its own
tool surface without review is the failure mode this whole design should avoid.

**`COMPLIANCE.md` §6 applies to every new tool.** The disclaimer wrapper is not
optional, and the philosophical and cultural tools need it *most*, because their
output reads like judgment. `assess_philosophical_stance` and
`analyze_text_for_market_parallels` both carry `heuristic: true` in-band.

**The cultural layer is unvalidated.** The rhetoric damping factor and the
stance deltas are priors, not findings. They ship behind an off-by-default flag
(`stance: none`), with the `absurdist` control arm, and the first backtest
pointed at them decides whether they stay. Say so in `docs/tools.md` rather than
letting a plausible-sounding table harden into an assumption.

**Prompt injection through `search_news`.** The local corpus is ours today, but
the moment it becomes a live feed, headline text is untrusted input reaching a
model that holds tools. Tool results get wrapped in an explicit
untrusted-content envelope from the start, so the boundary exists before it's
load-bearing.

**Cost runaway.** A stuck escalation loop on Opus is the expensive failure. Per-
session token budget in `config/models.yaml`, sticky escalation logged, and the
weekly review reports spend by tier alongside promotion candidates.

---

## 17. Runbook

```bash
mamba env create -f environment.yml    # or: mamba env update
mamba activate signals-app
pip install -e . --no-deps
pip install anthropic

cp .env.example .env                   # add ANTHROPIC_API_KEY
```

Then:

```bash
signals agent "RSI on SPY and NVDA over 6mo"      # → Haiku, tool call
signals agent --tier exploration "does the volume-spike detector \
  add anything on top of the MACD cross, or is it collinear?"  # → Opus, sandbox
signals experiments list --since 30d              # read the log
signals experiments candidates                    # what would be promoted
signals mcp                                       # stdio server for Claude Code
```

Known snag: `.mcp.json` runs the bare command `signals`, which resolves only
when the package is installed into the active environment. In a fresh
container — including the one this document was written in — the MCP server
fails to launch with `ENOENT: signals`. Either `pip install -e .` first, or
point `.mcp.json` at an absolute interpreter path. Worth fixing in PR 7 by
switching the command to `python -m signals_app.cli.main mcp`.

---

## Open questions

1. **Does the stance layer beat the `absurdist` control?** Unknown until PR 6
   ships and a backtest runs. If it doesn't, the honest move is to keep it as a
   labeled UI flavor and stop calling it risk management.
2. **Is `runs >= 5` the right promotion bar for this repo's traffic?** At a few
   exploration sessions a week, five runs of the same normalized code may take
   months. Revisit after four weeks of real log data; the gate is config.
3. **Does Haiku 4.5 hold up on tool selection across the 14 tools it can see?**
   (Nine existing read-only tools plus five of the seven new ones —
   `run_experiment` and `log_experiment_result` are Opus-only.) If selection
   accuracy degrades, the fix is tool search (`defer_loading`) before it is a
   model upgrade.
4. **When does the MCP connector path become worth it?** The moment
   `signals mcp --http` gets a public URL, option A in §4 becomes available and
   the tool registry works unchanged. Not before.
```
