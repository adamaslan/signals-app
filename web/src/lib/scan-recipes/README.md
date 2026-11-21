# Offline Scan Encyclopedia workbench

This is local research code, not a live screener or investment recommendation.

## What the counts mean

- 1,000 explicit recipes use 20 metric functions, five horizons, five evaluation views and two scopes.
- 500 recipes operate on one stock. The other 500 are constituent-median reductions, not a substitute for breadth, index returns or concentration.
- The separate 100-method pullback chapter has 100 chapter-specific calculators. Algorithms are present, but methods requiring unavailable histories or supplemental contracts return `missing`; this is not a successful numeric run.
- None of these counts means independent predictive edges. All results remain E0.

## Input and effects

The engines have no provider fetch, model call, database connection, account mutation or trade effect. Supply coherent OHLCV, an explicit session calendar, cutoff, source/feed, price basis and currency. Price adjustment quality is declared, not independently certified.

Recipe market rankings require membership snapshots for every evaluated date. Chapter cross-sectional calculators use the explicitly supplied cutoff membership. Historical member-local references describe that cutoff cohort, not a point-in-time historical index. Missing declared members block breadth rather than silently shrinking its denominator.

Benchmark chapter methods require `benchmark_symbol` and `benchmark`, separately from stock `bars` and constituent `panel`. Sector-relative methods require `sector_symbol` and `sector_proxy`. Each series must match the declared calendar exactly. All chapter methods currently require at least 252 sessions; some require 254, 274 or 278. Methods 47 and 50 require at least 505 sessions; method 97 requires 278. This is conservative shared warmup, not a claim that every formula intrinsically needs a year.

The UI can load invented data. Those results visibly identify the invented source and are not current market measurements. Uploaded JSON stays in browser memory.

## Run

Node 20+ is required. From this directory:

```
node tests/recipes.mjs
node tests/pullbacks.mjs
node tests/chapter-semantics.mjs
node tests/complete-methods.mjs
node tests/domain-controls.mjs
printf '%s' '{"action":"list"}' | node cli.mjs
printf '%s' '{"action":"list-methods"}' | node cli.mjs
```

Run a recipe with `{"recipe_id":"<catalog id>","input":{...}}`, or a chapter method with `{"method_id":"<chapter id>","input":{...}}`, through JSON stdin to `cli.mjs`.

Signals also exposes `python -m signals_app.scan_recipes --list`, `--list-methods`, `--recipe ID --input local.json`, and `--method ID --input local.json`. The main Signals command is `signals recipes` with those options. The full existing CLI requires the project's Python 3.12+ environment and dependencies, independently of this adapter.

## Test and publication limits

The pure engines, module adapter, actual workbench components and targeted TypeScript checks were exercised locally on invented fixtures. All 100 chapter calculators produced available outputs on a 1,200-session, eight-member invented fixture with every supplemental input supplied. Targeted controls check event timing, publication cutoffs, weight timing, action contamination, pivot confirmation and historical normalization. This is development evidence, not independent validation of every method or a predictive edge. Both app components executed the same chapter breadth calculation. This is not complete app, authentication, deployment or real-data validation.

This is source-branch research code, not full-app or live acceptance. The exact feature branch disables automatic Vercel deployment in its effective configuration; production and other branches are unchanged. No preview or production change is part of this source publication. In particular, the portal's package `prebuild` runs a DB migration. Its existing Vercel configuration bypasses that script, but this alone does not prove every route import or prerender is free of live database/provider access. Do not run `npm run build` against live credentials to test this feature.

## Calculation conventions

Empirical percentile ties count reference values less than or equal to the current value. The current reading is excluded wherever a prior-only reference is specified. Method 13 uses inclusive rolling lows: a close equal to the rolling minimum counts, including flat series. It does not claim a strict new-low breakout.

## Catalog packaging
`catalog.mjs` concatenates ten ordered100-recipe data modules. They contain the unchanged original1,000recipe objects, in the same order, with no runtime codec or regeneration. CLI, workbench and tests import this same module.

`ai-boundary.mjs` prepares an optional free-model explanation request only. It does not fetch, hold keys, authenticate caller policy, consume an atomic budget or establish working OpenRouter connectivity. Catalog/time/budget inputs are caller assertions pending a real authorized server adapter. Use `node test-ai-boundary.mjs` for offline boundary checks.
