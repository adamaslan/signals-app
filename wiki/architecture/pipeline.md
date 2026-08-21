# Signal Pipeline (L1–L5)

Every `GET /signals/{symbol}` request runs the same five-stage pipeline,
defined in [`src/signals_app/api/routes.py`](../../src/signals_app/api/routes.py)
`get_signals()`.

```
L1 Fetch OHLCV → L2 Compute indicators → L3 Detect signals →
L4 Confluence rank (+ MTF) → L5 LLM synthesis
```

## L1 — Fetch

`DataFetcher.fetch(symbol, period)` ([`data/fetcher.py`](../../src/signals_app/data/fetcher.py))
pulls OHLCV via yfinance. `period` must be one of `VALID_PERIODS` in
[`config.py`](../../src/signals_app/config.py) (`15m` through `max`). A
`ValueError` from the fetcher becomes an HTTP 400; any other exception is a 500.
If fewer than 20 bars come back, the route also 400s
("Insufficient data for {symbol}").

## L2 — Compute indicators

`compute_indicators(df_raw)` in
[`indicators/compute.py`](../../src/signals_app/indicators/compute.py) adds
every indicator series the detectors need: SMAs/EMAs (multiple periods), RSI
(multiple periods), MACD (multiple param sets), Bollinger Bands (multiple
period/stdev combos), Stochastic, ADX, ATR, Ichimoku, OBV/CMF, volume MAs,
high/low lookback bands, and MA-distance percentages. See
[concepts/signal-detectors.md](../concepts/signal-detectors.md) for which
detector consumes which columns.

## L3 — Detect signals

`detect_all_signals(df)` in
[`detection/orchestrator.py`](../../src/signals_app/detection/orchestrator.py)
runs all 18 detectors from `get_default_detectors()`. Each detector runs in
its own `ThreadPoolExecutor(max_workers=1)` with a
`DETECTOR_TIMEOUT_MS` (500ms) budget — one slow/failing detector cannot crash
or block the others. If `failure_count >= MAX_DETECTOR_FAILURES` (4), the
returned `SignalList.degraded` is `True` and `feature_unavailable` on the
final response gets `"detection_degraded"`. See
[concepts/signal-detectors.md](../concepts/signal-detectors.md).

## L4 — Confluence rank + multi-timeframe

`ConfluenceRanker.rank_signals()`
([`scoring/confluence.py`](../../src/signals_app/scoring/confluence.py))
turns the raw signal list into a `ConfluenceResult`: a `[-1, 1]` `score`, a
`bias` (bullish/bearish/neutral), a `confidence_label` (HIGH/MEDIUM/LOW), and
a rule-based `action` (BUY/SELL/HOLD). See
[concepts/confluence-scoring.md](../concepts/confluence-scoring.md) for the
exact weighting formula.

`compute_multi_timeframe()`
([`scoring/mtf.py`](../../src/signals_app/scoring/mtf.py)) is a separate
weighted-composite pass across timeframes (1D/5D/1M/3M/6M), used by
`build_timeframe_matrix()` in synthesis — not currently wired into the single-
symbol route's `matrix` field (`SignalOutput.matrix` is always `None` today;
see [ops/known-issues.md](../ops/known-issues.md)).

## L5 — LLM synthesis

`synthesize_single()`
([`synthesis/mtf_llm.py`](../../src/signals_app/synthesis/mtf_llm.py)) builds
a feature dict (confluence score/bias/action + current RSI/MACD/ADX/Close/
Volume/ATR/Price_Change), formats it into `PROMPT_TEMPLATE`, and calls
OpenRouter first, Gemini second, rule-based fallback last. See
[concepts/llm-synthesis.md](../concepts/llm-synthesis.md).

## Persistence (fire-and-forget)

After synthesis, `record_run()`
([`db/ops.py`](../../src/signals_app/db/ops.py)) writes the run (ticker,
period, direction, confidence, degraded flags) to the SQL DB. Failures here
are logged, not raised — a DB outage never fails the API response. See
[architecture/backend.md](backend.md#persistence).

## Response shape

`SignalOutput` ([`schemas/signal_output.py`](../../src/signals_app/schemas/signal_output.py)):
`ticker`, `signal` (the primary `Signal`), `matrix` (currently always `None`
for this endpoint), `feature_unavailable` (list of degraded-feature tags), and
`schema_version`.
