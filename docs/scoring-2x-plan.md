# Scoring: a plan twice as good as the six-item fix list

**Date:** 2026-09-23 (verified against code and updated the same day, see §0)
**Builds on:** [confluence-confidence-explainer.html](confluence-confidence-explainer.html) and the six-item fix list from the same session (category caps, per-detector calibration, fixed ceiling, continuous data quality, learned weights, MTF disagreement penalty).
**Code referenced:** `src/signals_app/scoring/confluence.py`, `scoring/calibration.py`, `scoring/mtf.py`, `scoring/relative_strength.py`, `detection/{trend,momentum,volume}.py`, `indicators/compute.py`, `indicators/grids.py`, `data/fetcher.py`, `scanner.py`, `backtests/engine.py` (repo root, not under `src/`), `scripts/calibrate.py`

---

## TL;DR

The six-item list makes the current scorer less wrong. It does not change what the scorer is trying to predict, and it gives no way to tell whether a change helped. This plan changes four things:

1. **Measure first.** Record a baseline before touching `confluence.py`. *Updated:* the baseline is roughly zero (§0.2), so "double the IC" is not a usable target. Use an absolute bar instead (§2).
2. **Fix the label.** `backtests/engine.py` counts a hit when the direction matches the *raw sign* of the forward return. In a rising market most bullish signals "hit" whether or not they carry any information. *Verified:* bullish-bias bars hit 59% on raw return against a 57.6% base rate, and only 46% on excess return.
3. **Predict a probability and rank the universe.** Output a calibrated P(outperform) and a cross-sectional rank each day.
4. **Train on history now.** The backtest engine already replays the detectors over history, so a labeled dataset exists today.

**New since verification:** before any of that, there is a **P-1 phase of implementation bugs** (§0.3). They corrupt the features any model would learn from: direction-less signals counted as bullish, fake 200-day averages on 63-bar windows, one market fact emitted as up to 15 votes, and a detector that votes both ways on the same bar. Fixing them is cheap and should come first. Otherwise the P0 baseline measures bugs rather than signal quality.

---

## 0. Verification against the implementation (2026-09-23)

### 0.1 Plan claims vs. code

| Plan claim | Status | What the code actually does |
|---|---|---|
| `backtests/engine.py:74-86` labels a hit by raw sign | ✅ Confirmed | `hit = forward_return > 0` / `< 0` at lines 84/86. The Supabase path uses the same raw sign (`db/calibration_store.py:65`). |
| Calibration is per `(detector, strength)` (old item 2) | ❌ Worse than stated | Calibration is keyed **only by strength string** (`"BULLISH"`, `"EXTREME BULLISH"`…). All 18 detectors share one hit rate per strength. `calibration/strength_hit_rates.json` has 6 keys in total. |
| The 1.00 ceiling exists "because the score is absolute" | ⚠️ Partly | The direct cause is the denominator: `score = (bull − bear) / (bull + bear + 0.1·neutral)` (`confluence.py:197-198`). The denominator counts only *signals that fired*, so two agreeing signals score exactly 1.00. Measured on the live 63-bar window, 3.8% of symbol-days hit \|score\| ≥ 0.99. On a 954-symbol universe that is about 36 symbols a day, consistent with "dozens". |
| `TIMEFRAME_WEIGHTS` 1Y = 0.20, 1D = 0.05 | ✅ Confirmed, **uncommitted** | These are uncommitted working-tree edits on `feat/trigger-universe-scan` (8 timeframes; committed `main` has 5). |
| Publication gate `\|score\| ≥ 0.15`, ≥ 2 signals, `config.py:220-222` | ✅ Confirmed, **uncommitted** | Loosened 2026-09-22 from 0.35 / 3 / 0.7. Note: `PUBLISH_MIN_SIGNALS` counts *all* signals including neutral and fan-out duplicates. The median bar has 14, so the "≥ 2" condition never binds. |
| `score_historical_signals()` walks every bar with `MIN_HISTORICAL_LOOKBACK` warmup | ⚠️ Split across files | The walk is in `detection/historical.py::scan_historical` (warmup 200). `backtests/engine.py::score_historical_signals` only scores it. Both run in pure Python, one detector call per bar. |
| "954 × 2,500 bars ≈ 2M rows fits on a laptop" | ⚠️ Memory yes, compute no | `scan_historical` runs 18 detectors per bar in Python. The probe below did about 5k bar-evaluations in minutes, so 2M would take hours. **Cheaper route:** the continuous features in §5a are *already columns* of `compute_indicators()`, computed once per symbol, vectorized. Build the dataset from the indicator frame plus labels. Replay detectors only for the binary-firing columns. |
| §4 "rank, don't score" is new work | ⚠️ Partly exists | `scoring/relative_strength.py` (381 lines, ported from gcp3) already computes `rank_in_universe`, but **nothing imports it**. Reuse it for `rank_pct` instead of starting from scratch. |
| §7 stack per-timeframe model outputs | ❌ Premise broken | The timeframes are not independent views. See bug B7: 1D and 5D never score, and 1M–1Y are the same daily bars. |

### 0.2 Measured baseline (probe, not the P0 harness)

**Setup:** 10 tickers (SPY, AAPL, NVDA, XOM, TQQQ, KO, JPM, TSLA, PFE, INTC). The last ~3 years of daily bars, every 3rd bar (249 dates, 2,490 symbol-days). 5-day forward return, with excess measured against SPY. The current `ConfluenceRanker` was run two ways: on the **live window** (63 bars, matching `DEFAULT_PERIOD="3mo"`) and on **full history**. Scripts were throwaway, run in the `signals-app` mamba env.

| Metric | Live 63-bar | Full history |
|---|---|---|
| Signals per bar (median / p90 / max) | 14 / 36 / 88 | 19 / 44 / 90 |
| Cross-sectional rank IC vs 5d excess (mean, t) | **0.014, t = 0.62** | **0.001, t = 0.04** |
| Bullish-bias hit rate, raw vs **excess** | 0.590 vs **0.458** | 0.582 vs **0.434** |
| Base rate of an up week | 0.576 | 0.576 |
| Direction hit on excess: HIGH / MEDIUM / LOW | **0.483** / 0.451 / 0.467 | **0.367** / 0.462 / 0.473 |
| Mean 5d excess: BUY / HOLD / SELL | +0.29% / **+0.41%** / +0.09% | +0.19% / **+0.49%** / −0.02% |
| Bars carrying ≥ 1 direction-less "bullish" vote (bug B1) | 49% | 42% |

**How to read this:**
- There is **no measurable skill** at a 5-day horizon. The apparent 59% hit rate is market beta, which confirms §3.
- **HIGH confidence is not better than LOW**, and on full history it is clearly worse.
- **HOLD outperforms BUY.**
- **Caveats:** 10 names is a tiny cross-section for rank IC. 2023–26 is mostly one bull regime. The 5-day windows overlap. Treat these numbers as *direction-of-effect only*. The P0 harness must redo this on the universe with purged CV.

### 0.3 Implementation bugs (new phase P-1: fix before measuring anything)

Each bug has a file reference and a concrete fix. B1–B5 change what the detectors say. B6–B9 change how the votes are combined or calibrated.

| # | Bug | Where | Evidence | Fix |
|---|---|---|---|---|
| **B1** | **Direction-less strengths are counted as bullish votes.** `SIGNIFICANT` (+1.0), `VERY_SIGNIFICANT` (+1.5) and `TRENDING` (+1.0) sit on the positive side of `_STRENGTH_BULL_WEIGHT`. They are emitted by volume spikes (a 3× capitulation-day spike votes *bullish*) and by `TrendSignalDetector`, whose **"STRONG DOWNTREND" is a bullish vote**. The backtest skips them (`"BULLISH" in strength` is false), so they are never calibrated either. | `confluence.py:37-39`, `volume.py:69-134`, `trend.py:237-238` | 49% of live bars carry at least one of these. "STRONG DOWNTREND" fired 340 times in the probe, each one a +1.0 bull vote. | Map these three strengths to 0 in `_STRENGTH_BULL_WEIGHT`. `TrendSignalDetector` should emit `BULLISH`/`BEARISH` by `Plus_DI` vs `Minus_DI` (both already computed). Volume spikes should not vote. Make them a multiplier (e.g. ×1.25) on the same bar's price-direction votes, or emit them signed by the sign of `Close − prev Close`. |
| **B2** | **Fake long averages on short windows.** `SMA_*` and `Volume_MA_*` use `rolling(min_periods=1)`. On the 63-bar live window, `SMA_100` and `SMA_200` are just the 63-bar mean. `ExpandedMACrossDetector` has no length guard, so it emits **fake GOLDEN/DEATH CROSS** (50/200), plus 20/100, 20/200, 10/100 and 50/100 crosses. Because `SMA_100 ≡ SMA_200` below 100 bars, the 50/100 and 50/200 crosses fire as a pair. `MADistance` and `Dist_SMA_200` measure distance from a 63-bar mean. (`MovingAverageSignalDetector` *does* guard with `len(df) <= 200`, so the codebase is inconsistent.) | `compute.py:65,192`, `trend.py:150-205` | Live runs on 63 bars, while backtest and calibration run with 200-bar warmup. **The detectors being calibrated are not the detectors running live.** | Use `min_periods=period` for SMA and volume MAs. The detectors already skip NaN via `_sf`. Also fetch ≥ 1y (≥ 252 bars) for the live scan and score only the last bar. That aligns live with backtest (see B8). |
| **B3** | **Nested-grid fan-out: one fact becomes many votes.** Thresholds are cumulative, not exclusive. `HLProximityDetector` covers 5 lookbacks × 3 proximities, so "at a 52-week high" yields up to 15 signals, 5 of them `EXTREME_BULLISH` (3.0 each). `MADistanceExpandedDetector` covers 6 periods × 4 thresholds, so up to 24. `MultiRSIDetector` covers 5 periods × 3 level pairs. `BBExpansionDetector` uses a nested 4 × 4 band grid (above 3σ implies above 2.5σ, 2σ and 1.5σ). | `trend.py:513-619,384-470`, `momentum.py:94-155`, `grids.py` | In the median bar, the single busiest detector emits 5 signals (p90 = 15, max = 38). **Three detectors (BBExpansion 24%, MADistance 16%, HLProximity 16%) produce 56% of all vote weight.** | Collapse each detector to **one signal per concept**: the most extreme level reached, with its level carried as a continuous `value` field. For example: HLProximity keeps the single nearest (lookback, proximity) pair, MADistance keeps the largest period breached, MultiRSI keeps one oversold/overbought vote plus one 50-cross vote. |
| **B4** | **BBExpansion votes both ways on the same bar.** `close > upper` is mathematically identical to `%B > 1`. Each breach emits `EXTREME_BULLISH` (+3) *and* `BEARISH` (−1), and the same happens in reverse for the lower band. That is a net +2 momentum vote per band, ×16 bands. | `trend.py:424-456` | Probe: "ABOVE UPPER BB" and "%B > 1" both fired exactly 2,806 times; "BELOW LOWER BB" and "%B < 0" both fired 1,525 times. | Pick one interpretation (breakout *or* mean-reversion), preferably chosen by measured lift, and delete the other branch. `BollingerBandSignalDetector` already covers the mean-reversion reading. |
| **B5** | **MACD cross is counted twice.** Histogram = MACD − signal, so "HIST turned positive" is the same event as "MACD crossed signal". `MultiMACDDetector` emits both (STRONG + normal = 2.5 + 1.0 per param set, plus category bonuses). | `momentum.py:262-313` | Probe: BULL CROSS and HIST BULL both n = 304; BEAR CROSS and HIST BEAR both n = 321. | Delete the histogram-flip branch. Where histogram information matters, add *histogram slope* (a different event). |
| **B6** | **Score saturates with two signals.** See §0.1. | `confluence.py:197-198` | 3.8% of live days at \|score\| ≥ 0.99. | Add a pseudo-count: `score = (bull − bear) / (bull + bear + K)` with K ≈ 4 (tune on the harness). Two agreeing votes then give ~0.4, not 1.0. This is the "fixed ceiling" item, and it is a one-line change. |
| **B7** | **"Multi-timeframe" is mostly one timeframe counted several times.** `fetcher.PERIOD_TO_INTERVAL` maps `1d`/`5d` → daily bars, returning 1 and 5 bars. `score_single_timeframe` drops anything under 20 bars, so **1D and 5D never score**, and their weights are dead. `1mo`, `3mo`, `6mo` and `1y` are **all daily bars ending on the same bar**, so once indicators warm up they produce nearly identical signals. Only `5y` (weekly) and `max` (monthly) are genuinely different. | `fetcher.py:31-46`, `mtf.py:150`, `scanner.py:181-189` | The code path proves it. The "aligned across timeframes" narrative in `DIVERGENCE_INTERPRETATIONS` is mostly self-agreement. | Redefine the timeframes as **bar intervals**: daily (≥ 252 bars), weekly (≥ 104 bars, from `5y`), monthly (from `max`), plus an optional intraday one (`1h` → 5m bars) if 1D is wanted. Each should have enough bars to warm up. Drop `1M`/`3M`/`6M`/`1Y` as separate votes. This also cuts the matrix's 8 LLM calls to 3–4. |
| **B8** | **Calibration runs on the wrong bars by default.** `scripts/calibrate.py --period` defaults to `2y`, and the fetcher maps `2y` → **weekly** bars (about 104). That is ≤ `MIN_HISTORICAL_LOOKBACK + horizon` (205), so every symbol is skipped. With `5y` it calibrates on weekly bars with a 5-*week* horizon, then applies those rates to daily live signals. | `calibrate.py:94`, `fetcher.py:41-42` | Code read. The committed `strength_hit_rates.json` has `EXTREME BULLISH = 0.637`, which the live path turns into HIGH. In the probe, the detectors producing most `EXTREME_BULLISH` votes (HLProximity near-high, BB breakout) hit **0.365–0.48 on excess**. | Fetch daily bars explicitly (`interval="1d"`, `period="10y"`) in calibration, independent of the period→interval map. Key buckets by **(detector, signal family)**, not strength. Store **lift over the bucket's base rate** on the §3 excess label, not an absolute hit rate. |
| **B9** | **The calibration override ignores score magnitude.** If the average hit rate of the winning side is ≥ 0.60, `confidence_label = "HIGH"`, even for a score of 0.11. That rate is itself mostly beta (B8), so HIGH mostly means "bullish in a bull market". | `confluence.py:222-232` | Probe: HIGH is not more accurate than LOW. | Until §4's `p_outperform` exists, require *both* \|score\| ≥ 0.55 *and* a calibrated lift > 0. Remove the path that promotes to HIGH on hit rate alone. |

Minor issues, fix when touched:

- `VolumeDivergenceDetector` compares **single-day** volume at bar −1 against bar −10. That is close to a coin flip and fires on about half of all bars. Compare the 10-day average against the prior 10-day average instead.
- `VolumeSignalDetector` (MA20) duplicates `VolumeDivergenceDetector`'s MA20 spike.
- The `1.5×` spike against a 5-bar mean is common, so it adds noise.
- `service.backtest` defaults to `DEFAULT_PERIOD` (63 bars), which is always below the 205-bar warmup, so a default call always errors.

---

## 0.4 Where the signals are weak, and how to firm up confluence

The table shows direction-adjusted results from the same probe on the live window, 5-day horizon. **Excess hit** = share of firings where the stock beat SPY in the signal's direction (coin flip = 0.50). Families with n ≥ 40 only. Small-sample caveats from §0.2 apply.

**Weakest (hit < 0.45 on excess, or negative direction-adjusted excess). Candidates to zero-weight or regime-gate:**

| Signal family | Detector | Vote | n | Excess hit | Mean dir-adj excess |
|---|---|---|---|---|---|
| Within 1% of N-bar high | HLProximity | EXTREME_BULLISH (3.0) | 893 | **0.365** | +11 bp |
| Stoch bear cross (overbought) | StochasticCross | STRONG_BEARISH | 179 | 0.369 | −58 bp |
| RSI overbought | RSISignal / MultiRSI | BEARISH | 244 / 2,765 | 0.381 / 0.401 | −75 / −51 bp |
| MACD(x,y,z) zero cross | MultiMACD | ±1.0 | 116 / 110 | 0.397 / 0.391 | −88 / −86 bp |
| Within 2–5% of N-bar high | HLProximity | BULLISH | 4,068 | 0.415 | +14 bp |
| BB %B > 1 (overbought) | BBExpansion | BEARISH | 2,806 | 0.421 | −40 bp |
| **> N% above SMA** | MADistance | BEARISH | **5,863** (most frequent signal) | 0.444 | **−82 bp** |
| MA alignment bearish | MovingAverage | STRONG_BEARISH | 559 | 0.440 | −72 bp |

**Strongest (candidates to keep and up-weight once they are confirmed at universe scale):**

| Signal family | Detector | n | Excess hit | Mean dir-adj excess |
|---|---|---|---|---|
| > N% below SMA (mean reversion) | MADistance | 2,723 | **0.576** | **+187 bp** |
| Large gain | PriceAction | 52 | 0.596 | +125 bp |
| Stoch bull cross (oversold) | StochasticCross | 103 | 0.515 | +98 bp |
| RSI oversold (multi-period) | MultiRSI | 1,044 | 0.510 | +74 bp |
| OBV cross above EMA | OBVCMF | 209 | 0.526 | +55 bp |

**Patterns worth acting on:**

1. **The "overbought / extended = bearish" family is anti-predictive in this sample.** Stocks extended above their averages kept outperforming. This is also the *largest* vote bloc, because of the fan-out in B3. Recommendation: regime-gate it. Allow bearish extension votes only when SPY is below its 200-day MA or realized vol is in its top quintile (§6). Otherwise make them neutral.
2. **"Near highs" (HLProximity) is the worst bullish signal, yet it carries the heaviest weight** (EXTREME = 3.0, up to 15 copies). It is essentially a beta proxy. Replace it with a *relative* version, "near high **and** outperforming SPY over 20d" (`relative_strength.py` has the returns logic), or drop it.
3. **Oversold mean-reversion is the most consistent bullish family.** It currently gets *less* weight than the breakout family it contradicts. Once B3 is fixed, give it a vote equal to one trend-family vote, not a fraction of one.
4. **Confluence should count independent families, not signals.** Define 4–5 families (trend: MA alignment + ADX/DI; momentum: RSI/Stoch/MACD; mean-reversion distance: MADistance/%B; volume/flow: OBV/CMF; relative strength vs SPY/sector). Require agreement across **≥ 3 families** for BUY/SELL, instead of `bull_count ≥ 3`, which today can be met by a single detector. Verify independence with the §5d correlation report before fixing the family list.
5. **Add information the detectors don't have:** relative strength vs benchmark and sector (already coded, unused), distance measured in **ATR units** rather than % (so TQQQ and KO are comparable), and an earnings-date proximity flag (signals within ~5 days of earnings are dominated by the print).

---

## 1. What the six-item plan misses

| # | Six-item plan | Blind spot | This plan |
|---|---|---|---|
| — | *(no metric)* | "Better" is never defined, so a change could make things worse without anyone noticing | §2: baseline harness, with rank IC, Brier score and decile spread |
| — | *(label unchanged)* | A hit is `forward_return > 0`, so a signal gets credit for market beta | §3: excess return versus a benchmark, scaled by volatility |
| — | *(detector bugs unaddressed)* | Direction-less bull votes, fake SMA-200, nested fan-out, self-contradicting BB votes (§0.3) | **P-1**: fix B1–B9 first |
| 1 | Cap votes per category | Assumes detectors are correlated *by category*. The real correlation has to be measured, and it crosses categories. **Most of the redundancy is within a detector** (B3–B5), not across a category. | P-1 collapses fan-out; §5d measures the rest |
| 2 | Calibrate per `(detector, strength)` | The code today is **per strength only** (B8). A per-detector version still averages over regimes. | §6: regime-conditioned weights |
| 3 | Fixed ceiling / Wilson shrinkage | A one-line pseudo-count (B6) fixes the saturation. It remains an *absolute* score. | B6 now; §4 cross-sectional rank later |
| 4 | Continuous data quality | Correct, and kept as-is | §5: data quality becomes a feature, not a multiplier |
| 5 | Learned weights from `forward_returns` | Waits months for live data. Also trains on binary detector firings, discarding the underlying values. | §5: backtest-replay dataset, with continuous features |
| 6 | MTF disagreement penalty | Built on timeframes that are mostly duplicates (B7) | B7 first; §7 stacking only on real intervals |

---

## 2. Step zero: a baseline harness

Without this step, "better" is just an impression. Build one evaluation script that scores **any** scorer function on the same point-in-time data.

**Metrics** (all out-of-sample, walk-forward):

| Metric | What it answers | Why it matters here |
|---|---|---|
| **Rank IC** (Spearman correlation between score and forward excess return, per date, then averaged) | Do higher scores lead to higher returns? | The headline number |
| **IC t-stat / IC IR** (mean IC ÷ std IC) | Is the IC consistent, or driven by a few lucky dates? | Guards against a single-regime fluke |
| **Top-minus-bottom decile spread** | What is the return gap between the best- and worst-ranked names? | The number a user of the heatmap actually feels |
| **Brier score + reliability curve** | When confidence is 0.70, is the call right about 70% of the time? | Directly measures the "confidence is too easy" complaint |
| **Coverage** | How many symbols clear the gate? | Stops the scorer from looking good by publishing almost nothing |
| **Per-detector report card** (new) | Per signal family: n, excess hit, lift over base, direction-adjusted mean excess, per regime | Makes §0.4 reproducible and turns "which signals are weak" into a weekly artifact |

**Target (updated):** the probe baseline is IC ≈ 0.01 (t ≈ 0.6), so "2× baseline" could be met by noise. The ship bar is absolute:
- mean universe rank IC ≥ **0.02** at 20d (≥ 0.015 at 5d) with **t ≥ 2** on purged walk-forward folds;
- non-negative IC in every §6 regime;
- a monotone decile spread (top > middle > bottom).

**Deliverable:** `backtests/evaluate.py` takes a `scorer(features_row) -> float`, returns the metrics table, and gets run once against today's `ConfluenceRanker` (pre-P-1) and once post-P-1. The two rows go to `docs/`. Each later phase is judged against the post-P-1 row.

---

## 3. Fix the label

`backtests/engine.py:80-86` currently computes (the Supabase path mirrors it in `db/calibration_store.py:65`):

```python
forward_return = (forward_close - bar.close) / bar.close
hit = forward_return > 0   # bullish
```

**Problem:** SPY rose over most of the history being replayed. A signal that always says "bullish" posts a hit rate above 50% while predicting nothing. *Verified in §0.2:* 0.590 raw against a 0.576 base rate, and 0.458 on excess.

**Replacement label:**

```
excess_return   = forward_return(symbol) - forward_return(benchmark)
target          = excess_return / realized_vol(symbol, lookback=20) * sqrt(horizon)
target_binary   = excess_return > 0          # for classification and Brier
```

- **Benchmark:** SPY by default. For sector ETFs and leveraged names, the matching underlying index (the seed CSV already has `sector_group`).
- **Vol-scaling** stops high-beta and leveraged tickers (TQQQ, SOXL, TSLA) from dominating. In the §0.4 probe, the *mean* excess figures are dominated by those names, which is why hit rate and mean can disagree in sign.
- **Keep the old label as a second column** so the baseline row in §2 stays comparable.
- **Apply to both calibration paths**: `backtests/engine.py` and `forward_returns` / `calibration_store.py`.

Expected effect: calibrated hit rates will *drop*. That is correct, because the old numbers were inflated.

---

## 4. Rank, don't score: cross-sectional output

Saturation at 1.00 comes from the fired-votes-only denominator (B6), made worse by the score being absolute: on a strong up day many symbols are unanimous.

**Replace the published number with two values:**

| Field | Definition | Range | Why it can't saturate |
|---|---|---|---|
| `rank_pct` | percentile of the model score among all symbols in the same scan run | 0–100 | Exactly 1% of the universe is in the top 1%, every day |
| `p_outperform` | calibrated probability that excess return > 0 at the horizon | 0.0–1.0 (in practice about 0.35–0.70) | Isotonic calibration on held-out data. Stays extreme only when history justifies it |

- **Start from `scoring/relative_strength.py`**, which already computes `rank_in_universe` / `universe_size` but is currently unused.
- **Confidence label** comes from `p_outperform` and its uncertainty, not from `abs(score)`. Example: HIGH requires `p ≥ 0.62` **and** at least 40 historical analogs in its bucket.
- **Heatmap and universe table** sort by `rank_pct`.
- **Direction** is still meaningful: a symbol can rank in the 95th percentile with `p_outperform = 0.54`. That reads as "best of a weak tape," which is honest. Today's 1.00 hides it.
- `rank_pct` needs the whole scan's scores before any row is written. `scanner.scan_universe` currently gates and synthesizes per symbol. Split it into a score-all pass, then a rank, then a gate-plus-synthesize pass.

---

## 5. The model: learned weights on continuous features, from backtest replay

### 5a. Dataset (available today, no waiting)

Build it from `compute_indicators()` output. That is vectorized, one pass per symbol, and point-in-time, since every indicator is rolling or EWM. Replay detectors via `scan_historical` only for the firing columns, after P-1 fixes them. Fetch **daily bars explicitly** (see B8).

| Column group | Examples | Note |
|---|---|---|
| Detector firings (post-P-1, one per concept) | `ma_align=+1`, `rsi_os=+1`, `hl_near_high=+1`, … | What the current scorer sees |
| **Underlying values** | RSI value, MACD histogram ÷ ATR, distance from 50/200 MA **in ATR units**, ADX with DI sign, BB %B, CMF, OBV slope, 20d return vs SPY | The detectors threshold these away. That discards most of the information. |
| Context | `data_quality_score`, bar count, volatility percentile, sector, days-to-earnings | Item 4 from the old plan becomes a feature |
| Regime | SPY above/below its 200-day MA, SPY ADX, realized-vol percentile | Feeds §6 |
| Labels | §3 targets at 5 / 20 / 60 day horizons | One model per horizon, or multi-task |

At roughly 954 symbols × ~2,500 bars, this is about 2M rows. That fits in memory, and building it from the indicator frame takes minutes rather than hours.

### 5b. Model ladder (stop at the first rung that clears the bar)

1. **L2-regularized logistic regression** on detector firings only. This is the "learned `_STRENGTH_BULL_WEIGHT`" from the old item 5, and the regularization handles correlated detectors.
2. **Same model plus continuous features.** Expect the largest single jump in IC here.
3. **Gradient-boosted trees** (LightGBM, `max_depth ≤ 4`, strong min-leaf) to capture interactions such as "RSI oversold × uptrend regime."

Adopt a rung only if it beats the previous one on out-of-sample IC by more than one standard error. (`scikit-learn`, `lightgbm` and `scipy` are not in `environment.yml` today; add them via mamba.)

### 5c. Validation that doesn't lie

- **Purged, embargoed walk-forward CV.** A 20-day forward label overlaps the next 19 rows, so a random K-fold split leaks the answer into training. Purge any training row whose label window touches the test fold, and embargo `horizon` bars after each fold.
- **Split by time, never by symbol only.**
- **Hold out the final 12 months entirely.** Look at it once, at the end.

### 5d. Measured decorrelation (replaces the category cap)

Compute the empirical correlation matrix of detector firings across the dataset. Cluster at |ρ| > 0.7 and report the clusters. P-1 removes the trivially redundant pairs (B4, B5 are ρ = 1 by construction); this report finds the rest and fixes the §0.4 family list.

---

## 6. Regime conditioning

A detector's reliability depends on the market regime, which a single per-detector hit rate averages away. §0.4 shows the likely case: bearish "overbought/extended" signals fail in an uptrend.

- Define 3–4 regimes from SPY alone: `trend_up`, `trend_down`, `range`, `high_vol`. Use simple rules: 200-day MA slope, ADX, and realized-vol percentile.
- **Pre-model quick win:** in `ConfluenceRanker`, zero out bearish MADistance, overbought RSI/Stoch and %B > 1 votes when the regime is `trend_up`.
- **Logistic rung:** add `detector × regime` interaction terms.
- **Tree rung:** add regime as a feature, and the model finds the splits itself.
- **Report per-regime IC** in the harness. A model with a strong average but a negative IC in `high_vol` should not ship without a gate for that regime.

---

## 7. Multi-timeframe: learn the combination (after B7)

`scoring/mtf.py` hard-codes `TIMEFRAME_WEIGHTS` and averages them. Stacking only makes sense once the timeframes are real, distinct bar intervals (B7). Until then it would be learning weights over copies of the daily signal.

- Each interval's model output becomes a feature: `p_daily, p_weekly, p_monthly` (+ `p_intraday` if kept).
- Add `dispersion = std(p_tf)` as an explicit feature. This is the learned version of the old item 6 disagreement penalty.
- Fit a small logistic meta-model on out-of-fold predictions only, to avoid stacking leakage.
- Timeframes that are missing for young tickers become NaN plus an availability flag, not zeros.
- Timeframes that fail to score are silently dropped from both the composite and `_dominant_action`. Today that always includes 1D and 5D (13% of the weight). Log the available-weight fraction per symbol, so a composite built from half the intended weight is visible.

---

## 8. Gate on expected value, not |score|

The current publication gate uses `|score| ≥ 0.15` and at least 2 signals (`config.py:220-222`, uncommitted). The signal-count term never binds (median 14 signals per bar). Replace it with:

```
publish if  p_outperform deviates from 0.5 by ≥ δ
        and expected_excess_return (from the regression head) > est_cost
        and data_quality_score ≥ floor
```

- **Interim (pre-model):** replace `PUBLISH_MIN_SIGNALS` with "≥ 2 independent families agree" (§0.4 item 4).
- `δ` is set on the validation fold to hit a target publish rate (for example ~40%, to keep LLM spend close to today's ~403 calls).
- `est_cost` is a flat 10 bps round-trip to start. Leveraged and illiquid names get higher values.
- **The LLM receives `p_outperform`, `rank_pct` and the top SHAP drivers.** The narrative then explains the model's actual reasons instead of a list of unanimous detectors.

---

## 9. Risks that can fake a "2×"

| Risk | How it fakes improvement | Mitigation |
|---|---|---|
| **Survivorship bias** | `seed/universe_symbols.csv` lists *today's* survivors, so replaying history on it excludes delisted losers | Report it, and discount headline IC. Where possible, add known delisted tickers to the backtest set |
| **Adjusted-close look-ahead** | yfinance back-adjusts (`auto_adjust=True` in `fetcher.py`). Harmless for returns, but dangerous for any absolute price-level feature | Use only ratio or ATR-normalized features. Never use raw price levels |
| **Label overlap leakage** | Overlapping 20-day windows make random CV look brilliant | Purge and embargo (§5c) |
| **Multiple-testing** | Trying 30 variants and keeping the best one. §0.4 alone looks at ~55 families. | Log every variant in the harness output. Touch the final holdout once. Confirm §0.4's winners on a fresh period before up-weighting |
| **Regime luck** | The out-of-sample window happens to be one long trend (as in the §0.2 probe) | Require per-regime IC to be non-negative, not just a good average |
| **Bug-fix mistaken for skill** | P-1 changes the score distribution. A lift in IC after P-1 is real but is not "the model" | Record pre- and post-P-1 baseline rows separately |

---

## 10. Rollout

| Phase | Work | Files | Ship criterion |
|---|---|---|---|
| **P-1** (new) | Fix B1–B9: direction-less votes, `min_periods`, fan-out collapse, BB contradiction, MACD dedupe, score pseudo-count, MTF intervals, calibration bars and keys, HIGH override | `scoring/confluence.py`, `detection/{trend,momentum,volume}.py`, `indicators/compute.py`, `scoring/mtf.py`, `scanner.py`, `scripts/calibrate.py`, `data/fetcher.py` (explicit interval) | Unit tests per bug. Bump `SIGNALS_APP_CODE_VERSION` to 1.2.0 (changes detection output) |
| **P0** | Evaluation harness, per-detector report card, and baseline rows (pre- and post-P-1) | new `backtests/evaluate.py`; vectorized dataset builder from `compute_indicators` | Baseline metrics committed to `docs/` |
| **P1** | Excess-return, vol-scaled label, applied to both calibration paths | `backtests/engine.py`, `db/calibration_store.py`, `scripts/calibrate_supabase.py` | Baseline recomputed on the new label |
| **P2** | Family-based confluence (§0.4 item 4) and regime gate on extension votes (§6) as a *scorer variant* | `scoring/confluence.py` | Beats the post-P-1 baseline IC, and gives a floor to compare the model against |
| **P3** | Logistic rungs 1–2, purged walk-forward | new `scoring/model.py`, `scripts/train_scorer.py` | Absolute bar from §2 (IC ≥ 0.02 at 20d, t ≥ 2), non-negative in every regime |
| **P4** | Isotonic calibration, `rank_pct` (reusing `relative_strength.py`), and `p_outperform` in output | `scoring/`, `schemas/signal_output.py`, Supabase migration adding the two columns | Reliability curve within ±5pp across all bins |
| **P5** | EV gate plus LLM prompt fed with drivers | `scanner.py`, `synthesis/mtf_llm.py` | Publish rate within 35–45%, LLM calls ≤ today's |
| **P6** | MTF stacking on real intervals, plus regime interactions | `scoring/mtf.py` | Beats P3 out-of-sample, and never loses in a regime |
| **P7** | Weekly retrain, replacing the strength-bucket calibration | `.github/workflows/calibrate.yml`, `calibration` table gains `model_version` | Drift alert if live 20-day IC falls below half the backtest IC for 4 straight weeks |

P-1, P0 and P1 are the prerequisites. P-1 is mostly small, local edits: B1, B5 and B6 are a few lines each, and B3 is the largest.

---

## 11. Why this is "2×" and not just "more items"

The six-item list is a set of **patches to a formula**. This plan changes the **process**:

- It **fixes the inputs first**. The detectors currently vote bullish on downtrends, count one fact 15 times, and argue with themselves (§0.3).
- It has a **metric** with an absolute bar, so improvement is measured rather than argued.
- It has a **correct target**, so calibration measures skill instead of market beta.
- It has an **output that cannot saturate** (rank plus calibrated probability), so the 1.00 / HIGH problem cannot come back in a different form.
- It uses **the data already on disk**, so learned weights arrive in weeks, not after months of live collection.

The harness in P0 decides whether the target was hit, and no one has to take it on trust.

---

## 12. Implementation status (2026-09-23)

P-1, P0 and P1 shipped in #29. P2–P7 are implemented as code + tests; **none of the ship criteria have been demonstrated on the full universe**. See `wiki/decisions/2026-09-23-learned-scorer-p2-p7.md` for the file map.

| Phase | Built | Not done / unverified |
|---|---|---|
| P2 | `FamilyConfluenceRanker`, regime gate | Thresholds (`FAMILY_*`) are untuned; "beats post-P-1 baseline IC" not measured |
| P3 | Logistic rungs 1–2, purged walk-forward, holdout, ship-bar check, `scripts/train_scorer.py` | Rung 3 (LightGBM) not built; full-universe run not done; smoke runs on small samples missed the bar |
| P4 | Isotonic calibrator, `rank_pct`, `p_outperform` in schema/DB, migration | Migration not applied to any database; reliability bar checked only in training |
| P5 | Score → rank → gate/synthesize scan, EV gate, LLM drivers | δ targets a 40% publish rate on OOF data; live publish rate and LLM-call count unmeasured; drivers are linear contributions, not SHAP |
| P6 | Interval resampling, stacking features, OOF stack dataset, comparison test | Live scan does not use per-interval models or the meta-model |
| P7 | Weekly workflow, model + IC-history tables, drift alert | Workflow never run; live IC uses only published names (range-restricted) |
