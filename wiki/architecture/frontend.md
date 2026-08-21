# Frontend Architecture

Next.js 15.3.3 / React 19, in `web/`. Statically exported
(`output: 'export'`) and deployed to GitHub Pages under basePath
`/signals-app`.

## Directory map

```
web/src/
├── app/
│   ├── page.tsx                     # home / ticker search
│   ├── settings/page.tsx
│   └── signals/[symbol]/
│       ├── page.tsx                  # server shell, generateStaticParams
│       └── _client.tsx               # client-fetch component (useState/useEffect)
├── components/
│   ├── SignalCard.tsx                 # primary signal display
│   ├── ConfluenceBar.tsx               # bull/bear/neutral + alignment bars
│   ├── EvidenceList.tsx
│   ├── CouncilPanel.tsx, SignalMatrixRow.tsx  # multi-timeframe display
│   ├── SignalLineageTree.tsx, SignalHistoryPanel.tsx, RecentRunsTable.tsx
│   ├── WatchlistPanel.tsx, WatchlistButton.tsx
│   ├── PeriodSelector.tsx, PeriodControlPanel.tsx
│   ├── SettingsPanel.tsx, RunRecorder.tsx, TickerSearch.tsx, Greeting.tsx
└── lib/
    ├── types.ts       # TS mirror of the backend Pydantic schema
    ├── api.ts          # fetchSignal, checkHealth
    ├── db.ts            # Dexie local-first data model
    ├── cookies.ts        # SSR-visible profile mirror
    └── useProfile.ts      # hook bridging db.ts + cookies.ts
```

## Static export model

`next.config.ts` sets `output: 'export'` with `basePath: '/signals-app'` (no
`assetPrefix` — basePath covers it). Because static export can't run
server-side rewrites, `[symbol]/page.tsx` is split: a server shell handles
`generateStaticParams`, and `_client.tsx` does the actual data fetch
client-side via `useState`/`useEffect`, hitting `NEXT_PUBLIC_API_URL` (or the
`/api` rewrite proxy in `next dev`, which doesn't survive into the static
export). `public/404.html` plus a redirect-restoring script in `layout.tsx`
work around GitHub Pages' lack of SPA routing for deep links like
`/signals/AAPL/`. See [decisions/2026-06-28-github-pages-deploy.md](../decisions/2026-06-28-github-pages-deploy.md).

## Local-first data model (`lib/db.ts`)

The frontend keeps its own full history entirely in the browser via Dexie
(IndexedDB) — no server database involved on this side. Five tables in the
`signals_app` Dexie database:

| Table | Purpose |
|---|---|
| `profile` | single row (`id = "me"`): name, defaultPeriod, defaultNoLlm, theme, firstSeen/lastSeen, lastTicker, lastSignal, totalRuns |
| `history` | append-only run log — ticker, period, resolvedPeriod, noLlm, signal, confidence, aiDegraded, ts |
| `watchlist` | starred tickers with note, targetPrice, lastSignal/lastCheckedAt |
| `savedConfigs` | named (period, noLlm) presets |
| `alerts` | per-ticker rules: target direction + minConfidence, fires via `evaluateAlerts()` |

`createDb()` guards against SSR (`typeof window === "undefined"` → `null`),
and every exported function short-circuits to a no-op/empty value when `db`
is null — this is the seam that a Node runtime with a broken global
`localStorage` can trip over if that guard is bypassed at import time by a
polyfill (see [ops/known-issues.md](../ops/known-issues.md)).

`recordRun()` is the central write path: appends to `history`, bumps
`profile` (`lastSeen`/`lastTicker`/`lastSignal`/`totalRuns` via a
read-modify-write transaction), refreshes the `watchlist` row if the ticker
is watched, and evaluates `alerts` for that ticker — returning any that
fired so the UI can surface a notification.

`exportAll()` / `wipeAll()` give the user full data ownership — dump
everything as JSON, or erase every table ("forget me").

## SSR ↔ client bridge (`cookies.ts`, `useProfile.ts`)

IndexedDB is async and client-only, so the server can't read it during SSR
for a personalized first paint. `cookies.ts` mirrors a small, non-sensitive
subset (`name`, `lastTicker`, `lastSignal`, `lastSeen`) into a
`SameSite=Lax` cookie (not `HttpOnly` — client needs to write it), read on
both sides. `useProfile()` (`lib/useProfile.ts`) is the hook every component
uses: on mount it reads the Dexie profile async, and every `record()` call
updates both the Dexie history and the cookie mirror together so the next
server render greets the user correctly while the richer local DB keeps full
history.

## Rendering a signal

See [concepts/signal-rendering.md](../concepts/signal-rendering.md) for how
`SignalCard` and `ConfluenceBar` turn a `Signal`/`ConfluenceResult` into UI.
