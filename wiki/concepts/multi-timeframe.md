# Multi-Timeframe Scoring & Alignment

Two related-but-distinct mechanisms exist for combining signal across
timeframes. They are not currently wired together into one response — see the
note at the bottom.

## 1. Weighted composite score (`scoring/mtf.py`)

`compute_multi_timeframe(symbol, dfs_by_timeframe)` in
[`scoring/mtf.py`](../../src/signals_app/scoring/mtf.py) runs the **full**
indicator → detect → confluence pipeline independently for each of 5
supported timeframes, then combines them:

```python
TIMEFRAME_WEIGHTS = {"1D": 0.10, "5D": 0.15, "1M": 0.25, "3M": 0.30, "6M": 0.20}
```

Shorter timeframes are deliberately down-weighted ("noisier"); 3M carries the
most weight. `composite_score` is the weighted average of each timeframe's
`ConfluenceResult.score` over timeframes that had enough data (≥20 bars).
`dominant_action` is the plurality vote of BUY/SELL/HOLD across timeframes,
weighted the same way (`_dominant_action()`).

`any_degraded` is true if any single timeframe's detection was degraded
(per [signal-detectors.md](signal-detectors.md)'s failure-count logic).

**Note**: `SignalOutput.matrix` is always `None` in the current
`/signals/{symbol}` route — `compute_multi_timeframe()` exists and is tested
but isn't called from the API today. It's available for a future
multi-timeframe endpoint or CLI use.

## 2. LLM signal matrix + alignment (`synthesis/mtf_llm.py`)

Separately, `build_timeframe_matrix()`
([`synthesis/mtf_llm.py`](../../src/signals_app/synthesis/mtf_llm.py)) calls
the LLM (or fallback) **per timeframe concurrently** via `asyncio.gather`,
producing a `TimeframeMatrix`
([`schemas/signal_output.py`](../../src/signals_app/schemas/signal_output.py)):

- `signals`: map of timeframe → `Signal` (each one LLM-synthesized or
  fallback, independently).
- `alignment_score`: fraction of signals agreeing with the majority
  direction (`alignment_score()` helper) — 1.0 means every timeframe agrees.
- `divergence_pattern`: classified via `classify_divergence()` by comparing
  short-timeframe bias (1D/5D) against long-timeframe bias (1M/3M/6M/1Y):
  - both bullish → `aligned_bullish`
  - both bearish → `aligned_bearish`
  - short bullish / long bearish → `short_bull_long_bear` ("potential
    bear-market rally; caution on entries")
  - short bearish / long bullish → `short_bear_long_bull` ("potential
    buy-the-dip opportunity")
  - anything else → `mixed`
- `divergence_interpretation`: the human-readable string for the pattern,
  from `DIVERGENCE_INTERPRETATIONS` in `config.py`.

This is the mechanism the frontend's `ConfluenceBar` "timeframe alignment"
bar visualizes (green >70%, amber 40–70%, red <40% — see
[concepts/signal-rendering.md](signal-rendering.md)), and what
`CouncilPanel`/`SignalMatrixRow` components are built to display.

## Caching

Per-timeframe TTLs in `TIMEFRAME_CACHE_TTL_SECONDS`: 1D/5D never cached (0),
1M cached 4h, 3M cached 12h, 6M/1Y cached 24h — an in-memory dict
(`_LOCAL_CACHE`) keyed `mtf:{ticker}:{timeframe}` in local mode. A degraded
(`ai_degraded=True`) signal is never cached, so a temporary LLM outage
doesn't get "stuck" as a cached fallback.
