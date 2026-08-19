---
Created: 2026-08-18
Status: living plan — supersedes the "Postgres (Neon)" data-layer section of
        docs/signals-app-architecture.md, which was written before Supabase
        was chosen and before GitHub Pages became the deploy target.
---

# Backend State & Implementation Plan: GitHub Actions + Supabase

Two parts, matching the ask:

- **[Part 1](#part-1--current-backend-state)** — what the backend actually is
  today, verified against the code, not the older design docs.
- **[Part 2](#part-2--the-target-architecture)** — how to implement the idea
  fully on GitHub Actions + Supabase, with a build order.
- **[Part 3](#part-3--the-automated-signals-engine)** — the automated signals
  engine and what "high quality output" has to mean concretely for it.

---

## Part 1 — Current Backend State

### The one-line summary

**The backend is a complete, well-factored FastAPI application that is not
deployed anywhere and has no scheduled execution.** Everything below the API
boundary (L1–L5 of the layered architecture) is built and tested. Everything
at and above the API boundary — hosting, persistence that survives a process
restart, scheduling, and the frontend's access to real data — is missing.

The public site at `adamaslan.github.io/signals-app` is a functional shell:
it loads, and every signal fetch fails, because `NEXT_PUBLIC_API_URL` has no
target.

### What exists — verified layer by layer

| Layer | Module | LOC | State |
|---|---|---|---|
| L1 Data | [`data/fetcher.py`](../src/signals_app/data/fetcher.py) | 312 | ✅ yfinance + retry/backoff + **in-process dict cache** |
| L2 Indicators | [`indicators/`](../src/signals_app/indicators/) | 1,038 | ✅ vectorized compute, param grids, pivots, RSI/MACD divergence, data-quality scoring |
| L3 Detection | [`detection/`](../src/signals_app/detection/) | 1,425 | ✅ 18 detectors, per-detector timeout isolation, `degraded` flag, historical bar-by-bar scan |
| L4 Scoring | [`scoring/`](../src/signals_app/scoring/) | 973 | ✅ ConfluenceRanker, multi-timeframe composite, relative strength, hit-rate calibration |
| L5 Synthesis | [`synthesis/mtf_llm.py`](../src/signals_app/synthesis/mtf_llm.py) | 491 | ✅ OpenRouter → Gemini → rule-based fallback chain, circuit breaker |
| Schema | [`schemas/signal_output.py`](../src/signals_app/schemas/signal_output.py) | 266 | ✅ Pydantic contract; evidence weights sum to 1.0, counter-evidence required above 0.6 confidence |
| Backtest | [`backtests/engine.py`](../backtests/engine.py) | 127 | ✅ forward-return hit-rate buckets by category and strength |
| L6 API | [`api/routes.py`](../src/signals_app/api/routes.py) | 352 | ✅ code exists — ❌ not hosted |
| Persistence | [`db/`](../src/signals_app/db/) | 236 | ⚠️ SQLAlchemy async, **one table**, SQLite by default |
| Frontend | [`web/`](../web/) | Next 15 static export | ✅ deployed to Pages — ❌ no backend to call |

That is roughly **5,200 lines of working engine** with no way to run on a
schedule and nowhere to put the results.

### The three API endpoints

| Route | Does | Blocking problem |
|---|---|---|
| `GET /signals/{symbol}` | Full L1→L5 pipeline, one symbol, one period, synchronous | Cold-path latency is a full yfinance fetch + 18 detectors + an LLM call. Not viable as a user-facing request against a cold serverless host. |
| `GET /history/{symbol}` | Reads persisted runs from `signal_runs` | Table stores *metadata only* — direction, confidence, flags. **The signal itself is never persisted.** |
| `GET /backtest/{symbol}` | Historical scan + hit-rate buckets | Scans thousands of bars synchronously inside a request handler. This is a batch job wearing an HTTP costume. |

### What the persistence layer actually stores

One table, [`db/models.py`](../src/signals_app/db/models.py):

```
signal_runs: id, ticker, period, resolved_period, direction,
             confidence, ai_degraded, no_llm, prompt_version, ts
```

Ten columns of *provenance about* a signal. The `SignalOutput` — the
evidence list, the counter-evidence, the confluence score, the reasons, the
data-quality score — is computed, returned once over HTTP, and discarded.

The consequences compound:

1. **No signal history.** `/history/{symbol}` can tell you a BUY happened on
   Tuesday. It cannot tell you *why*, so nothing can review a past call.
2. **No accumulating backtest corpus.** Every calibration run re-fetches and
   re-scans from yfinance. The historical scan is thrown away each time.
3. **The calibration loop is manual and ephemeral.** `scripts/calibrate.py`
   writes `calibration/strength_hit_rates.json` to local disk — a path that
   does not survive a container restart, and is `.gitignore`-adjacent
   (`output/` is ignored; `calibration/` is untracked and empty).

### The frontend stores more than the backend does

[`web/src/lib/db.ts`](../web/src/lib/db.ts) is a Dexie/IndexedDB store with
`Profile`, `HistoryEntry`, and `WatchItem` tables. Watchlists, user
preferences, and run history live **on one device, in one browser**. Clear
site data and it is gone. There is no account, no sync, no server-side
notion of a user.

This is the single largest architectural gap: **the app's user state has no
server.** Supabase closes exactly this gap.

### CI/CD as it stands

One workflow, [`deploy-pages.yml`](../.github/workflows/deploy-pages.yml):
build `web/` on push → static export → GitHub Pages.

Verified against the live repo (`gh variable list`, `gh secret list`):
**zero repository variables and zero secrets are configured.** So even the
`NEXT_PUBLIC_API_URL` the workflow reads is unset. There is:

- ❌ no test workflow — `pytest` never runs in CI
- ❌ no lint/typecheck workflow — `ruff` and `mypy` are configured in
  `pyproject.toml` and never invoked
- ❌ no scheduled workflow of any kind
- ❌ no backend deploy

### Known defects that block the plan

These are real and must be fixed early, in roughly this order:

1. **`pyproject.toml` cannot build.** `build-backend =
   "setuptools.backends.legacy:build"` is not a real backend; the correct
   value is `setuptools.build_meta`. Consequence: `pip install -e .` fails,
   so `import signals_app` only works via `PYTHONPATH` gymnastics. **This
   blocks every CI job that needs to import the package** — it is the first
   thing to fix. (Diagnosed in [`nu1.md`](../nu1.md).)
2. **`aiosqlite` missing from `environment.yml`** though `db/session.py`
   requires it — a fresh `mamba env create` produces an env that cannot open
   its own default database.
3. **`SignalOutput.matrix` is always `None`.** `compute_multi_timeframe()`
   and `build_timeframe_matrix()` are fully implemented and never called
   from a route. The `CouncilPanel` and `SignalMatrixRow` components have no
   data source.
4. **`web/src/lib/types.ts` is a hand-copied mirror** of the Pydantic schema
   with nothing enforcing agreement. A backend rename surfaces as a runtime
   render failure in the browser.
5. **Deprecated FastAPI lifecycle.** `@app.on_event("startup")` /
   `("shutdown")` in [`api/main.py`](../src/signals_app/api/main.py) should
   be a `lifespan` context manager.
6. **CORS is `allow_origins=["*"]` with `allow_credentials=True`** — an
   invalid combination browsers reject, and wrong regardless once auth
   exists.

### Why the current shape resists deployment

Worth stating plainly, because it drives the whole Part 2 design:

- **Synchronous LLM inside a request handler.** A cold container plus
  yfinance plus an LLM call is a multi-second p99. Cold-start hosting and
  per-request LLM calls are a bad pairing.
- **The cache is process memory.** `_MEM_CACHE` in `fetcher.py` is a
  module-level dict. Every new container starts cold; every scale-out event
  multiplies yfinance load. yfinance rate-limits.
- **yfinance from a datacenter IP is unreliable.** Fine from a laptop,
  routinely throttled from cloud egress. Any always-on hosted backend
  inherits this as a permanent flakiness source.

All three problems dissolve under the same move: **stop computing signals
during requests.** Compute them on a schedule, write the results down, and
serve reads from a database. That is what Part 2 does.

---

## Part 2 — The Target Architecture

### The core decision: precompute, don't serve-compute

```
BEFORE (today)                         AFTER (target)
──────────────                         ──────────────
browser                                browser
   │  GET /signals/AAPL                    │  supabase-js select
   ▼                                       ▼
FastAPI (nowhere)                      Supabase Postgres  ◄── read-only,
   │  yfinance → 18 detectors              │                  <50ms, RLS
   │  → LLM → discard                      │
   ▼                                       ▲  write
one JSON response                      GitHub Actions (cron)
                                          engine runs on a schedule
```

The FastAPI app stops being the production entry point. It stays as the local
dev server, the OpenAPI contract, and the thing the batch job imports. The
**GitHub Actions runner becomes the compute layer** and **Supabase becomes
both the database and the read API**.

Why this fits unusually well here:

- The engine is already a pure function of `(symbol, period) → SignalOutput`.
  Nothing about it needs to be a web server.
- Actions runners have residential-ish egress and generous CPU — better for
  yfinance and 18-detector scans than a cold serverless container.
- Supabase's auto-generated REST/realtime API over Postgres means the read
  path needs **no backend code at all**.
- Cost: within free tiers on both sides for this workload.

Trade-off, stated honestly: signals become **as fresh as the last cron run**,
not as fresh as the request. For a swing-trading signal engine on daily bars
this is correct — the current design recomputes a daily-bar signal on every
page view, which is waste, not freshness. An on-demand path for uncovered
tickers is specified in Phase 5 below.

### Universe seed

[`seed/universe_symbols.csv`](../seed/universe_symbols.csv) — 954 active
tickers (776 equities, 173 ETFs, 4 crypto, 1 fund), extracted from
`nuwrrrld-portal/docs/universe-by-industry.md`'s Appendix A.4 alphabetical
index (981 registered rows minus 27 delisted/no-metadata entries). Columns:
`ticker,name,asset_type,sector_group`. This is the seed for the `symbols`
table below — load it once via `scripts/seed_universe.py` (Phase 4/9), not
per-run. Treat `nuwrrrld-portal`'s doc as the source of truth if the two
drift; re-run the extraction rather than hand-editing the CSV.

### Supabase schema

Eight tables. This is the design the architecture doc gestured at, made
concrete and mapped onto what the code actually produces.

```sql
-- ── Universe & runs ────────────────────────────────────────────────
create table symbols (
  ticker        text primary key,
  name          text,
  asset_type    text not null default 'equity',
  active        boolean not null default true,
  priority      int not null default 100,  -- scan order; low = first
  added_at      timestamptz not null default now()
);

create table engine_runs (
  id            bigserial primary key,
  started_at    timestamptz not null default now(),
  finished_at   timestamptz,
  trigger       text not null,             -- 'cron' | 'manual' | 'backfill'
  code_version  text not null,             -- SIGNALS_APP_CODE_VERSION
  git_sha       text not null,
  symbols_total int not null default 0,
  symbols_ok    int not null default 0,
  symbols_failed int not null default 0,
  llm_provider  text,
  status        text not null default 'running',  -- running|ok|partial|failed
  error         text
);

-- ── The thing that is currently thrown away ────────────────────────
create table signals (
  id                  bigserial primary key,
  run_id              bigint not null references engine_runs(id) on delete cascade,
  ticker              text not null references symbols(ticker),
  period              text not null,
  bar_ts              timestamptz not null,   -- the bar this describes
  direction           text,                   -- BUY|SELL|HOLD
  confidence          double precision,
  confluence_score    double precision,
  bias                text,
  bull_count          int,
  bear_count          int,
  total_signals       int,
  data_quality_score  double precision,
  data_quality_reasons jsonb not null default '[]',
  evidence            jsonb not null default '[]',  -- full evidence[]
  counter_evidence    jsonb not null default '[]',
  matrix              jsonb,                  -- TimeframeMatrix when computed
  ai_degraded         boolean not null default false,
  no_llm              boolean not null default false,
  prompt_version      text,
  llm_model           text,
  code_version        text not null,
  created_at          timestamptz not null default now(),
  unique (ticker, period, bar_ts, code_version)
);
create index on signals (ticker, period, bar_ts desc);
create index on signals (created_at desc);

-- ── Raw detector output, for mining and calibration ────────────────
create table detector_hits (
  id           bigserial primary key,
  ticker       text not null references symbols(ticker),
  bar_ts       timestamptz not null,
  detector     text not null,
  category     text not null,
  strength     text not null,
  description  text,
  code_version text not null,
  unique (ticker, bar_ts, detector, description, code_version)
);
create index on detector_hits (ticker, bar_ts desc);
create index on detector_hits (strength, category);

-- ── Validation loop ────────────────────────────────────────────────
create table forward_returns (
  ticker       text not null references symbols(ticker),
  bar_ts       timestamptz not null,
  horizon_days int not null,
  pct_return   double precision not null,
  computed_at  timestamptz not null default now(),
  primary key (ticker, bar_ts, horizon_days)
);

create table calibration (
  id            bigserial primary key,
  computed_at   timestamptz not null default now(),
  code_version  text not null,
  horizon_days  int not null,
  bucket_kind   text not null,   -- 'strength' | 'category' | 'confluence_band'
  bucket_key    text not null,
  hits          int not null,
  total         int not null,
  hit_rate      double precision not null,
  is_active     boolean not null default false
);
create index on calibration (is_active, bucket_kind);

-- ── User state (currently trapped in IndexedDB) ────────────────────
create table profiles (
  id             uuid primary key references auth.users(id) on delete cascade,
  display_name   text,
  default_period text not null default '3mo',
  theme          text not null default 'dark',
  created_at     timestamptz not null default now()
);

create table watchlist (
  user_id  uuid not null references auth.users(id) on delete cascade,
  ticker   text not null references symbols(ticker),
  note     text,
  added_at timestamptz not null default now(),
  primary key (user_id, ticker)
);
```

**Row-Level Security** — the read path is public, so this is the only thing
standing between the data and the internet:

```sql
alter table signals        enable row level security;
alter table symbols        enable row level security;
alter table calibration    enable row level security;
alter table engine_runs    enable row level security;
alter table profiles       enable row level security;
alter table watchlist      enable row level security;

-- Public read on engine output
create policy "public read"  on signals     for select using (true);
create policy "public read"  on symbols     for select using (true);
create policy "public read"  on calibration for select using (is_active);
create policy "public read"  on engine_runs for select using (true);

-- Per-user rows, owner-only
create policy "own profile"   on profiles
  for all using (auth.uid() = id) with check (auth.uid() = id);
create policy "own watchlist" on watchlist
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
```

No `insert`/`update` policy exists for `signals` — writes happen only through
the **service-role key**, which lives in GitHub Actions secrets and bypasses
RLS. The browser's anon key can never write engine output. `detector_hits`
and `forward_returns` get no policies and no grants: internal tables,
service-role only.

### The workflows

Five, replacing the current one.

#### 1. `ci.yml` — the missing foundation

```yaml
on: [push, pull_request]
```

Runs `ruff check`, `mypy src/`, and `pytest`. Requires fixing the
`build-backend` typo first (defect 1) so `pip install -e .` works. This does
not exist today and should be the very first PR — everything after it is
riskier without a green test signal.

#### 2. `signals-scan.yml` — the engine

```yaml
on:
  schedule:
    - cron: '25 21 * * 1-5'   # 21:25 UTC ≈ 16:25 ET, ~10 min after US close
  workflow_dispatch:
    inputs:
      symbols: { description: 'Space-separated tickers (blank = full universe)' }
```

Runs `scripts/scan_universe.py` (new — see Part 3). Reads the active universe
from `symbols`, runs the pipeline per ticker, writes `signals`,
`detector_hits`, and one `engine_runs` row.

Key operational requirements, all of which matter more than they look:

- **Concurrency guard** — `concurrency: { group: signals-scan,
  cancel-in-progress: false }`. Two overlapping scans double-write and
  double-spend LLM tokens.
- **Matrix shard the universe** once it exceeds ~50 tickers.
  `strategy.matrix.shard: [0,1,2,3]` with `fail-fast: false`, each shard
  taking `ticker_index % 4 == shard`. Keeps wall-clock under the 6h job cap
  with headroom and isolates failures.
- **Cache the mamba env** with `mamba-org/setup-micromamba@v2` +
  `cache-environment: true`. Env creation is ~2 min uncached, seconds cached.
- **Never fail the whole run on one bad ticker.** Per-symbol try/except,
  tally into `symbols_failed`, exit non-zero only if the failure *rate*
  exceeds a threshold (start at 20%).
- **`if: github.event_name != 'schedule' || github.repository ==
  'adamaslan/signals-app'`** on the job, so forks don't inherit the cron.

Scheduled workflows are disabled after 60 days of repo inactivity — worth
knowing before wondering why signals went stale.

#### 3. `calibrate.yml` — the validation loop

```yaml
on:
  schedule:
    - cron: '0 6 * * 6'   # Saturday 06:00 UTC — markets closed
  workflow_dispatch:
```

1. Compute `forward_returns` for every bar now old enough to have a realized
   `horizon_days` return.
2. Join `detector_hits` against `forward_returns` → hit-rate per strength,
   per category, per confluence band.
3. Insert a new `calibration` generation; flip `is_active` in a transaction
   so readers never see a partial table.

This finally closes the loop the code was built for: `ConfluenceRanker`
already accepts `strength_hit_rates`, `scripts/calibrate.py` already computes
them, and the live path already calls `load_strength_hit_rates()`. Today that
loads a JSON file that does not persist. **Point it at the `calibration`
table and the loop closes** — that is a small change with disproportionate
payoff.

#### 4. `backfill.yml` — bootstrapping history

`workflow_dispatch` only, inputs `symbols` and `period` (default `5y`).
Runs the historical scan and writes every bar's detector hits into
`detector_hits`. Without this, calibration has to wait months to accumulate
enough samples to clear `CALIBRATION_MIN_BUCKET_SIZE = 30`. With it, useful
calibration exists on day one.

#### 5. `deploy-pages.yml` — mostly as-is

Keep it, plus: set `NEXT_PUBLIC_SUPABASE_URL` and
`NEXT_PUBLIC_SUPABASE_ANON_KEY` as repo **variables** (they are public by
design — the anon key is safe in a client bundle *because* RLS is on; that
dependency is worth being deliberate about).

### Secrets

| Name | Kind | Used by | Notes |
|---|---|---|---|
| `SUPABASE_URL` | secret | scan, calibrate, backfill | |
| `SUPABASE_SERVICE_ROLE_KEY` | secret | scan, calibrate, backfill | **Bypasses RLS.** Never in a client bundle, never in a PR-triggered workflow. |
| `OPENROUTER_API_KEY` | secret | scan | Existing provider preference; takes priority over Gemini. |
| `NEXT_PUBLIC_SUPABASE_URL` | variable | deploy-pages | |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | variable | deploy-pages | Public by design; RLS is the actual control. |

Set these with the `secrets-sync` skill rather than pasting values into a
chat session.

### Frontend changes

The static export stays — this design is *better* suited to it, because reads
go straight from the browser to Supabase with no backend hop.

1. Add `@supabase/supabase-js`; create a client from the two public env vars.
2. Replace `fetchSignal()` in [`web/src/lib/api.ts`](../web/src/lib/api.ts)
   with a `signals` select — latest row per ticker/period.
3. **Keep Dexie**, repurposed as an offline cache rather than the system of
   record. Signed-in users sync `watchlist` and `profiles` to Supabase;
   signed-out users keep working exactly as today. This is a strict
   improvement with no regression for anonymous use.
4. Generate `types.ts` from the Supabase schema (`supabase gen types
   typescript`) — retiring defect 4 for the DB-shaped half of the types.

### Build order

Each phase is independently shippable and leaves the repo working.

| # | Phase | Delivers | Depends on |
|---|---|---|---|
| **0** | **Repo hygiene** — fix `build-backend`, add `aiosqlite`, `.nvmrc`, `lifespan`, CORS | `pip install -e .` works | — |
| **1** | **`ci.yml`** — ruff + mypy + pytest | Green baseline; every later phase is verified | 0 |
| **2** | **Supabase project + schema + RLS** | Tables live, policies proven with anon-key probes | — |
| **3** | **`db/supabase.py` writer + `scripts/scan_universe.py`** | Engine can persist a full `SignalOutput` | 0,2 |
| **4** | **`signals-scan.yml`** on 5 tickers | First automated signals in the DB | 3 |
| **5** | **Frontend reads Supabase** | Deployed site shows real data — *the app works end to end for the first time* | 2,4 |
| **6** | **`backfill.yml`** + `detector_hits` | Historical corpus for calibration | 3 |
| **7** | **`calibrate.yml`** + read `calibration` from DB | Closed validation loop | 6 |
| **8** | **Supabase Auth + `watchlist`/`profiles` sync** | User state survives a browser wipe | 2,5 |
| **9** | **Scale to the full 954-ticker seed, shard the matrix** | Real coverage | 4 |
| **10** | **Wire `matrix` (multi-timeframe)** | `CouncilPanel`/`SignalMatrixRow` get data; defect 3 closed | 4 |

Phase 5 is the milestone worth aiming at — it is the first point where the
deployed thing is genuinely functional rather than a shell.

### What happens to FastAPI

It stays, with a narrowed job:

- local dev server (`scripts/run_local.sh`, `dev.sh`)
- the OpenAPI contract and `/docs`
- the importable pipeline that `scan_universe.py` calls

Optionally deploy it later behind an on-demand path for tickers outside the
scanned universe: browser → Supabase Edge Function → FastAPI → write to
`signals` → browser reads the row. Not needed before Phase 9, and possibly
never.

---

## Part 3 — The Automated Signals Engine

Step 2 of the ask: an automated engine producing **high quality output**.
The scheduling is the easy half; "high quality" is the half that needs
defining, because an engine that emits a confident BUY on every ticker every
day is automated and worthless.

### What "high quality" has to mean, concretely

Six properties, each with an enforcement mechanism in the code rather than in
a doc.

#### 1. Calibrated — a stated confidence matches the observed hit rate

The one that matters most, and the one the codebase is closest to. When the
engine says HIGH confidence, HIGH-confidence calls should resolve favorably
measurably more often than MEDIUM ones. All the parts exist
(`backtests/engine.py`, `scoring/calibration.py`, the `strength_hit_rates`
parameter); what is missing is durable storage and a schedule, which
Phases 6–7 supply.

**Enforcement:** the weekly calibration job publishes a reliability table
(predicted band vs. realized hit-rate). If HIGH does not outperform MEDIUM
over a full generation, that is a defect, and the job should say so loudly
rather than silently republishing.

#### 2. Falsifiable — every signal is scored against reality

Every row in `signals` gets its `forward_returns` computed once the horizon
has elapsed. No signal escapes grading. This is what turns the app from a
signal *generator* into a signal *engine* — the difference is whether the
output is ever checked.

#### 3. Selective — most days, most tickers produce nothing

The current pipeline always emits a direction. That is an artifact of
`_fallback_signal()` guaranteeing a result, and it is the fastest way to
destroy output quality: a BUY on every ticker every day carries zero
information.

**Enforcement:** a publication gate before writing to `signals`, all
thresholds named constants in `config.py`:

```
publish only if:
  data_quality_score >= PUBLISH_MIN_DATA_QUALITY   (0.7)
  and total_signals  >= PUBLISH_MIN_SIGNALS        (3)
  and abs(confluence_score) >= PUBLISH_MIN_SCORE   (0.35 — reuse the
                                                    existing BUY/SELL bands)
  and not ai_degraded  (or: publish, but never above MEDIUM confidence)
```

Everything else is stored as HOLD or not stored at all. Expect the gate to
reject the large majority of ticker-days. That is the point — the engine's
value is in what it declines to say.

#### 4. Explainable — the reasoning is persisted, not just the verdict

The `Signal` schema already enforces the hard part: evidence weights sum to
1.0, counter-evidence is mandatory above 0.6 confidence. That discipline is
currently computed and thrown away. Persisting `evidence` and
`counter_evidence` as `jsonb` means a call from three months ago can be
re-read and judged — which is what makes #2 meaningful rather than a number.

#### 5. Provenance-stamped — every signal knows what produced it

`code_version`, `prompt_version`, `llm_model`, `git_sha`, `run_id`. Without
these, a change in hit-rate is unattributable: was it the market, or the
detector edit from last Tuesday? The `unique (ticker, period, bar_ts,
code_version)` constraint means a logic change produces a *new* signal rather
than silently overwriting history — the schema enforces the discipline.

#### 6. Fail-loud, never fail-silent

The existing degradation chain (OpenRouter → Gemini → rule-based) is good
engineering and a quality hazard: it can quietly produce an entire day of
rule-based output that looks identical to LLM-synthesized output in the UI.

**Enforcement:** `ai_degraded` propagates to the DB and is *rendered* in the
UI, not just stored. Every run writes an `engine_runs` row; a run that is
`partial` or `failed` is visible. If yfinance throttles half the universe,
the site should say so.

### `scripts/scan_universe.py`

The new entry point. Sketch:

```python
def scan_universe(
    symbols: Sequence[str],
    period: str,
    writer: SignalWriter,          # injected — Supabase or a local stub
    gate: PublicationGate,         # injected — the #3 thresholds
) -> RunSummary:
    """Run the pipeline over a universe and persist gated results."""
```

Design notes that follow from the codebase's own conventions:

- **Dependency-injected writer** so tests run against an in-memory fake and
  local dev can write SQLite — matching the DI pattern the rest of the code
  already uses.
- **Per-symbol isolation.** One bad ticker is a tallied failure, never an
  aborted run — the same discipline `detect_all_signals()` already applies to
  detectors, applied one level up.
- **Bounded concurrency.** yfinance throttles; a semaphore around fetches
  (start at 4) with the existing `FETCH_BACKOFF_*` backoff.
- **Batch the writes.** One upsert per table per shard, not per ticker.
- **Idempotent.** The unique constraint makes a re-run of the same day a
  no-op, so a retried workflow is safe.

### Cost and rate-limit envelope

Worth sizing before scaling the universe, since both are real limits:

| Universe | LLM calls/day | Actions minutes/day | Notes |
|---|---|---|---|
| 5 (Phase 4) | 5 | ~3 | Trivial. |
| 50 | ~50 | ~15 | Comfortable on free tiers. |
| 954 (Phase 9, full seed) | ~954 pre-gate | ~90, sharded 4-way | Needs the gate to run *before* the LLM call, not after. |

An important ordering consequence: **run the publication gate before LLM
synthesis, not after.** If confluence is below threshold, the signal is not
going to be published — so paying for an LLM call to synthesize it is pure
waste. At 500 tickers that inverts the cost profile entirely. This also means
`scan_universe.py` should call the pipeline layer-by-layer rather than
reusing the `/signals/{symbol}` route handler, which always synthesizes.

### Definition of done for Step 2

The engine is complete when:

- [ ] A cron scan runs unattended for 30 consecutive days with no manual
      intervention.
- [ ] Every published signal has a realized forward return recorded.
- [ ] The calibration table shows HIGH confidence outperforming MEDIUM,
      with bucket sizes above `CALIBRATION_MIN_BUCKET_SIZE`.
- [ ] The publication gate rejects the majority of ticker-days.
- [ ] A degraded or partial run is visible in the UI, not just in logs.
- [ ] Deleting all browser data loses nothing but local cache.

---

## Appendix — verification commands

```bash
# Confirm the build-backend defect (defect 1)
grep build-backend pyproject.toml

# Confirm aiosqlite is missing from the env spec (defect 2)
grep aiosqlite environment.yml || echo "MISSING"

# Confirm matrix is never populated (defect 3)
grep -rn "build_timeframe_matrix\|compute_multi_timeframe" src/signals_app/api/

# Confirm no CI secrets/variables are configured
gh variable list && gh secret list

# Run the suite before pyproject is fixed
PYTHONPATH="$PWD/src:$PWD" python -m pytest -q
```

## Related docs

- [`signals-app-architecture.md`](signals-app-architecture.md) — original
  layered design. Still accurate for L1–L5; its Neon/Postgres data-layer
  section is superseded here.
- [`signal-multiplication-analysis.md`](signal-multiplication-analysis.md) —
  why the detector breadth looks the way it does.
- [`signal-robustness-status.md`](signal-robustness-status.md) — progress
  against the 7 robustness pillars.
- [`../wiki/ops/known-issues.md`](../wiki/ops/known-issues.md) — the live
  defect list; defects 1–6 above should be reconciled into it.
- [`../nu1.md`](../nu1.md) — the session that diagnosed defect 1.
