# signals-app — Overview

**What it is**: a financial signal detection and LLM-synthesis app for equities.
Given a ticker and a time period, it runs ~18 independent technical-analysis
detectors over OHLCV data, aggregates them into a weighted confluence score,
then (optionally) asks an LLM to turn that into a structured directional call
with confidence and cited evidence.

**Who it's for**: a single local user doing manual/semi-automated technical
analysis — not a multi-tenant SaaS. Data persistence is local-first (SQLite
backend DB + browser IndexedDB on the frontend), with Neon Postgres wired in
as an optional upgrade path (`DATABASE_URL` env var) rather than a requirement.

**What "a signal" means**: see [concepts/signal-detectors.md](concepts/signal-detectors.md)
for the full definition. Short version: a signal is a directional call
(`strong_buy` → `strong_sell`) on one ticker/timeframe, backed by evidence
items whose weights are validated to sum to 1.0, with confidence constrained
to never be exactly 0 or 1, and HOLD calls capped at 0.75 confidence.

## Two halves

- **Backend** — Python 3.12, FastAPI, `src/signals_app/`. See
  [architecture/backend.md](architecture/backend.md).
- **Frontend** — Next.js 15 / React 19, `web/`. Statically exported to GitHub
  Pages; local state lives entirely in the browser (Dexie/IndexedDB). See
  [architecture/frontend.md](architecture/frontend.md).

## Current deployment state

- Frontend: static-exported and deployed to GitHub Pages on every push to
  `main` touching `web/**` (see [decisions/2026-06-28-github-pages-deploy.md](decisions/2026-06-28-github-pages-deploy.md)).
- Backend: not deployed anywhere yet — local dev only, via
  `mamba run -n signals-app uvicorn signals_app.api.main:app`. The deployed
  frontend has no live backend to call unless `NEXT_PUBLIC_API_URL` is pointed
  at one manually.
- Local dev setup has a handful of known rough edges — see
  [ops/known-issues.md](ops/known-issues.md).

## Repo layout

```
signals-app/
├── src/signals_app/     # backend Python package
├── web/                 # Next.js frontend
├── scripts/              # analyze.py CLI, run_local.sh
├── tests/
├── backtests/
├── docs/                 # HTML dev-session snapshots (see ops/known-issues.md)
└── wiki/                 # this wiki
```

## Index

See [index.md](index.md) for the full page catalog.
