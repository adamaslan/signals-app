# API Endpoints

Legacy routes are defined in [`src/signals_app/api/routes.py`](../../src/signals_app/api/routes.py); the versioned `/v1` surface is in [`v1.py`](../../src/signals_app/api/v1.py) (see below).

> **As of PR #21** the routes are a thin adapter over
> [`signals_app.service`](../../src/signals_app/service.py) — the pipeline
> logic moved there and the routes only translate `SignalsError` subclasses
> into HTTP statuses (`_raise_http`). The same `service` functions back the
> `signals` CLI. See
> [decisions/2026-08-30-service-seam-and-cli.md](../decisions/2026-08-30-service-seam-and-cli.md).

## `GET /signals/{symbol}`

Full L1–L5 pipeline for one ticker. See
[architecture/pipeline.md](../architecture/pipeline.md).

**Params**
| Name | Type | Default | Notes |
|---|---|---|---|
| `symbol` | path | — | Uppercased + stripped server-side |
| `period` | query | `3mo` (`DEFAULT_PERIOD`) | Must be in `VALID_PERIODS`: `15m,1h,4h,1d,5d,1mo,3mo,6mo,1y,2y,5y,10y,ytd,max` |
| `no_llm` | query bool | `false` | Skips LLM synthesis, returns confluence-derived rule-based signal instead |

**Response**: `SignalOutput` — see [concepts/signal-schema.md](../concepts/signal-schema.md).

**Errors** (mapped from `service.analyze`'s domain exceptions by `_raise_http`)
| Status | Cause |
|---|---|
| 400 | Invalid `period` (`InvalidPeriod`); fewer than 20 bars returned (`InsufficientData`) |
| 404 | Provider returned no data for the symbol (`SymbolNotFound`) — was 400 before PR #21 |
| 503 | yfinance / indicator / detection / confluence layer errored (`UpstreamUnavailable`) — was 500 before PR #21 |
| 500 | Anything else unhandled |

Notably, **LLM/synthesis failures do not 5xx** — `synthesize_single()` errors
are caught inside `service.analyze` and substituted with a fallback `Signal`,
tagged `feature_unavailable.append("synthesis_error")`.

## `GET /history/{symbol}`

Recent persisted runs for a ticker from the SQL DB, newest first. See
[architecture/backend.md](../architecture/backend.md#persistence).

**Params**
| Name | Type | Default | Notes |
|---|---|---|---|
| `symbol` | path | — | Uppercased + stripped |
| `limit` | query int | 50 | 1–200 |
| `offset` | query int | 0 | Pagination |

**Response**: `list[dict]` — each dict matches the frontend `HistoryEntry`
shape exactly (`RunRecord.to_dict()` in `db/ops.py` maps
`resolved_period → resolvedPeriod`, `direction → signal`,
`ai_degraded → aiDegraded`, etc.) so the frontend can consume it with zero
transformation.

**Errors**: 503 (`UpstreamUnavailable`) if the DB query itself fails — this
one *does* propagate, unlike the fire-and-forget write path in
`record_run()`, because a read failure here is the actual point of the
request.

## `GET /health`

Liveness probe. Returns `{"status": "ok"}`, no params, no failure modes
beyond the process not running at all. (Distinct from the richer
`signals health` CLI command / `service.health()`, which probes yfinance
reachability and reports which LLM provider is configured.)

## `/v1` — the integration API

Defined in [`src/signals_app/api/v1.py`](../../src/signals_app/api/v1.py), for
machine consumers (the portal's council grounding, the mobile RAG council's
vector store, scripts) rather than this repo's own UI. The unversioned routes
above are unchanged and keep their `{"detail": ...}` error shape.

| Route | Purpose |
|---|---|
| `GET /v1/signals/{symbol}` | `SignalOutput`, now with a deterministic `state` block (confluence, bias, action, RSI/ADX/ATR/MACD, close, as-of date) |
| `POST /v1/signals/batch` | Basket; partial success is a 200 with `ok` + `failed`. LLM batches capped lower than rule-based ones |
| `GET /v1/backtest/{symbol}` | Same body as `GET /backtest/{symbol}`; cached in-process ~6h |
| `POST /v1/backtest/batch` | Weighted-merged hit-rates across a basket |
| `GET /v1/brief/{symbol}` | Signal + state + hit-rates as one grounding brief (JSON, or `format=text` for a prompt block); a failed backtest is reported in `omitted`, not raised |
| `POST /v1/briefs` | Briefs for a basket |
| `POST /v1/rag/documents` | One vector-store document per ticker with a stable per-bar id; `shape=chroma` returns `{ids, documents, metadatas}` for `collection.upsert` |
| `GET /v1/history/{symbol}`, `POST /v1/scan`, `GET /v1/detectors` | Typed counterparts of the existing routes |
| `GET /v1/meta`, `GET /v1/health` | Versions/limits/auth flag; deep health (503 when yfinance is unreachable) |

- **Errors** are `{"error": {"type", "message"}}`; `InsufficientData` is 422 here
  (400 on the legacy routes).
- **Auth** is opt-in: when `SIGNALS_API_KEY` is set, every `/v1` route except
  `meta` and `health` needs `Authorization: Bearer` or `X-API-Key`. The legacy
  routes, including `POST /scan`, are not covered by it.
- Every response carries `X-Request-ID` (echoed or minted) and
  `X-Signals-Code-Version`.

Two fixes ship with it: the backtest now fetches daily bars (`fetch` maps 2y/5y
to weekly, ~105 bars, under the 200-bar warmup, so the default period failed
for every symbol), and `analyze` runs its blocking pipeline in a worker thread
(`synthesize_single` creates its own event loop, which raised inside the server
loop and silently degraded every HTTP/MCP call to the rule-based fallback).

## The `signals` CLI — same `service`, different surface

Since PR #21 the `signals` console script exposes the same `service`
functions: `signals analyze` / `backtest` / `history` / `detectors` /
`health` / `serve`. `--json` emits the identical `SignalOutput` payload the
route returns. `signals-analyze` is a deprecated shim that forwards to
`signals serve`. See
[decisions/2026-08-30-service-seam-and-cli.md](../decisions/2026-08-30-service-seam-and-cli.md).

## Not currently exposed

`compute_multi_timeframe()` (weighted composite across timeframes) and
`build_timeframe_matrix()` (LLM-per-timeframe + alignment/divergence) both
exist and are used internally/in tests, but neither has its own route yet —
see [concepts/multi-timeframe.md](../concepts/multi-timeframe.md). A future
`GET /signals/{symbol}/matrix` or similar would be the natural home.

## `POST /backtest/run`

Backtest a basket on the engine (every detector × every daily bar). Body:
`symbols` (1–25), `period` (default `2y`), `horizon_days` (1–60, default 20),
optional `focus` (`[{group: signal|category|strength, key}]`). Returns
`by_signal` / `by_category` / `by_strength` buckets — each with Wilson
`lower`/`upper` and a mix-weighted `baseline` — plus `up_rate` and, when
`focus` is given, a `verdict`. Read-only. Errors as `/signals`: 400 bad period,
otherwise per-symbol failures are reported in `symbols_failed`, not raised.
See [concepts/backtest-lab.md](../concepts/backtest-lab.md).

## `POST /backtest/suggest`

Body: `symbols` (1–100), `period`, optional `horizon_days`, `max_suggestions`
(1–20). Detects live signals (latest bar, no LLM) and returns
`hypotheses` — each a ready-to-POST `/backtest/run` spec with a `title`,
`rationale` and `kind` — plus `live_signals` per ticker.

## `POST /scan`

Manual real scan of ≤ 100 tickers, publishes to Supabase; the frontend's
"Run real scan" button. Local backend only.

