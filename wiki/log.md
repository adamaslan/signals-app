# Wiki Log

Append-only. Format: `## [YYYY-MM-DD] type | description`

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
