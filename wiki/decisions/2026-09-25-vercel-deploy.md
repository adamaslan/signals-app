# 2026-09-25 — Second Host: Vercel Alongside GitHub Pages

**PR**: #37

**What changed**: the same static export (`web/`, `output: "export"`) now
deploys to Vercel in addition to GitHub Pages. Vercel builds from the
`web/` root directory with the Next.js preset; GitHub Pages is unchanged.

**Why it works from one codebase**: the only host-specific facts are the
URL prefix and the 404 handling.

- `basePath` is `""` when the build runs on Vercel (`VERCEL=1`) and
  `/signals-app` otherwise. `NEXT_PUBLIC_BASE_PATH` (used by the dev-only
  `/api` proxy calls) follows the same value.
- `scripts/postbuild.mjs` copies the SPA deep-link redirect `404.html` only
  for Pages builds — Vercel serves each exported route natively and keeps
  Next's own 404.
- The dev-only `/api/*` rewrite is registered only under `next dev`.

**Project setup facts** (not derivable from the code): the Vercel project was
first imported with the FastAPI preset and a pip install command, which made
every build fail; it is now root dir `web/`, Next.js preset, Node 22 to match
`web/.nvmrc`. The two public Supabase env vars are set for Production and
Preview. `web/vercel.json` carries the install command, security headers and
immutable caching for `/_next/static`. A root `.vercelignore` keeps the Python
pipeline out of CLI uploads — its entries must be anchored with a leading `/`,
because an unanchored `src` also matches `web/src` and breaks the build.

**Dependency bump**: Vercel refuses to deploy Next.js 15.3.3 (known
vulnerability), so next moved to 15.3.9 and react/react-dom to 19.0.8. Remaining
`npm audit` advisories only clear with Next 16.

**Open items**: both hosts deploy on every push to `main`; whether to keep
both is undecided. FastAPI-era env vars (service-role key and friends) are
still on the Vercel project unused. See
[2026-06-28-github-pages-deploy.md](2026-06-28-github-pages-deploy.md) for the
original static-export reasoning.
