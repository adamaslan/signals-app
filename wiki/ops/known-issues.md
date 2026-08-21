# Known Issues

Rolling list. Seeded from a real local-dev session documented in
[`docs/dev-setup-signals-pipeline.html`](../../docs/dev-setup-signals-pipeline.html) —
update this page as issues are found or fixed, rather than leaving that HTML
snapshot as the only record.

## Fixed

### Missing `aiosqlite` in `environment.yml`
**Symptom**: backend fails on startup with
`ModuleNotFoundError: No module named 'aiosqlite'` (or SQLAlchemy raising
`Can't load plugin: sqlalchemy.dialects:sqlite.aiosqlite`) the first time
`init_db()` opens the async engine.
**Root cause**: `aiosqlite` is imported by `db/session.py` and declared in
`pyproject.toml` (line 21), but is absent from `environment.yml` — so a
`mamba env create -f environment.yml` produces an env that can't open the
default local SQLite DB.
**Verify**: `grep aiosqlite environment.yml` returns nothing.
**Workaround**: install it manually into the env:
```bash
mamba install -n signals-app -c conda-forge aiosqlite -y
```
**Proper fix (not yet applied)**: add `aiosqlite` to the `dependencies:`
list in `environment.yml` so env creation is self-sufficient and matches
`pyproject.toml`.

### Node 25 SSR crash: `localStorage.getItem is not a function`
**Symptom**: every server-rendered frontend route returns HTTP 500; the
`next dev` console shows `TypeError: localStorage.getItem is not a function`
thrown during render, not a build error.
**Root cause**: Node 25 (`node --version` → v25.8.2 here) ships an
**experimental global `localStorage`** that is *present but non-functional*
outside a proper Web context. Dexie
([`db.ts`](../architecture/frontend.md#local-first-data-model)), imported
transitively during SSR, feature-detects `localStorage` as available and
calls into it, then throws. The `db.ts` `typeof window === "undefined"`
guard doesn't help here — the crash is inside Dexie's own detection, not the
app's guarded `createDb()`. Next.js 15.3.3 / React 19 predate Node 25, so
this runtime combination was never targeted.
**Verify**: the error only appears under Node ≥25; downgrading the runtime
or disabling the flag makes it vanish with no code change.
**Workaround**: disable the experimental API for the dev process:
```bash
NODE_OPTIONS="--no-experimental-webstorage" npm run dev
```
**Proper fix (not yet applied)**: pin a supported Node runtime (see
[No Node version pin](#no-node-version-pin) below) so the flag isn't needed;
optionally bake the flag into the `dev`/`build` npm scripts as a belt-and-
braces default.

## Open

### `.claude/commands/dev.sh` breaks when invoked from elsewhere
**Symptom**: running the script from outside the repo root fails with
`cd: .../web: No such file or directory`, and/or the backend crashes with
`ModuleNotFoundError: No module named 'signals_app'`.
**Root cause**: the script derives `SCRIPT_DIR` from its own location and
does `cd "$SCRIPT_DIR/web"`, which only resolves when the script physically
sits one level above `web/` (i.e. run from repo root). It also invokes
`mamba run -n signals-app uvicorn ...` directly, which did not reliably
inherit an exported `PYTHONPATH` in at least one dev session — so
`signals_app` wasn't importable.
**Verify**: `cd /tmp && /path/to/dev.sh` reproduces the `cd` failure.
**Workaround**: run the script only from repo root, or start the two
processes manually with `PYTHONPATH` set explicitly:
```bash
PYTHONPATH="$PWD/src" mamba run -n signals-app uvicorn signals_app.api.main:app --reload --port 8000
(cd web && npm run dev)
```
**Proper fix (not yet applied)**: resolve the repo root robustly (e.g.
`ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"`) and set
`PYTHONPATH="$ROOT/src"` inside the script before the `mamba run`.

### No Node version pin
**Symptom**: a fresh clone on whatever Node happens to be on `PATH` can hit
the SSR crash above (or other version-specific surprises) with no signal that
a specific runtime was expected.
**Root cause**: no `.nvmrc` and no `engines` field in `web/package.json`
(`grep '"engines"' web/package.json` → nothing). The project targets Next.js
15.3.3 / React 19, which predate Node 25's experimental Web Storage API.
**Verify**: `ls .nvmrc` fails; `node --version` currently reports v25.8.2 —
a version the app was never tested against.
**Proper fix (not yet applied)**: add `.nvmrc` (or `engines.node`) pinning
Node 20 or 22 LTS, which removes the need for
`--no-experimental-webstorage` entirely.

### Port 8000 default vs. real-world conflicts
**Symptom**: uvicorn exits at startup with
`ERROR: [Errno 48] Address already in use`, or — worse — the frontend
silently talks to an *unrelated* service already bound to `:8000` and
returns nonsense.
**Root cause**: docs/scripts hardcode `:8000`, a very common default that
other local projects also grab. No alternate port or pre-flight conflict
check exists.
**Verify**: `lsof -i :8000 -sTCP:LISTEN` shows a non-signals-app process
holding the port.
**Workaround**: run the backend on a free port and point the frontend at it,
e.g. `--port 8010` plus `BACKEND_URL=http://localhost:8010` in
`web/.env.local`. **Do not kill the conflicting process** without confirming
it's safe — it may be an unrelated running service holding live state, per
the general safety rules.
**Proper fix (not yet applied)**: make the port configurable via env
(`PORT`/`--port`) with a documented default, and have `dev.sh` fail fast with
a clear message if the chosen port is occupied.

### `SignalOutput.matrix` unused on the live route
`compute_multi_timeframe()` and `build_timeframe_matrix()`
(see [concepts/multi-timeframe.md](../concepts/multi-timeframe.md)) are
fully implemented but not called from `GET /signals/{symbol}` —
`matrix` is always `None` in practice today.
**Impact**: not a bug, but the multi-timeframe UI components
(`CouncilPanel`, `SignalMatrixRow`) have no live data source — anything they
render is empty/placeholder until a route populates `matrix`.
**Verify**: `grep -rn "build_timeframe_matrix\|compute_multi_timeframe" src/signals_app/api/`
returns no hits from the request handlers.

### No deployed backend behind the deployed frontend
The frontend is live on GitHub Pages
(see [decisions/2026-06-28-github-pages-deploy.md](../decisions/2026-06-28-github-pages-deploy.md)),
but no backend is deployed anywhere for it to call — `NEXT_PUBLIC_API_URL`
has no real target in production yet.
**Impact**: the public site loads but every signal fetch fails; it's a
functional shell until a backend is hosted and the env var is set at build
time.
**Verify**: check the GitHub Pages `deploy-pages.yml` env / repo secrets for
a `NEXT_PUBLIC_API_URL` value — currently unset/placeholder.

### Frontend `types.ts` is a hand-maintained schema mirror
`web/src/lib/types.ts` mirrors the backend Pydantic schema
(`schemas/signal_output.py`) by hand, with no codegen or contract test
linking them — see
[concepts/signal-schema.md](../concepts/signal-schema.md#frontend-mirror-websrclibtypests).
**Impact**: a backend field rename/retype won't be caught at build time; the
mismatch surfaces only as a runtime parse/render failure in the browser,
possibly long after the backend change lands.
**Verify**: diff the `SignalDirection` / `EvidenceSource` / `Timeframe` unions
in `types.ts` against the corresponding enums in `signal_output.py` — nothing
enforces they stay aligned.
**Proper fix (not yet applied)**: generate `types.ts` from the Pydantic
models (e.g. via an OpenAPI/JSON-schema export), or add a CI contract check
that fails on drift.
