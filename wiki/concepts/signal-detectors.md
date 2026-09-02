# Signal Detectors

18 detector classes across 3 categories, all implementing the
`SignalDetector` Protocol ([`detection/base.py`](../../src/signals_app/detection/base.py)):

```python
class SignalDetector(Protocol):
    def detect(self, df: pd.DataFrame) -> list[MutableSignal]: ...
```

No inheritance required — any object with a matching `detect()` method
qualifies. Each detector reads the latest 1–2 rows of the indicator
DataFrame (output of `compute_indicators()`) and independently emits zero or
more `MutableSignal` objects: `signal` (label), `description`, `strength`
(a `SignalStrength` enum value), `category` (a `SignalCategory` enum value).

Full per-detector breakdown: [entities/detector-catalog.md](../entities/detector-catalog.md).

## Orchestration & robustness

`get_default_detectors()` in
[`detection/orchestrator.py`](../../src/signals_app/detection/orchestrator.py)
returns the fixed list of 18. `detect_all_signals(df)` runs each one via
`_run_detector_with_timeout()`, which wraps `detector.detect(df)` in a
`ThreadPoolExecutor(max_workers=1)` and calls `future.result(timeout=...)`.

Why a thread pool instead of `signal.SIGALRM`: SIGALRM is process-wide and
not thread-safe — concurrent async requests would stomp each other's timers,
and `signal.signal()` can only be called from the main thread anyway. A
per-call thread-pool future is clean and safe across asyncio tasks.

`DETECTOR_TIMEOUT_MS = 500` (config.py) is the per-detector budget.
`MAX_DETECTOR_FAILURES = 4`: once that many detectors time out or raise, the
returned `SignalList.degraded` becomes `True` and `.warnings` accumulates
strings like `detector_timeout:IchimokuDetector` or
`detector_error:VolumeSignalDetector:KeyError`. A single detector crashing
never takes down the request — degraded results still return a signal, just
flagged.

## Category 1 — Trend (9 detectors)

Source: [`detection/trend.py`](../../src/signals_app/detection/trend.py).

- `MovingAverageSignalDetector` — golden/death cross (50/200 SMA), price vs
  20 SMA crosses, and 10>20>50 SMA stack alignment (bullish/bearish).
- `ExpandedMACrossDetector` — the same cross logic but swept across all
  `MA_CROSS_PAIRS` (11 fast/slow SMA pairs from `indicators/grids.py`), so
  e.g. a 10/50 cross fires independently of the 50/200 golden cross.
- `TrendSignalDetector` — ADX-based: `STRONG UPTREND`/`STRONG DOWNTREND` when
  `ADX > ADX_TRENDING` (25.0), direction from price vs 50 SMA.
- `IchimokuDetector` — Tenkan/Kijun cross, price vs Kumo (cloud) position
  (above/below/inside), and cloud color (SpanA > SpanB = bullish "green
  cloud").
- `BollingerBandSignalDetector` — standard 20-period/2.0-stdev band touch
  (`AT LOWER BB`/`AT UPPER BB`, with a 1% tolerance).
- `BBExpansionDetector` — sweeps periods (10/20/30/50) × stdevs
  (1.5/2.0/2.5/3.0): price breaking outside the band, `%B` extremes (>1 or
  <0), and "riding the band" (2 consecutive closes outside).
- `PriceActionSignalDetector` — single-bar `LARGE GAIN`/`LARGE LOSS` when
  `|Price_Change| > LARGE_MOVE_PERCENT` (5.0%).
- `HLProximityDetector` — price within X% of N-bar high/low, swept across
  `HL_LOOKBACKS` × `HL_PROXIMITIES` from `indicators/grids.py`.
- `MADistanceExpandedDetector` — price extended >N% above/below key SMAs
  (`MA_DIST_PERIODS` × `MA_DIST_THRESHOLDS`) — a mean-reversion signal
  (far above SMA = bearish, far below = bullish).

## Category 2 — Momentum (6 detectors)

Source: [`detection/momentum.py`](../../src/signals_app/detection/momentum.py).

- `RSISignalDetector` — standard 14-period RSI: extreme oversold (<20),
  oversold (<30), overbought (>70).
- `MultiRSIDetector` — sweeps `RSI_PERIODS` × `RSI_OS_OB_LEVELS` (multiple
  period/threshold pairs), plus a "crossed 50" bull/bear signal per period.
- `MACDSignalDetector` — standard (12,26,9) MACD: signal-line cross and
  zero-line cross, both directions.
- `MultiMACDDetector` — same cross logic across `MACD_PARAMS` (non-standard
  fast/slow/signal triples), plus histogram sign-flip signals.
- `StochasticSignalDetector` — %K oversold (<20) / overbought (>80).
- `StochasticCrossDetector` — %K/%D cross specifically within the
  oversold (<30) or overbought (>70) zone — a stronger signal than a bare
  OB/OS reading.

## Category 3 — Volume (3 detectors)

Source: [`detection/volume.py`](../../src/signals_app/detection/volume.py).

- `VolumeSignalDetector` — volume vs 20-bar average: `VOLUME SPIKE 2X`,
  `EXTREME VOLUME 3X`.
- `VolumeDivergenceDetector` — the same spike check swept across
  `VOLUME_MA_PERIODS` (1.5x/2x/3x thresholds), plus a 10-bar
  price-vs-volume divergence check (price up + volume down = bearish
  divergence, and vice versa).
- `OBVCMFDetector` — three sub-checks: 20-bar OBV-vs-price divergence
  (accumulation/distribution), OBV crossing its own 20-period EMA, and
  Chaikin Money Flow (CMF) strong buying/selling (`|CMF| > 0.1`) plus
  zero-line crosses.

## Downstream

Raw signals feed [concepts/confluence-scoring.md](confluence-scoring.md)
(weighted bull/bear aggregation) and, per-timeframe, the composite scoring in
[concepts/multi-timeframe.md](multi-timeframe.md).

## Indicator warmup (200-period SMAs, MA-distance)

`compute_indicators()` always computes SMA/volume-MA for periods up to 200
(`indicators/grids.py`'s `MA_PERIODS_EXTENDED`/`VOLUME_MA_PERIODS`), and
several detectors here key directly off them — `MovingAverageSignalDetector`
(golden/death cross), `MADistanceExpandedDetector` (`>N% ABOVE/BELOW {p}SMA`).
Those are only meaningful with ≥200 bars of history. `DataFetcher.fetch()`
transparently widens short daily-interval requests (e.g. the default `"3mo"`
≈63 bars → `"1y"` ≈252 bars) before calling yfinance so this floor is met
without callers changing what period they ask for; `score_data_quality`
independently flags `indicator_warmup_short:{n}<200` whenever a df still
falls short. See PR #24 / `docs/universe-scan-improvements.md` — the
regression this closes: with `min_periods=1` on the rolling window, an
under-warmed SMA_200 silently averaged over whatever bars existed instead of
returning `NaN`, so fabricated long-window signals drove real published
SELL/BUY calls on the 2026-09-01 universe scan.
