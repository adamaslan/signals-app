# Local Dev

## Quick start

```bash
# Backend (from repo root)
mamba env create -f environment.yml -y      # first time only
mamba install -n signals-app -c conda-forge aiosqlite -y   # first time only — missing from environment.yml
PYTHONPATH="$PWD/src" mamba run -n signals-app uvicorn signals_app.api.main:app \
  --reload --host 127.0.0.1 --port 8000 --log-level info

# Frontend (from web/)
npm run dev
```

Or via `.claude/commands/dev.sh` (runs both) — see caveats in
[known-issues.md](known-issues.md).

## Environment

- Backend reads `.env` (see `.env.example`) for `GEMINI_API_KEY` /
  `OPENROUTER_API_KEY` / `DATABASE_URL`. None are required in local mode —
  see [architecture/backend.md](../architecture/backend.md#config-configpy).
- Frontend reads `web/.env.local` (see `web/.env.local.example`):
  `BACKEND_URL` (server-side fetches) and `NEXT_PUBLIC_API_URL`
  (client-side, baked in at build). Leave `NEXT_PUBLIC_API_URL` unset in
  `next dev` to use the `/api` rewrite proxy instead.

## Full narrative + fixes

A full HTML write-up of a real local-dev session — every issue hit, root
cause, and fix — lives at
[`docs/dev-setup-signals-pipeline.html`](../../docs/dev-setup-signals-pipeline.html)
(and a duplicate at `docs/signals-app-dev-issues.html`). The durable,
maintained version of that content is [known-issues.md](known-issues.md) —
treat the HTML files as a frozen snapshot of one session, not the
up-to-date source.
