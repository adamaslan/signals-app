# Local Universes

A **universe** is a named, device-local basket of tickers the user can
track over time and (later) backtest. It sits alongside the flat
`watchlist`, not replacing it: `watchlist` stays the low-friction
single-ticker path; a universe is the "run and read this whole basket as a
batch" object.

Design doc: `homebase/docs/signals-app-docs/local-universe-save-track-backtest.md`.
Introduced on branch `feat/local-universes` (2026-08-28), build steps 1–4
of that doc. Steps 5–9 (Supabase views, the `universe_hit_rates` RPC
backtest, cross-device sync, alerts/timeline/lineage) are not built.

## Why it's a Supabase read, never a new endpoint

The production data path is `GitHub Actions cron → scan_universe.py →
Supabase Postgres → browser (direct, anon key)`. FastAPI is not deployed.
So everything a universe needs is either a Supabase read or client-side
compute over rows already fetched:

- **Refreshing a universe** (`runUniverse`) is **one batched `.in()` query**
  against `signals` (chunked at 200 tickers), keeping the newest row per
  ticker client-side. Not a fan-out of N requests.
- **Coverage** (`refreshCoverage`) is one `.in()` query against `symbols`.
- **Backtest** will be a `security definer` RPC over `detector_hits ⋈
  forward_returns` — not built yet; `backtestUniverse()` throws
  "not deployed" until then.

## The three Dexie tables (db.ts v2)

`this.version(2)` adds three **new** tables (no `upgrade()` callback — only
populated-table reshapes need one):

| Table | Purpose |
|---|---|
| `universes` | `++id, name, updatedAt` — the basket: name (unique per device, case-insensitive), note, `tickers[]` (uppercased/de-duped), `defaultPeriod`, `revision`, cached `coverage`. |
| `universeRuns` | `++id, universeId, startedAt, status` — one batch refresh: `universeRevision` snapshot, denormalised `results[]` (one `UniverseRunResult` per ticker), and a `summary` (bull/bear/neutral/failed/uncovered counts + avg confidence/data-quality/alignment). |
| `universeBacktests` | `++id, universeId, ranAt, [universeId+universeRevision+horizonDays]` — cached universe-level hit-rate buckets. The **compound index is the cache key**. |

`exportAll()` / `wipeAll()` extended to cover all three. `DIRECTION_RANK` is
now `export`ed from `db.ts` (was module-private) — `diffRuns` needs it.

### The `createDb()` guard changed

Was `typeof window === "undefined" → null`. Now `typeof indexedDB ===
"undefined" → null`. Rationale: a test runner that polyfills `indexedDB`
(`fake-indexeddb`) but not `window` now gets a **real, queryable** store,
which is how `universe.test.ts` runs against Dexie in Node. SSR behaviour is
unchanged (no `indexedDB` on the server either).

## Key semantics

- **`revision`** bumps on *every* membership mutation and **invalidates the
  cached `coverage`** (its ticker set is now stale). A `UniverseRun` records
  the revision it ran against; `diffRuns` flags (does not block) a
  comparison across differing revisions, so a membership edit can't silently
  masquerade as market movement.
- **uncovered ≠ failed.** A ticker with no `signals` row is recorded as
  `error: "uncovered"`, counted separately in the summary, and rendered
  distinctly (dashed "not scanned") — never as a `hold` or an error.
- **The paste box never silently drops.** `importTickersFromText` returns
  `{ added, skipped, invalid }`; the tokenizer treats newlines/commas/
  tabs/`;`/`|` as hard separators and truncates a line at its first numeric
  token (so `AAPL 100 shares` → `AAPL`, but `$aapl msft goog` keeps all
  three).
- **Wilson lower bound** (`wilsonLowerBound`) is the honest hit-rate number
  — a 3/3 bucket has a true 95% lower bound near 0.44, not 1.0. Used by the
  backtest panel (step 6, unbuilt) but the helper ships now.

## Helper surface — `web/src/lib/universe.ts`

CRUD (`createUniverse`/`getUniverse`/`listUniverses`/`renameUniverse`/
`updateUniverseMeta`/`deleteUniverse` — cascades runs + backtests) ·
membership (`addTicker`/`removeTicker`/`setTickers`/`importTickersFromText`,
each via a `mutateTickers` transaction that no-ops when nothing changes) ·
import/export (`exportUniverse`/`importUniverse` JSON, `exportUniverseCsv`)
· coverage (`refreshCoverage`) · tracking (`runUniverse`/`listUniverseRuns`/
`getUniverseRun`/`diffRuns`) · backtest helpers (`wilsonLowerBound`/
`toBucketDTO`/`getCachedUniverseBacktest`/`backtestUniverse`) · watchlist
bridges (`universeFromWatchlist`/`watchlistFromUniverse`, both one-way).

Every function is SSR-safe (`if (!db) return …`), mirroring `db.ts`.

## UI

- `/universe/` — one query-param static page (`?id=N` → editor, no id →
  list), matching the `/signal/` `output: export` pattern.
- `UniverseListPanel` · `UniverseEditor` · `UniverseTable` (sortable) ·
  `UniverseHeatmap` (colour = direction, opacity = confidence, corner dot =
  degraded/low-quality) · `UniverseDriftView` (7 drift classes +
  revision-mismatch banner).

## Related presentation fixes shipped in the same branch

Independent of universes but from the same doc's §5 "must" / "high" tier:

- **`FreshnessBadge`** + `lib/freshness.ts` — classifies the age of
  `bar_ts` as fresh / stale (1–3d) / very-stale (>3d) / unknown, so a
  9-day-old "STRONG BUY" from a broken cron run can't look live.
- **`DataQualityMeter`** — renders `data_quality_score`; below 0.7 it
  desaturates the whole `SignalCard` and shows `data_quality_reasons`.
- **`EngineHealthStrip`** — site-wide strip in `layout.tsx`, red when the
  newest `engine_runs` row is `failed`/`partial` or >26h old. `api.ts`
  gains `fetchEngineHealth`.
- `SIGNAL_ROW_COLUMNS` / `SignalOutput` gained `bar_ts`, `confluence_score`,
  `created_at` — no migration; those columns already exist on `signals`.
