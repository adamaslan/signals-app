---
ARCHIVED: 2026-07-06
REASON: Superseded by the actual wiki at signals-app/wiki/ (see wiki/index.md) — this was the proposed structure, now built out.
---

# signals-app Wiki — Outline

No wiki currently exists for this project. This is a proposed structure, following
the LLM-wiki pattern (see `homebase/docs/karpathywiki.md`): raw sources stay
immutable, the wiki is an LLM-maintained layer of interlinked markdown pages, and
a schema file tells the agent how to keep it current.

If adopted, this would live at `signals-app/wiki/` with the schema entry in this
repo's own `CLAUDE.md`.

---

## 1. Layers

- **Raw sources** — this repo's own git history, commit messages, PR descriptions,
  and any research notes/papers on the underlying technical-analysis methods
  (RSI, MACD, Ichimoku, OBV/CMF, confluence scoring). Immutable; the wiki reads
  from these but never edits them.
- **Wiki** (`signals-app/wiki/`) — LLM-owned markdown pages described below.
- **Schema** — a `wiki/SCHEMA.md` (or a section in the repo's `CLAUDE.md`) telling
  the agent: when to ingest a new commit/PR, how to update pages, and the
  conventions for `index.md` / `log.md`.

## 2. Proposed file structure

```
signals-app/wiki/
├── index.md                 # catalog of every page, one-line summary each
├── log.md                   # append-only ingest/query timeline
├── overview.md               # what the app is, in one page
├── architecture/
│   ├── pipeline.md            # L1-L5 request lifecycle (fetch→indicators→detect→rank→synthesize)
│   ├── backend.md              # FastAPI app structure, config, DB layer
│   └── frontend.md             # Next.js app structure, Dexie local-DB, SSR/export model
├── concepts/
│   ├── signal-detectors.md     # the 18 detectors, grouped trend/momentum/volume
│   ├── confluence-scoring.md   # ConfluenceRanker: bull/bear counts, score, bias
│   ├── multi-timeframe.md      # period→timeframe mapping, alignment score
│   ├── llm-synthesis.md        # synthesize_single, fallback behavior, ai_degraded
│   └── signal-schema.md        # SignalOutput/Signal/Evidence Pydantic + TS mirror types
├── entities/
│   ├── detector-catalog.md     # one row per detector: class, category, signal names, trigger condition
│   └── api-endpoints.md        # /signals/{symbol}, /history/{symbol}, /health — params, responses
├── decisions/
│   ├── 2026-06-20-scaffold.md         # why this app exists, initial scope
│   ├── 2026-06-28-sqlite-persistence.md
│   └── 2026-06-28-github-pages-deploy.md
└── ops/
    ├── local-dev.md             # links out to docs/dev-setup-signals-pipeline.html
    └── known-issues.md          # open issues log (port conflicts, Node version, dev.sh path bug)
```

## 3. What goes on each page (content sketch)

### `overview.md`
One page answering: what is signals-app, who is it for, what does a "signal" mean,
what's the current deployment state (local dev only / GitHub Pages / prod backend).

### `architecture/pipeline.md`
The five-stage request lifecycle for `GET /signals/{symbol}`, cross-linked to
`concepts/signal-detectors.md`, `concepts/confluence-scoring.md`,
`concepts/llm-synthesis.md` for the stage that each links to.

### `concepts/signal-detectors.md`
Prose explanation of the detector Protocol, timeout isolation via ThreadPoolExecutor,
and the `SignalList.degraded`/`.warnings` mechanism — linking out to the full
per-detector table in `entities/detector-catalog.md`.

### `entities/detector-catalog.md`
A table, one row per detector class (e.g. `RSISignalDetector`, `MACDSignalDetector`,
`IchimokuDetector`, `OBVCMFDetector`), columns: category, signal names emitted,
trigger condition, source file + line.

### `concepts/confluence-scoring.md`
How `ConfluenceRanker` turns a flat signal list into `bull_count`/`bear_count`/
`score`/`bias`/`action` — the actual weighting/threshold logic, not just the shape.

### `concepts/llm-synthesis.md`
The feature dict built for the prompt, what the LLM is asked to return, the
fallback path (`_fallback_signal`) and when `ai_degraded` gets set — important
because this is the boundary between "rule-based" and "LLM-reasoned" output.

### `architecture/frontend.md`
Next.js static-export model (`output: 'export'`, GitHub Pages basePath, SPA 404
redirect), Dexie local-only persistence layer, how `useProfile`/`cookies.ts`
bridge SSR and client state.

### `decisions/*.md`
One page per major architectural turn (why SQLite before Neon, why static export
to GitHub Pages instead of a hosted Next.js server) — captures the *why* that
commit messages alone don't fully carry forward.

### `ops/known-issues.md`
Rolling list seeded from `docs/dev-setup-signals-pipeline.html`: the Node 25
`localStorage` SSR crash, port 8000 conflicts, `dev.sh` path fragility, missing
Node version pin — updated as issues are found/fixed rather than left in a
one-off HTML snapshot.

## 4. Indexing & logging conventions

- `index.md`: one line per page — `- [Confluence Scoring](concepts/confluence-scoring.md) — how bull/bear counts become a bias`.
  Regenerated/appended whenever a new page is added.
- `log.md`: append-only, one entry per ingest/query, format:
  `## [2026-07-06] ingest | PR #3 GitHub Pages deploy` — parseable with
  `grep "^## \[" wiki/log.md | tail -5`.

## 5. Bootstrapping steps (if this gets built)

1. Create `signals-app/wiki/` with `index.md` and `log.md` (empty shells).
2. Ingest current state: read `src/signals_app/**`, `web/src/**`, and the 5
   existing commits — write `overview.md` + the `architecture/*` pages.
3. Build `entities/detector-catalog.md` directly from the 18 detector classes.
4. Seed `ops/known-issues.md` from the existing
   `docs/dev-setup-signals-pipeline.html` content, then treat the HTML file as
   a frozen snapshot / superseded source once the wiki page exists.
5. Add a short "Wiki maintenance" section to this repo's `CLAUDE.md` (or a new
   `wiki/SCHEMA.md`) so future sessions know to update the wiki on each
   significant commit or design decision, not just write throwaway summaries.
