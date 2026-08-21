# API Endpoints

All defined in [`src/signals_app/api/routes.py`](../../src/signals_app/api/routes.py).

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

**Errors**
| Status | Cause |
|---|---|
| 400 | Invalid `period`; fetch `ValueError` (bad symbol); fewer than 20 bars returned |
| 500 | Unhandled fetch/indicator/detection/confluence error |

Notably, **LLM/synthesis failures do not 500** — `synthesize_single()` errors
are caught in the route itself and substituted with a fallback `Signal`,
tagged `unavailable.append("synthesis_error")`.

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

**Errors**: 500 if the DB query itself fails (this one *does* propagate —
unlike the fire-and-forget write path in `record_run()`, a read failure here
is the actual point of the request, so it can't be silently swallowed).

## `GET /health`

Liveness probe. Returns `{"status": "ok"}`, no params, no failure modes
beyond the process not running at all.

## Not currently exposed

`compute_multi_timeframe()` (weighted composite across timeframes) and
`build_timeframe_matrix()` (LLM-per-timeframe + alignment/divergence) both
exist and are used internally/in tests, but neither has its own route yet —
see [concepts/multi-timeframe.md](../concepts/multi-timeframe.md). A future
`GET /signals/{symbol}/matrix` or similar would be the natural home.
