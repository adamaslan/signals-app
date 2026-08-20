# signals-app — Application Overview

**Written:** 2026-08-20 · **State:** all 11 planned phases shipped (PRs #7–#17)

A technical-analysis signal engine. It scans a 954-ticker universe on a schedule,
runs 18 independent detectors over each ticker, scores their agreement, **rejects
most of them**, and only then pays an LLM to write up the survivors. Results land
in Supabase and are served by a static Next.js dashboard on GitHub Pages.

The one design idea worth understanding before anything else: **the publication
gate runs before LLM synthesis.** Everything about the cost profile, the module
boundaries, and the workflow layout follows from that ordering.

---

## Outline

1. [What it does](#1-what-it-does)
2. [The pipeline, layer by layer](#2-the-pipeline-layer-by-layer)
3. [The publication gate — the load-bearing idea](#3-the-publication-gate--the-load-bearing-idea)
4. [Repository map](#4-repository-map)
5. [Data model](#5-data-model)
6. [Automation — the four workflows](#6-automation--the-four-workflows)
7. [The frontend](#7-the-frontend)
8. [The feedback loop: calibration](#8-the-feedback-loop-calibration)
9. [Configuration and secrets](#9-configuration-and-secrets)
10. [Running it locally](#10-running-it-locally)
11. [Testing](#11-testing)
12. [Design decisions worth knowing](#12-design-decisions-worth-knowing)
13. [Current state and known gaps](#13-current-state-and-known-gaps)

---

## 1. What it does

| | |
|---|---|
| **Universe** | 954 tickers — equities, ETFs, leveraged/inverse, a little crypto |
| **Cadence** | Weekdays 21:25 UTC (after US close); calibration Saturdays 06:00 UTC |
| **Detectors** | 18, across trend / momentum / volume / price-action |
| **Total potential signals per ticker** | up to 166 distinct signal firings in one scan pass — 18 detectors, several sweeping parameter grids (e.g. `BBExpansionDetector` alone covers 4 BB periods × 4 std-devs × 3 checks = 48) |
| **Total potential signals per scan** | up to 158,364 — 166 × 954 tickers (theoretical ceiling; most detectors fire far fewer per ticker on any given day) |
| **Publication rate** | ~42 % of scanned tickers ([measured](universe-scan-findings.md)) |
| **LLM** | OpenRouter (`google/gemini-2.0-flash-001`), Gemini fallback, or none |
| **Storage** | Supabase Postgres, 8 tables, RLS on user data |
| **Frontend** | Next.js 15 static export → GitHub Pages at `/signals-app` |
| **Backend size** | ~6,700 lines of Python across 37 modules |

---

## 2. The pipeline, layer by layer

The layers are called individually by `scripts/scan_universe.py` rather than
through the API's `get_signals()`, specifically so the gate can be inserted
between L4 and L5.

```
L1  fetch          data/fetcher.py         yfinance OHLCV, 3mo default
     ↓                                     (<20 bars → insufficient_bars, tallied)
L2  indicators     indicators/compute.py   RSI, MACD, ADX, ATR, Bollinger,
     ↓                                     Ichimoku, Stochastic, OBV/CMF, pivots
     │             indicators/data_quality.py → score 0..1 (staleness, gaps)
     ↓
L3  detect         detection/orchestrator.py  18 detectors, each isolated:
     ↓                                        one failure ⇒ degraded flag, not a crash
L4  score          scoring/confluence.py   strength-weighted bull/bear vote
     ↓                                     → score, bias, action (BUY/SELL/HOLD)
     │
     ╞═══ PUBLICATION GATE ═══  ~58 % exit here, having cost $0 in LLM spend
     │
L5  synthesize     synthesis/mtf_llm.py    LLM writes direction, confidence,
     ↓                                     evidence + counter-evidence
L6  persist        db/supabase.py          signals, detector_hits, engine_runs
```

**Failure isolation is applied at two levels.** `detect_all_signals()` isolates
individual detectors within a ticker; `scan_one_symbol()` applies the same
discipline one level up, isolating tickers within a run. It never raises — every
failure comes back as a `SymbolResult` so the run tallies instead of aborting.

**Optional L5b — the multi-timeframe matrix** (`--matrix`, Phase 10): re-runs
L1–L4 across 5 timeframes (1D/5D/1M/3M/6M) and makes one LLM call per timeframe.
Up to 5x the cost per gated symbol, so it is opt-in and deliberately blocked on
the full-universe workflow path.

---

## 3. The publication gate — the load-bearing idea

```python
# scan_universe.py
def passes_publication_gate(data_quality_score, total_signals,
                            confluence_score, ai_degraded) -> bool:
    if data_quality_score is None or data_quality_score < 0.7:   # PUBLISH_MIN_DATA_QUALITY
        return False
    if total_signals < 3:                                        # PUBLISH_MIN_SIGNALS
        return False
    if abs(confluence_score) < 0.35:                             # PUBLISH_MIN_CONFLUENCE_SCORE
        return False
    return True
```

Three properties matter:

1. **It runs before synthesis.** A rejected symbol costs a fetch and some pandas,
   not an API call. This is why a 954-ticker run is ~403 LLM calls, not 954.
2. **`abs()` makes it direction-neutral.** A strong bearish reading publishes as
   readily as a strong bullish one. The gate suppresses *weak* opinions, not
   negative ones.
3. **Rejecting most input is the product.** Per the plan: *an engine that always
   emits a direction carries no information.* The ~58 % rejection rate is a
   feature; a rate near 0 % would mean the gate had broken.

`PUBLISH_MIN_CONFLUENCE_SCORE` deliberately reuses `CONFLUENCE_BUY_THRESHOLD`
(0.35) rather than introducing a fourth tunable — anything in HOLD territory
isn't worth persisting.

---

## 4. Repository map

```
src/signals_app/
  config.py          all constants + Settings; the single source of tunables
  data/fetcher.py    yfinance wrapper → OHLCV
  indicators/        compute, data_quality, divergence, grids, pivots
  detection/         base (ABC) + trend, momentum, volume, historical
                     orchestrator.py registers all 18 detectors
  scoring/           confluence (the vote), mtf (multi-timeframe),
                     calibration (hit-rate feedback), relative_strength
  synthesis/mtf_llm.py   OpenRouter/Gemini calls, circuit breaker, degraded mode
  db/                supabase (writer + records), models, ops, session,
                     calibration_store
  api/               FastAPI app + routes (local/dev serving)
  schemas/           Pydantic output contracts
  utils/safety.py

scripts/scan_universe.py   the production entry point Actions calls
seed/universe_symbols.csv  954 tickers: ticker, name, asset_type, sector_group
supabase/migrations/       initial schema, RLS policies
tests/                     72 tests, 6 files
web/                       Next.js 15 static export
```

---

## 5. Data model

Eight tables in Supabase Postgres:

| Table | Holds | Notes |
|---|---|---|
| `symbols` | the ticker universe | FK target for `signals` + `detector_hits`; `ensure_symbol()` upserts before writes |
| `signals` | published signals | the gated output — direction, confidence, evidence, matrix |
| `detector_hits` | every detector firing | written for **all** scanned symbols, not just published ones |
| `engine_runs` | one row per scan | trigger, git SHA, counts, `llm_provider`, status |
| `forward_returns` | realized returns | the calibration input |
| `calibration` | strength → hit rate | generation-versioned; feeds back into scoring |
| `profiles` | user profiles | RLS-protected |
| `watchlist` | per-user watchlists | RLS-protected |

The `detector_hits` / `signals` split is the useful asymmetry: hits are recorded
for everything so calibration can later ask *"how did signals we rejected
actually perform?"*, while `signals` holds only what cleared the gate.

`engine_runs.status` is `"ok"` at zero failures, `"partial"` under 20 %,
`"failed"` above. With 4 permanently-dead tickers in the seed, real runs land on
`"partial"` — see the findings doc.

---

## 6. Automation — the four workflows

| Workflow | Trigger | Does |
|---|---|---|
| `signals-scan.yml` | cron `25 21 * * 1-5` + dispatch | The scan. Pilot job (5 tickers, 30 min cap) or full universe sharded `[0,1,2,3]` (90 min cap) |
| `calibrate.yml` | cron `0 6 * * 6` | Weekly. Joins forward returns → new calibration generation |
| `backfill.yml` | dispatch only | Historical signals for a ticker list/period |
| `ci.yml` | push to main, PRs | `backend`: ruff + mypy (advisory) + pytest. `frontend`: tsc + static build + Playwright E2E |
| `deploy-pages.yml` | push touching `web/` | Static export → GitHub Pages |

Sharding exists for the Actions job-time cap and failure isolation — one shard's
yfinance hiccup doesn't take down the other three. It is **not** required for
correctness; locally, omit `--shard` entirely.

`--matrix` is exposed on the pilot job only. The full-universe path doesn't
accept it, by design.

---

## 7. The frontend

Next.js 15 / React 19, `output: "export"`, `basePath: "/signals-app"`,
`trailingSlash: true`. Reads Supabase directly with the anon key — no backend in
the deployed path. Local IndexedDB (Dexie) mirrors state for offline use, with
`lib/sync.ts` reconciling against Supabase once signed in.

Routes: `/` (dashboard), `/signal` (detail), `/settings`.
23 components — `SignalCard`, `SignalMatrixRow`, `ConfluenceBar`, `TickerSearch`,
`WatchlistPanel`, `AuthPanel`, `RecentRunsTable`, `SignalLineageTree`, others.

**`SpaRedirect` + `public/404.html` are load-bearing.** GitHub Pages has no
server-side rewrites, so deep links 404 and are bounced back through
`404.html` into the SPA. The build step (`next build && cp public/404.html
out/404.html`) is what makes deep linking work at all.

> The E2E tests run against the **static export**, never `next dev`. All three
> deploy bugs found in PR #10 were invisible to the dev server, and the broken
> deploy they caused went unnoticed for 7 weeks. Testing the dev server would
> reopen exactly that blind spot.

---

## 8. The feedback loop: calibration

The engine tunes itself:

1. `scan` writes signals with a strength label (`WEAK`…`VERY_STRONG`).
2. Time passes; `forward_returns` accumulates realized outcomes.
3. `calibrate.yml` runs weekly, deriving a **hit rate per strength label** and
   writing a new generation to `calibration`.
4. The next scan's `ConfluenceRanker` reads those rates and adjusts vote weights:
   above 0.60 hit rate the strength's vote is boosted, below 0.50 it is damped.

`load_strength_hit_rates_from_supabase()` is called **once per scan run**, not
once per symbol — it's the same table read regardless of ticker. If it returns
`None` (no generation yet, or Supabase unreachable), scoring falls through to the
uncalibrated defaults rather than failing.

---

## 9. Configuration and secrets

All tunables live in `config.py` as `Final` constants. The `Settings` dataclass
reads env vars at construction.

| Var | Purpose | Required |
|---|---|---|
| `SUPABASE_URL` | project URL | for writes |
| `SUPABASE_SERVICE_ROLE_KEY` | writer credential | for writes |
| `OPENROUTER_API_KEY` | LLM; takes priority over Gemini | for synthesis |
| `GEMINI_API_KEY` | fallback LLM | optional |
| `OPENROUTER_MODEL` | default `google/gemini-2.0-flash-001` | optional |

`llm_provider` resolves to `"openrouter"` → `"gemini"` → `"none"`. With `"none"`,
gated symbols still compute and persist; they just carry no LLM narrative.

Degraded-mode handling is real, not aspirational: a circuit breaker
(5 failures / 60 s → open for 300 s) plus a 15 s per-call timeout means LLM
trouble degrades output rather than failing the run.

---

## 10. Running it locally

```bash
source /opt/homebrew/Caskroom/miniforge/base/bin/activate signals-app

# whole universe, free: gate + log, no LLM, no writes  (~44 s)
python scripts/scan_universe.py --seed seed/universe_symbols.csv --dry-run

# a few tickers, for real
python scripts/scan_universe.py AAPL MSFT NVDA

# whole universe, for real (~403 LLM calls — see the findings doc first)
python scripts/scan_universe.py --seed seed/universe_symbols.csv --trigger manual

# local API
uvicorn signals_app.api.main:app --reload --port 8010

# frontend
cd web && npm run dev
```

`--shard` is only for Actions. `--limit N` caps the run. `--max-concurrent`
defaults to 4 because yfinance throttles.

---

## 11. Testing

- **72 Python tests** across 6 files (detection, scan_universe, calibration +
  data quality, backfill, historical/backtest, calibrate-supabase).
- **Playwright E2E** in `web/e2e/` against the static export: `deploy-smoke`
  (boots, no console errors, no 4xx assets, deep link survives the 404 bounce)
  and `dashboard` (sections mount, search accepts input).
- **CI**: pytest blocking; ruff + mypy advisory pending the cleanup pass; frontend
  typecheck, build, and E2E blocking.

---

## 12. Design decisions worth knowing

**Gate before spend.** Stated three times because it explains the module
boundaries: `scan_universe.py` calls the layers directly instead of reusing
`api/routes.py:get_signals()` purely so the gate can sit between L4 and L5.

**Two-level failure isolation.** Detectors within a ticker, tickers within a run.
Neither aborts; both tally.

**Dependency-injected writer.** `SignalWriter` is a Protocol, so the whole scan
is testable with no live Supabase — and `--dry-run` is just `writer=None`.

**Deterministic sharding.** Shards are taken over a *sorted* list, so `--shard
2/4` selects the same tickers regardless of which Actions job starts first.
Verified: the four shards sum exactly to the unsharded run.

**Calibration is generation-versioned,** not overwritten — you can see what the
engine believed at any past point.

**Cost multipliers are opt-in.** `--matrix` is 5x and is unavailable on the
expensive path, not merely discouraged there.

---

## 13. Current state and known gaps

Shipped and live-verified: all 11 phases. Five bugs were caught only by live
testing and are fixed — the never-working Pages deploy, silent PostgREST `409`
upserts, a timestamp-timezone mismatch breaking the calibration join, an
unencoded `+` in a query URL, and "forget me" not clearing cloud data.

Open, in priority order (full detail in [TODO.md](TODO.md)):

- **P0** — `OPENROUTER_API_KEY` is not set in either place; per-call token cost
  unmeasured; the staged live run hasn't happened. The call count *is* now known
  exactly: 403.
- **P1** — no `data-testid` hooks (0 hits in `web/src`); 2 time-of-day-flaky
  calibration tests that build fixtures from `date.today()` and trip the 26-hour
  staleness gate.
- **P2** — 121 ruff findings (42 auto-fixable) and 38 mypy findings (14 are just
  missing pandas stubs), both advisory in CI. The point of the cleanup is
  flipping them to blocking; fixing without flipping just lets them re-accumulate.
- **P3** — whether to run `--matrix` at full-universe scale (now a priced
  decision: ~2,000 calls); whether 4 shards is the right count, pending real
  wall-time data.

---

**See also:** [TODO.md](TODO.md) · [universe-scan-findings.md](universe-scan-findings.md) ·
[backend-state-and-supabase-plan.md](backend-state-and-supabase-plan.md)
