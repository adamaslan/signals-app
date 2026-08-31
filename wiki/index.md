# Wiki Index

## Start here

- [Overview](overview.md) — what signals-app is, who it's for, current deployment state

## Architecture

- [Pipeline (L1–L5)](architecture/pipeline.md) — the five-stage request lifecycle for `GET /signals/{symbol}`
- [Backend](architecture/backend.md) — FastAPI structure, config, DB layer, robustness patterns
- [Frontend](architecture/frontend.md) — Next.js static export, Dexie local-first data model, SSR bridge

## Concepts

- [Signal Detectors](concepts/signal-detectors.md) — the 18 detectors, orchestration, timeout isolation
- [Confluence Scoring](concepts/confluence-scoring.md) — how bull/bear votes become a score/bias/action
- [Multi-Timeframe](concepts/multi-timeframe.md) — weighted composite score + LLM alignment/divergence
- [LLM Synthesis](concepts/llm-synthesis.md) — OpenRouter → Gemini → rule-based fallback chain
- [Signal Schema](concepts/signal-schema.md) — the Pydantic contract (`Signal`, `Evidence`, validators)
- [Signal Rendering](concepts/signal-rendering.md) — how `SignalCard`/`ConfluenceBar` visualize a signal

## Entities

- [Detector Catalog](entities/detector-catalog.md) — full table: every detector, category, signal names, trigger
- [API Endpoints](entities/api-endpoints.md) — `/signals/{symbol}`, `/history/{symbol}`, `/health`

## Decisions

- [2026-06-20 — Initial Scaffold](decisions/2026-06-20-scaffold.md)
- [2026-06-28 — SQLite Persistence (Neon-Ready)](decisions/2026-06-28-sqlite-persistence.md)
- [2026-06-28 — GitHub Pages Deploy](decisions/2026-06-28-github-pages-deploy.md)
- [2026-08-30 — `service.py` seam + `signals` CLI](decisions/2026-08-30-service-seam-and-cli.md)

## Ops

- [Local Dev](ops/local-dev.md) — quick start, env vars
- [Known Issues](ops/known-issues.md) — fixed and open issues, rolling list
