# Backtest Lab and Suggested Backtests

Frontend-driven engine backtests, plus engine-proposed hypotheses to test.
Shipped on `feat/frontend-backtests` (2026-09-25).

## Two different backtests — don't conflate them

| | Universe backtest ([local-universes](local-universes.md)) | Backtest Lab (this page) |
|---|---|---|
| Source | Supabase `detector_outcomes` RPCs | Engine replay: every detector × every daily bar |
| Covers | Signals that were *published* | Every directional detector output, published or not |
| Runs where | Anywhere (static site OK) | Local backend only (`next dev` + `scripts/run_local.sh`) |

Testing a hypothesis needs the second: a claim about a signal that never
cleared the publish gate has no rows in `detector_outcomes`.

## Engine

- `backtests/engine.py` — `score_historical_signals` now also returns
  `by_signal` (detector signal name), a `baseline` bucket (scored bars that
  rose over the horizon) and per-bucket `bullish` counts. `bucket_baseline`
  turns those into a **mix-weighted chance rate**: a bullish call hits by
  chance with probability `up_rate`, a bearish one with `1 - up_rate`.
  Judging against a flat 50% credits market beta as skill.
- `service.backtest` always uses daily bars (`fetch_daily_history`).
  `fetch()` maps `2y`/`5y` to weekly bars, which starved the 200-bar warmup
  and made `horizon_days` mean weeks.
- `signals_app/hypotheses.py` — pure module. `suggest_hypotheses` turns the
  signals live on each ticker's latest bar into runnable specs; 
  `evaluate_hypothesis` judges a finished backtest.
- `service.suggest_backtests` / `service.run_hypothesis` do the I/O.

## Hypothesis kinds

`cluster` (one signal live on several tickers), `single` (a ticker's
strongest signal on its own history), `conflict` (bull vs bear on one
ticker), `category` (the family driving the run), `strength` (is STRONG
earned vs plain?). Clusters are capped and kinds interleaved so a correlated
basket doesn't yield eight near-identical cluster claims.

## Verdicts

Per focus bucket, with Wilson 95% bounds and the bucket's own chance rate:
`supported` (lower bound > chance), `contradicted` (upper bound < chance),
`inconclusive` (straddles, or fewer than 30 calls), `no_data`. A multi-focus
hypothesis takes its headline from the most decisive focus.

## Routes and caps

`POST /backtest/run` (≤ 25 tickers, `MAX_MANUAL_BACKTEST_SYMBOLS`) and
`POST /backtest/suggest` (≤ 100). See [api-endpoints](../entities/api-endpoints.md).

## Frontend

`/backtest/` (query-param page, like `/universe/`), a Suggested backtests
panel on the universe editor and signal page. `web/src/lib/backend.ts` is the
one place that builds local-backend URLs: rewrite sources are
basePath-relative and `trailingSlash: true` 308s slashless POSTs, so the old
bare `fetch("/api/scan")` 404'd — fixed for `triggerUniverseScan` too.

## Known limits

Suggestions read the latest bar with rule-based detectors only (no LLM, no
DB). Replays are synchronous CPU work, hence the 25-ticker cap. Hit-rates
pool overlapping windows (consecutive bars share forward returns), so
intervals are somewhat optimistic; treat "supported" as a lead, not proof.
