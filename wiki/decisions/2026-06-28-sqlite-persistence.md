# 2026-06-28 — Local SQLite Persistence (Neon-Ready)

**Commit**: `28ab06c` (PR #2).

**What changed**: added a SQL persistence layer
([architecture/backend.md#persistence](../architecture/backend.md#persistence))
so signal-run history lives in a real queryable database, not only in the
browser's IndexedDB. `db/models.py` (SignalRun table), `db/session.py`
(async engine factory), `db/ops.py` (`record_run()` / `get_ticker_history()`).

**Why "Neon-ready" rather than "Neon"**: `db/session.py` defaults to
SQLite+aiosqlite (`signals_local.db`) for local dev, and swaps to
Neon/Postgres automatically when `DATABASE_URL` is set to a
`postgresql+asyncpg://` URL — same code path, no branching logic needed at
the call site. This means local dev has zero external dependencies (no
Postgres instance required to run the app), while a production deploy is a
one-env-var change away from a real hosted Postgres (Neon). Tables are
created on startup via `init_db()`, called from `api/main.py`'s startup
event — no separate migration step for this simple single-table schema.

**Why the schema mirrors the frontend's `HistoryEntry`**: `RunRecord.to_dict()`
deliberately renames fields (`resolved_period → resolvedPeriod`,
`direction → signal`, `ai_degraded → aiDegraded`) to match the frontend
Dexie `HistoryEntry` shape exactly, so `/history/{symbol}` can be consumed
directly by the web client with zero client-side transformation — see
[architecture/frontend.md](../architecture/frontend.md#local-first-data-model).
This was a deliberate design choice to keep the backend and frontend history
schemas readable as "the same event, two independent logs" rather than
requiring an adapter layer.
