# 2026-06-28 — Deploy Frontend to GitHub Pages

**Commit**: `142fab9` (PR #3, two commits: initial deploy + review-response
fixes).

**What changed**: the Next.js frontend was made static-exportable and wired
to auto-deploy to GitHub Pages on every push to `main` touching `web/**`.
See [architecture/frontend.md](../architecture/frontend.md#static-export-model)
for the technical detail.

**Why static export instead of a hosted Next.js server**: GitHub Pages only
serves static files — there's no server runtime to run Next.js SSR/API
routes on. Choosing `output: 'export'` means the frontend can be hosted for
free with zero infrastructure to maintain, at the cost of losing
server-side rewrites (the `/api/*` proxy pattern that works in `next dev`
doesn't survive into the static export — the client must call
`NEXT_PUBLIC_API_URL` directly, baked in at build time).

**Consequence**: the deployed frontend has no backend to call unless
`NEXT_PUBLIC_API_URL` is set to point at a real hosted backend at build
time — as of this wiki, no such backend deployment exists (see
[overview.md](../overview.md#current-deployment-state)). The static site is
live, but functionally inert against real ticker data until a backend is
deployed somewhere and the env var is set.

**GitHub Pages deep-link problem and fix**: static hosts don't do SPA
routing, so a direct link to `/signals/AAPL/` 404s (GitHub Pages has no
server-side rewrite to fall back to `index.html`). Fixed with the
well-known SPA-on-static-host pattern: `public/404.html` redirects to the
app root with the original path encoded in a query param, and a script in
`layout.tsx` restores that path via `history.replaceState` before React
hydrates — so the user never sees a flash of the wrong page.

**Review fixes folded into the same PR** (second commit): the initial SPA
redirect took the `p` query param and used it directly, which is an open
redirect risk if it's not validated as a relative path — fixed to reject
anything not starting with a single `/` (blocks `//evil.com`-style
protocol-relative redirects). Also fixed a stale-fetch race condition in
`SignalDashboard` (rapid symbol/period changes could let an older fetch's
result overwrite a newer one — fixed with an `active` flag discarded on
effect cleanup), removed a redundant `assetPrefix` (already covered by
`basePath`), and replaced raw `<a>` tags with `next/link`'s `<Link>` so
`basePath` is applied centrally rather than per-link.
