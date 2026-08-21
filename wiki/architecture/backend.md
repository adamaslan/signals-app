# Backend Architecture

Python 3.12, FastAPI, packaged via `pyproject.toml` (`src/` layout, console
script `signals-analyze`).

## Directory map

```
src/signals_app/
├── api/            main.py (app factory), routes.py (all endpoints)
├── config.py       every threshold/constant + Settings dataclass
├── data/fetcher.py yfinance OHLCV fetch
├── indicators/     compute.py, grids.py (parameter sweeps), pivots.py, divergence.py
├── detection/      base.py (Protocol + orchestration), momentum.py, trend.py, volume.py, orchestrator.py
├── scoring/         confluence.py, mtf.py, relative_strength.py
├── synthesis/       mtf_llm.py (LLM call + fallback + cache)
├── schemas/          signal_output.py (Pydantic contract)
├── db/                models.py, ops.py, session.py
└── utils/safety.py
```

## Config (`config.py`)

Every magic number in the pipeline is a named `Final` constant here — RSI
thresholds, MACD periods, ADX trending threshold, volume spike ratios,
confluence buy/sell thresholds, detector timeout/failure budget, LLM timeouts
and circuit-breaker params. See [concepts/confluence-scoring.md](../concepts/confluence-scoring.md)
and [concepts/signal-detectors.md](../concepts/signal-detectors.md) for how
these get used.

`Settings` (frozen dataclass, `get_settings()`) reads env vars:
`SIGNALS_ENV` (local/cloud), `GEMINI_API_KEY`, `OPENROUTER_API_KEY` (+ model),
`DATABASE_URL`, `OUTPUT_DIR`, `LOG_LEVEL`. `Settings.llm_provider` resolves to
`"openrouter"` (priority), `"gemini"`, or `"none"`. `validate()` requires
`DATABASE_URL` and an LLM key when `env == "cloud"` — local mode has no hard
requirements.

## API surface

Three endpoints, all in [`api/routes.py`](../../src/signals_app/api/routes.py):

| Endpoint | Purpose |
|---|---|
| `GET /signals/{symbol}` | Full L1–L5 pipeline. Params: `period` (default `3mo`), `no_llm` (bool). |
| `GET /history/{symbol}` | Past runs for a ticker from the SQL DB, newest first. Params: `limit` (1–200, default 50), `offset`. |
| `GET /health` | Liveness probe, returns `{"status": "ok"}`. |

Full pipeline detail: [architecture/pipeline.md](pipeline.md).

## Persistence

Two independent stores, intentionally not shared:

- **Backend SQL DB** (`db/models.py`, `db/ops.py`, `db/session.py`) — a
  `signal_runs` table (SQLAlchemy ORM, async via `aiosqlite` locally or
  `asyncpg` against Postgres/Neon in cloud mode). Composite index on
  `(ticker, ts)` since every history query filters by ticker then sorts by
  time. `record_run()` is called fire-and-forget after each `/signals`
  request; `get_ticker_history()` backs `/history/{symbol}`.
- **Frontend Dexie/IndexedDB** — entirely separate, browser-local, described
  in [architecture/frontend.md](frontend.md#local-first-data-model). The two
  schemas are shaped to match (`RunRecord.to_dict()` uses the same field names
  as the frontend's `HistoryEntry`) so `/history` responses can be consumed
  directly by the web client, but nothing keeps them in sync automatically —
  they're two independent logs of the same kind of event.

## Robustness patterns worth knowing

- **Per-detector isolation**: each of the 18 detectors runs in its own
  single-worker thread pool with a wall-clock timeout
  (`_run_detector_with_timeout` in `detection/base.py`). `SIGALRM` was
  rejected because it's process-wide and unsafe across concurrent async
  requests.
- **LLM fallback chain**: OpenRouter → Gemini → rule-based, never a bare
  exception. See [concepts/llm-synthesis.md](../concepts/llm-synthesis.md).
- **Fire-and-forget persistence**: a DB write failure is logged
  (`logger.warning`) but never raised — the API response always reflects
  pipeline success/failure, not persistence success/failure.
