# Detector Catalog

One row per detector class. All implement `SignalDetector.detect(df) -> list[MutableSignal]`.
See [concepts/signal-detectors.md](../concepts/signal-detectors.md) for the
narrative version and orchestration details.

## Trend — `src/signals_app/detection/trend.py`

| Class | Category | Signal names emitted | Trigger |
|---|---|---|---|
| `MovingAverageSignalDetector` | MA_CROSS, MA_TREND | GOLDEN CROSS, DEATH CROSS, PRICE ABOVE/BELOW 20 MA, MA ALIGNMENT BULLISH/BEARISH | 50/200 SMA cross (needs >200 bars); price vs 20 SMA cross; 10>20>50 SMA stack |
| `ExpandedMACrossDetector` | MA_CROSS | `{fast}/{slow} MA BULL/BEAR CROSS` (or GOLDEN/DEATH CROSS for the 50/200 pair) | Cross of any of 11 SMA pairs in `MA_CROSS_PAIRS` |
| `TrendSignalDetector` | TREND | STRONG UPTREND / STRONG DOWNTREND | `ADX > 25.0`, direction from Close vs SMA_50 |
| `IchimokuDetector` | ICHIMOKU | ICHIMOKU TK BULL/BEAR CROSS, PRICE ABOVE/BELOW/INSIDE KUMO, BULLISH/BEARISH KUMO | Tenkan/Kijun cross; Close vs cloud top/bottom; SpanA vs SpanB |
| `BollingerBandSignalDetector` | BOLLINGER | AT LOWER BB, AT UPPER BB | Close within 1% of standard 20/2.0 band edge |
| `BBExpansionDetector` | BB_BREAKOUT | ABOVE UPPER/BELOW LOWER `BB(period,sd)`, `{label} %B > 1 / < 0`, `{label} RIDING UPPER BAND` | Swept across periods (10/20/30/50) × stdevs (1.5/2.0/2.5/3.0) |
| `PriceActionSignalDetector` | PRICE_ACTION | LARGE GAIN, LARGE LOSS | `\|Price_Change\| > 5.0%` in one bar |
| `HLProximityDetector` | RANGE | `WITHIN {n}% OF {lb}b HIGH/LOW` | Close within proximity thresholds of N-bar high/low |
| `MADistanceExpandedDetector` | MA_DISTANCE | `>{t}% ABOVE/BELOW {period}SMA` | Price extended beyond distance thresholds from key SMAs |

## Momentum — `src/signals_app/detection/momentum.py`

| Class | Category | Signal names emitted | Trigger |
|---|---|---|---|
| `RSISignalDetector` | RSI | RSI EXTREME OVERSOLD, RSI OVERSOLD, RSI OVERBOUGHT | Standard 14-period RSI vs 20/30/70/80 thresholds |
| `MultiRSIDetector` | RSI | `RSI{period} OVERSOLD/OVERBOUGHT (<>{level})`, `RSI{period} CROSSED 50 BULL/BEAR` | Swept across `RSI_PERIODS` × `RSI_OS_OB_LEVELS`; 50-line cross |
| `MACDSignalDetector` | MACD | MACD BULL/BEAR CROSS, MACD ZERO CROSS UP/DOWN | Standard (12,26,9) MACD signal-line and zero-line cross |
| `MultiMACDDetector` | MACD | `MACD({f},{s},{sig}) BULL/BEAR CROSS`, `... ZERO BULL/BEAR`, `... HIST BULL/BEAR` | Non-standard param sets from `MACD_PARAMS`; histogram sign flip |
| `StochasticSignalDetector` | STOCHASTIC | STOCHASTIC OVERSOLD, STOCHASTIC OVERBOUGHT | %K vs 20/80 thresholds |
| `StochasticCrossDetector` | STOCHASTIC | STOCH BULL CROSS (OVERSOLD), STOCH BEAR CROSS (OVERBOUGHT) | %K/%D cross specifically within OS(<30)/OB(>70) zone |

## Volume — `src/signals_app/detection/volume.py`

| Class | Category | Signal names emitted | Trigger |
|---|---|---|---|
| `VolumeSignalDetector` | VOLUME | VOLUME SPIKE 2X, EXTREME VOLUME 3X | Volume vs 20-bar MA ratio > 2x / 3x |
| `VolumeDivergenceDetector` | VOLUME | `VOLUME SPIKE >1.5x/2x/3x (MA{n})`, VOLUME BULLISH/BEARISH DIVERGENCE (10b) | Swept across `VOLUME_MA_PERIODS`; 10-bar price-vs-volume divergence |
| `OBVCMFDetector` | OBV_CMF | OBV BULLISH/BEARISH DIVERGENCE, OBV BULL/BEAR CROSS EMA, CMF STRONG BUYING/SELLING, CMF CROSSED POSITIVE/NEGATIVE | 20-bar OBV vs price divergence; OBV vs its 20-EMA; CMF vs ±0.1 and zero-line |

## Orchestrator

`get_default_detectors()` in
[`detection/orchestrator.py`](../../src/signals_app/detection/orchestrator.py)
returns all 18 in this fixed order (trend, then momentum, then volume).
`detect_all_signals()` runs them with per-detector timeout isolation — see
[concepts/signal-detectors.md](../concepts/signal-detectors.md#orchestration--robustness).
