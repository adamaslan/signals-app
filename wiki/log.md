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
