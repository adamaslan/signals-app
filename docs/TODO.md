# signals-app — TODO

**Last updated:** 2026-08-20
**State:** All 11 phases of [backend-state-and-supabase-plan.md](backend-state-and-supabase-plan.md)
are shipped and merged (PRs #7–#17). The engine runs unattended, calibrates itself,
and serves a live dashboard. Everything below is **verification, cleanup, and cost
work** — no unbuilt features.

Counts in this file were measured on 2026-08-20, not copied from an older doc.

> **Note on links:** `uni5.html` / `uni6-*.md` live in the separate `homebase`
> repo (`~/code/homebase/docs/signals-app-docs/`). The relative links below
> resolve when both repos are checked out side by side under `~/code/`, but not
> on github.com.

---

## P0 — Blocking the production run

### 1. Set `OPENROUTER_API_KEY` in both places
- [ ] Add to local `signals-app/.env` (copy file-to-file from
      `~/code/nuwrrrld-portal/.env.local` — never paste the value into a shell arg)
- [ ] `gh secret set OPENROUTER_API_KEY` via stdin pipe
- [ ] Confirm `gh secret list` shows 3 secrets, not 2

> Currently `gh secret list` shows only `SUPABASE_URL` and
> `SUPABASE_SERVICE_ROLE_KEY`. The OpenRouter *code path* is shipped
> (`src/signals_app/synthesis/mtf_llm.py:173`) and both workflow jobs already pass
> the key through — only the key itself is missing.

Full procedure: [uni6 §1](../../homebase/docs/signals-app-docs/uni6-production-run-and-e2e.md)

### 2. Price the full-universe run before spending
- [x] Dry-run all 4 shards, record the `P published` count per shard
  - Shard 0: 115 published
  - Shard 1: 96 published
  - Shard 2: 98 published
  - Shard 3: 94 published
  - **Total: 403 LLM calls** (gate is working — ~42% of 954-ticker universe)
- [ ] Get real token counts from one live single-symbol call
- [ ] Sanity check: if the total approaches 954, the gate isn't gating — stop and investigate

### 3. Staged live run
- [ ] Pilot: 5 tickers with a real key, verify rows land in Supabase and
      `llm_provider` reads `openrouter`
- [ ] Full universe on Actions: `gh workflow run signals-scan.yml -f full_universe=true`
- [ ] **Record per-shard wall time** against the 90-min `timeout-minutes` cap
- [ ] If any shard >60 min: bump `matrix.shard` to `[0..7]` and `--shard .../8`
- [ ] Update [uni5.html](../../homebase/docs/signals-app-docs/uni5.html) with measured cost + timing,
      strike the last debt item

---

## P1 — Test coverage gaps

### 4. Playwright E2E (nothing exists yet)
Verified: no `playwright.config.ts`, no `e2e/` dir, `playwright` appears 0 times in
`web/package.json`.

- [x] Install `@playwright/test` + `serve-handler` in `web/`
- [x] Write `web/scripts/serve-static.mjs` — a **GitHub-Pages-accurate** static server
      (`basePath: /signals-app` + `404.html` SPA rewrite)
- [x] `web/playwright.config.ts` pointed at the static export, **not `next dev`**
- [x] `deploy-smoke.spec.ts`: page boots, no console errors, no 4xx assets, deep link
      survives the 404 redirect (✅ all 4 smoke tests pass)
- [x] `dashboard.spec.ts`: Recent Runs + Watchlist sections mount, ticker search accepts input
      (scaffold written; will pass once UI elements are added to markup)
- [x] Add to `ci.yml` `frontend` job as a **blocking** step (job already has
      `defaults.run.working-directory: web`)
- [ ] `gh secret set SUPABASE_ANON_KEY` — only needed if E2E reads live data

> **Must run against the static export.** All three deploy bugs from PR #10 were
> invisible to `next dev`; testing the dev server would re-open the blind spot that
> hid a broken deploy for 7 weeks.

### 5. Add `data-testid` hooks
Verified: `grep -r 'data-testid' web/src` → **0 hits**. Text/role selectors work for
now but get brittle on data-driven cells.

- [ ] `TickerSearch`, `SignalCard`, `SignalMatrixRow`, `ConfluenceBar`,
      `AuthPanel`, `WatchlistButton`

### 6. Fix the 2 time-of-day-flaky tests
`tests/test_calibration_and_data_quality.py` builds fixtures from `date.today()`
(lines 121, 129, 145, 163, 181), which is **midnight** — so once >26h has elapsed
the fixture trips `DATA_QUALITY_STALE_HOURS = 26.0` and the test fails depending on
clock time.

- [ ] Freeze time in the fixture (inject an explicit `datetime`, or `freezegun`)
- [ ] **Do not** widen `DATA_QUALITY_STALE_HOURS` — that weakens a real production
      gate to satisfy a test

---

## P2 — Lint / type cleanup

Do this as **its own PR, after** the production run. A 121-finding sweep mixed into
infra work is where a real regression hides.

### 7. Ruff: 121 findings → 0, then make blocking
**42 are auto-fixable.** Measured breakdown:

| Count | Rule | Auto? |
|---|---|---|
| 57 | `E501` line-too-long | manual |
| 21 | `F401` unused-import | ✅ |
| 13 | `E402` module-import-not-at-top | manual |
| 11 | `I001` unsorted-imports | ✅ |
| 6 | `UP042` replace-str-enum | manual |
| 4 | `UP017` datetime-timezone-utc | ✅ |
| 4 | `UP037` quoted-annotation | ✅ |
| 2 | `E741` ambiguous-variable-name | manual |
| 2 | `UP041` timeout-error-alias | ✅ |
| 1 | `F841` unused-variable | manual |

- [ ] `ruff check --fix src scripts tests` — clears 42 in one commit
- [ ] Then one commit per remaining rule category
- [ ] `E402` needs care: some may be deliberate (post-`load_dotenv()` imports)
- [ ] `UP042` touches the `SignalStrength` / `SignalCategory` str-enums in `config.py` — behavior-sensitive, review individually
- [ ] Flip the ruff step in `ci.yml` from advisory → **blocking**

### 8. mypy: 38 findings → 0, then make blocking
**14 of the 38 are just missing pandas stubs** — one dependency line, not real work.

| Count | Code |
|---|---|
| 14 | `import-untyped` (mostly pandas) |
| 9 | `operator` |
| 6 | `type-arg` |
| 4 | `unused-ignore` |
| 2 | `no-any-return` |
| 2 | `attr-defined` |
| 1 | `no-untyped-def` |

- [ ] Add `pandas-stubs` to `environment.yml` → kills ~14 findings
- [ ] `unused-ignore` (4) — delete stale `# type: ignore` comments, free win
- [ ] `type-arg` (6) — e.g. bare `dict` at `src/signals_app/api/routes.py:250`
- [ ] `operator` (9) — the real work; likely pandas/None arithmetic
- [ ] Flip the mypy step in `ci.yml` from advisory → **blocking**

> The flip to blocking is the point. Fixing the findings without it just lets them
> re-accumulate.

---

## P3 — Deferred product decisions

### 9a. Close the signal-coverage gap vs. `boll-4-april-500.py`
`~/code/homebase/docs/boll4-500b.md` documents a sibling script
(`ai-fin-opt2/alpha-fullstack/ai-fin3/boll-4-april-500.py`) that emits up to
~555 signals/bar vs. `signals-app`'s ~166/ticker ceiling. Per that doc's
comparison table, the gap is two separate things — do them as separate PRs:

- [ ] **Add a `SUPPORT_RESISTANCE` detector.** This is the single biggest gap:
      4 pivot-detection windows × 4 proximity levels × up to 5 support + 5
      resistance levels — ~3,249 of 8,604 observed signals (38%) in the
      reference run, and the only one of the doc's 9 parameter-grid categories
      `signals-app` doesn't cover yet.
      - Reuse `indicators/pivots.py` (`precompute_pivots`, `get_nearest_levels`)
        rather than reimplementing pivot detection — same O(1)-lookup design
        `boll-4-april-500.py` independently converged on.
      - New `detection/support_resistance.py`, registered in
        `orchestrator.py:get_default_detectors()` (18 → 19 detectors).
      - Signal pattern: `NEAR SUPPORT (w={w}, prox={p}%)` /
        `NEAR RESISTANCE (w={w}, prox={p}%)`, category `SUPPORT_RESISTANCE`,
        strength BULLISH/BEARISH — see boll4-500b.md's S/R section for the
        exact fire conditions and threshold grid (`SR_PROXIMITIES` already
        exists in `indicators/grids.py:83`, unused until this lands).
      - Update `docs/app-overview.md`'s per-ticker signal ceiling once merged.
- [ ] **(Separately, lower priority) historical bar-by-bar scanning.**
      `signals-app`'s detectors currently read `df.iloc[-1]` only (latest bar).
      `boll-4-april-500.py`'s `detect_signals()` loops
      `for i in range(start_bar, len(df))`, running every detector at every
      bar. Porting this to `orchestrator.py` would let `scan_universe.py`
      optionally emit a full historical signal timeline per ticker, not just
      the latest-bar snapshot — useful for backtesting/calibration, not
      needed for the live daily scan. Gate behind a flag (e.g.
      `--historical`), matching the opt-in pattern already used for
      `--matrix`.

### 9b. New script: per-symbol signal report (`boll-4`-style optimal.md)
`boll-4-april-500.py` writes a human-readable Markdown report per symbol
(`{SYMBOL}_{ts}.md`) alongside its JSON output — `signals-app` has no
equivalent; `scan_universe.py` only prints a one-line summary to stdout.

- [ ] New `scripts/generate_signal_report.py`:
      - Takes a ticker (or `--seed` list) and reuses the same L1–L4 pipeline
        `scan_universe.py` calls (fetch → indicators → detect → confluence) —
        do not duplicate pipeline logic, import from `signals_app.*` exactly
        as `scan_universe.py` does.
      - Writes `{ticker}_{timestamp}_optimal.md` with: signal counts by
        strength and category (mirroring boll4-500b.md's "At a Glance" /
        "Signals by Strength" / "Signals by Category" tables), the
        confluence score/bias/action, and the top-N most active signals —
        i.e. the same shape as boll4-500b.md itself, generated instead of
        hand-written.
      - No LLM calls, no Supabase writes — this is a local
        analysis/documentation tool, not a production path. Should work
        standalone against `--dry-run`-equivalent data.
      - Output directory: `docs/reports/` (gitignored, or archived per the
        docs/archive convention if checked in).

### 9c. Matrix computation at full-universe scale
`--matrix` is pilot-only by design — up to **5x** fetches and LLM calls per gated
symbol, and it's deliberately unavailable on the `full_universe` workflow path.

- [ ] Decide *after* the §2 cost numbers exist. Not a default; a priced decision.

### 10. Shard count vs. runtime
- [ ] Revisit `[0,1,2,3]` once real per-shard wall times are recorded (see P0 #3)

---

## Done — do not redo

All 11 phases shipped and live-verified (PRs #7–#17): repo hygiene, `ci.yml`,
Supabase schema + RLS, the writer + `scan_universe.py`, `signals-scan.yml`,
frontend reading Supabase, `backfill.yml`, `calibrate.yml`, Supabase Auth + sync,
the sharded 954-ticker universe, and the multi-timeframe matrix.

Five bugs were caught only by live testing and are already fixed: the never-working
Pages deploy, silent PostgREST `409` upserts, timestamp-timezone breaking the
calibration join, an unencoded `+` in a query URL, and "forget me" not clearing
cloud data. See [uni5.html](../../homebase/docs/signals-app-docs/uni5.html) for detail.

---

## Run the whole universe locally, with just Python

No GitHub Actions, no sharding, no secrets required for the dry run. `--shard`
is *optional* — omit it and one process scans all 954 tickers. Measured at
**43.8s wall** for the full dry run (`ThreadPoolExecutor`, 4 concurrent
fetches), so there is no reason to shard locally.

```bash
# once per shell
source /opt/homebrew/Caskroom/miniforge/base/bin/activate signals-app

# 1. FREE — all 954 tickers, gate + log only, no LLM calls, no writes
python scripts/scan_universe.py --seed seed/universe_symbols.csv --dry-run

# 2. Pilot with real LLM + Supabase writes (needs OPENROUTER_API_KEY, #1 above)
python scripts/scan_universe.py --seed seed/universe_symbols.csv --limit 5

# 3. The real thing — all 954, LLM + writes, single process
python scripts/scan_universe.py --seed seed/universe_symbols.csv --trigger manual

# optional: raise fetch concurrency (yfinance throttles; 4 is the safe default)
python scripts/scan_universe.py --seed seed/universe_symbols.csv --max-concurrent 8
```

- [x] Verify a single-process full-universe dry run reproduces the sharded
      totals — it does, **exactly**: 954 scanned / 403 published / 4 failed
- [ ] Run step 3 once `OPENROUTER_API_KEY` is set (this is the ~403-call spend)

> **Don't add `--matrix` here.** It is up to 5x fetches *and* 5x LLM calls per
> gated symbol — ~2000 extra calls at this scale. It is deliberately blocked on
> the `full_universe` Actions path for that reason (P3 #9); running it locally
> bypasses that guard rather than removing the cost.

Full measured detail: [universe-scan-findings.md](universe-scan-findings.md)

---

## Suggested order

Cheapest-first, so the free work de-risks the expensive work:

1. **#2** dry-run pricing *(free)*
2. **#4** Playwright scaffold + smoke test *(free, offline)*
3. **#1** key wiring *(10 min)*
4. **#3** pilot run *(cents)*
5. **#4** E2E into CI, blocking
6. **#3** full universe *(the spend — now with a known price)*
7. **#7/#8** lint + type cleanup, separate PR
8. **#6** flaky-test fix (fold into the cleanup PR)
