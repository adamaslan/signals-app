# API Endpoints

All defined in [`src/signals_app/api/routes.py`](../../src/signals_app/api/routes.py).

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
