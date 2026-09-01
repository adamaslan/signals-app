# Wiki Log

Append-only. Format: `## [YYYY-MM-DD] type | description`

## [2026-08-31] ingest | branch feat/local-universes-5-9 — Local Universes steps 5–9

Ingested the steps 5–9 build (4 commits) on top of the merged PR #20. Four
additive Supabase migrations (`latest_signals` view; `detector_outcomes` +
`universe_hit_rates` / `universe_backtest_meta` security-definer RPCs;
`universes` cloud sync table; `coverage_requests` queue). `lib/stats.ts`
(Wilson lower+upper, thin<30). `backtestUniverse` is now RPC-backed and
cached. New UI: `UniverseBacktestPanel`, `UniverseTimeline`,
`ProvenanceChips`, `CalibrationHint` (+ `lib/calibration.ts`), coverage-
request links. `sync.ts` gained `mergeUniverses` + `syncUniverseUp/Delete`.
40 unit tests (26 universe + 9 stats + 5 freshness) + 11 e2e green.

Deferred: server-side filter/sort (#11), counter-evidence equal-weight (#3),
confluence internals (#7), saved views (#12), universe alerts (#13),
lineage drill-down (#20).

Pages touched: updated `concepts/local-universes.md` (steps 5–9 sections),
this log.

## [2026-08-28] ingest | branch feat/local-universes — Local Universes (build steps 1–4)

Ingested the local-universe feature build (`feat/local-universes`, 2 commits):
Dexie v2 (`universes`/`universeRuns`/`universeBacktests` + `createDb()` guard
now keys off `indexedDB` not `window`), `web/src/lib/universe.ts` helper
library, `/universe/` UI (list + editor + sortable table + heatmap + drift
view), and the §5 "must"-tier presentation fixes (`FreshnessBadge`,
`DataQualityMeter`, `EngineHealthStrip`; `bar_ts`/`confluence_score`/
`created_at` added to `SIGNAL_ROW_COLUMNS`). Vitest wired up (30 unit tests),
3 new e2e. Steps 5–9 of the design doc (Supabase views, `universe_hit_rates`
RPC backtest, cross-device sync, alerts/timeline/lineage) not built.

Pages touched: new `concepts/local-universes.md`; updated
`architecture/frontend.md` (v2 tables + guard change), `index.md`.

## [2026-07-06] update | Hardened ops/known-issues.md

Made every dev-issue entry more robust: added **Symptom / Root cause /
Verify / Workaround / Proper fix** structure where applicable (and
**Impact / Verify** for the observational entries). Re-verified the asserted
facts against the repo first — `aiosqlite` absent from `environment.yml`
(present in `pyproject.toml:21`), Node v25.8.2, no `.nvmrc`/`engines` field.
Each "Fixed" and "Open" item now carries a concrete one-line reproduction/
detection command and a distinct proper-fix note separate from the
workaround.

## [2026-07-06] build | Initial wiki build-out

Built the full wiki structure from scratch per `docs/wiki-outline.md`,
ingesting the entire backend (`src/signals_app/**`) and frontend
(`web/src/**`) source, plus all 5 commits in the repo's git history.

Pages created: `overview.md`, `index.md`, `log.md`,
`architecture/{pipeline,backend,frontend}.md`,
`concepts/{signal-detectors,confluence-scoring,multi-timeframe,llm-synthesis,signal-schema,signal-rendering}.md`,
`entities/{detector-catalog,api-endpoints}.md`,
`decisions/{2026-06-20-scaffold,2026-06-28-sqlite-persistence,2026-06-28-github-pages-deploy}.md`,
`ops/{local-dev,known-issues}.md`.

Known gaps flagged for future ingest passes: `CouncilPanel`/`SignalMatrixRow`
prop shapes not read in detail (stubbed in `signal-rendering.md`);
`indicators/compute.py`, `indicators/grids.py`, `indicators/pivots.py`,
`indicators/divergence.py`, `scoring/relative_strength.py`,
`data/fetcher.py`, `utils/safety.py`, and `backtests/` not yet ingested at
the same depth as detection/scoring/synthesis/schema — worth a follow-up
pass. `tests/` not reviewed for what behavior is actually covered.

## [2026-08-30] ingest | PR #21 feat: service.py seam + `signals` CLI (build steps 1–3) | pages touched: 3

Ingested the platform steps 1–3 change. `src/signals_app/service.py` is now
the single framework-agnostic seam; `api/routes.py` became a thin adapter
over it; a real `signals` Typer CLI replaced the misnamed `signals-analyze`
console script (now a deprecation shim → `signals serve`).

Pages: created `decisions/2026-08-30-service-seam-and-cli.md`; updated
`entities/api-endpoints.md` (error codes 404/503 replace 400/500 for
bad-symbol/upstream; CLI section added) and `index.md` (new decision link).

Not yet reflected: `architecture/backend.md` still describes the pre-seam
route structure — worth a pass when steps 4–5 (scan seam + MCP server) land,
since those touch the same section.

## [2026-08-30] ingest | PR #21 steps 4–7 — scan seam, MCP server, universes, cost gate | pages touched: 1

Extended the decision page 2026-08-30-service-seam-and-cli.md with steps 4–7:
scanner.py (scan pipeline lifted out of scripts/scan_universe.py, which is now
a thin shim), service.scan() + `signals scan`, the read-only MCP server
(mcp/server.py — 9 tools / 3 resources / 2 prompts, disclaimer from
COMPLIANCE.md §6), universes.py + `signals universe *` + scan --preset/--universe,
and the --estimate/--yes LLM cost gate. Also added docs/signals-app-docs/
signals-client-ts.html — a portal+schwab integration explainer (not a wiki page).

All in PR #21; steps 1–7 now shipped on branch feat/signals-service-cli.

## [2026-09-01] ingest | PR #24 fabricated 100/200SMA signals on short-history scans | pages touched: 1

Added a "Indicator warmup" section to concepts/signal-detectors.md: the
2026-09-01 universe scan published SELL/BUY calls (A, AAPL, AAPU, ABBV, ABT)
built on SMA_100/SMA_200 that had silently collapsed to a 63-bar mean
(min_periods=1 on a "3mo" ≈63-bar fetch). Fixed by using pandas' default
min_periods=window (NaN when unsatisfiable), widening DataFetcher's fetch for
short daily periods to clear the 200-bar floor, and a new
indicator_warmup_short data-quality deduction. Re-running the same 12 tickers
post-fix: all 5 previously-published symbols dropped below the gate. Full
analysis in docs/universe-scan-improvements.md.
