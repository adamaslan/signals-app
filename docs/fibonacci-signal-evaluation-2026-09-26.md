# Fibonacci signal evaluation (2026-09-26)

What the `FibonacciDetector` signals actually predict, measured before deciding what ships in the default detector set.

> **Re-evaluated the same day on the full universe; read that section first.** The +4.4pp headline below came from the 200-ticker sample the rule was selected on. On all 940 usable seed tickers the shipped signal's edge is **+1.2pp (z = 1.8)**. The first study's sections are kept as written, relabelled where they overstated the method.

## Method
- **Universe:** random sample (fixed seed) of 200 tickers from `seed/universe_symbols.csv`; 197 had enough history. 5 years of daily bars, `compute_indicators` output.
- **Causal:** at bar `i` the detector sees only `df.iloc[: i + 1]`. First evaluated bar is 200 (indicator warm-up); last is `len - 21`.
- **Outcome:** close-to-close return 21 bars ahead. A bullish signal "hits" if that return is > 0, a bearish one if < 0. The baseline is the unconditional hit rate over every bar of the same tickers (P(up) = 54.1%, mean 21-day return +1.8%).
- **No overlap:** at most one event per ticker per signal per 21 bars.
- **Split-sample robustness check (not out-of-sample validation):** tickers split into two halves by sorted order (A/B); a result only counts if it holds in both. Both halves took part in choosing among the variants below, so neither is a holdout. The full-universe re-evaluation is the closest thing to one.
- **Variants tried:** tolerance 0.15 / 0.25 / 0.40 ATR, minimum leg 2 / 3 / 5 ATR.

## Results (default parameters: 0.25 ATR, 3 ATR legs), edge = hit rate minus baseline
| Signal | n | edge A / B (pp) | Verdict |
|---|---|---|---|
| Golden pocket hold, above-average volume, bullish | 991 | **+5.2 / +5.5** | Edge, both halves |
| Golden pocket hold, above-average volume, bearish | 921 | +0.9 / +3.1 | Not significant |
| Golden pocket hold, normal volume, bullish | 1358 | +1.7 / -2.7 | No edge |
| Golden pocket hold, normal volume, bearish | 1358 | -0.8 / -2.8 | Wrong direction |
| Confluence hold (all four grades) | 1.6-2.1k each | -3 to +3, mixed | No edge |
| 0.786 break, bearish / bullish | 2.2k / 2.5k | +3.1 / -0.8, +0.9 / +1.8 | No edge |
| 1.618 target (non-directional) | 1793 | -2.9 / -2.3 vs. up-baseline | Slightly bearish afterwards |

The volume condition is what separates the signal from noise: the same hold on normal volume has no edge. Tolerance barely matters (the volume-confirmed bullish hold is +4.1 to +6.5 across 0.15-0.40 ATR); a 5-ATR minimum leg destroys it (+1.0).

## The exact shipped default
Bullish golden-pocket hold on above-average volume, emitted whether or not a confluence zone is present: **n = 1603 events over 194 tickers, hit rate 58.5% vs. 54.1% baseline (+4.4pp, about 3.5 standard errors), mean 21-day excess return +0.9%.** Per-half numbers for this exact set were not kept; the closely related set that let confluence pre-empt it was +5.2 / +5.5.

## Control: is it the Fibonacci ratios?
Same event, same volume condition, but the zone moved to non-Fibonacci retracement bands (edge in pp, pooled; standard error about 1.5):

| Zone | Bullish, volume-confirmed |
|---|---|
| 0.618-0.65 (golden pocket) | **+5.6** (5.6 / 5.6 across halves) |
| 0.54-0.57 | +4.2 (6.5 / 1.8) |
| 0.70-0.73 | +3.0 (1.3 / 4.6) |
| 0.44-0.47 | +1.3 (3.4 / -0.9) |

**Reading:** the golden pocket is the best and most stable zone, but the gap to the control zones is about one standard error, so this data does not prove the edge is specific to Fibonacci ratios. Much of it looks like "pullback into the middle of a leg, then a bullish reversal bar on above-average volume".

## What shipped
`FibonacciDetector()` emits only the bullish, volume-confirmed golden pocket hold. Confluence holds, the 0.786 break, the 1.618 target, and the normal-volume and bearish holds are behind `FibonacciDetector(experimental=True)`, following the rule that a signal earns its place by beating baseline instead of being tuned until it does.

## Re-evaluation on the full universe (2026-09-26)

Reproducible with `scripts/eval_fibonacci.py`, which runs the shipped `FibonacciDetector()` bar by bar and causally (first bar 200, 21-bar horizon, one event per ticker per 21 bars). Pivots are computed once per ticker and filtered per bar; a check over 3,600 sampled bars found zero differences from calling the detector on each real prefix or window. The baseline is each group's own unconditional hit rate.

```bash
python scripts/eval_fibonacci.py --cache /tmp/fibcache --sample-size 0 --window 0
python scripts/eval_fibonacci.py --cache /tmp/fibcache --sample-size 0 --window 63
```

| Run | Tickers | Events | Edge pooled (pp) | z | Halves A / B (pp) | Excess 21d return |
|---|---|---|---|---|---|---|
| First study (selection sample) | 197 | 1603 | +4.4 | 3.5 | not kept | +0.9% |
| As first shipped, new 200-ticker sample (seed 20260926) | 198 | 1611 | +1.0 | 0.8 | +1.5 / +0.5 | -0.19% |
| As first shipped, full universe | 940 | 7751 | +1.6 | 2.8 | +1.8 / +1.4 | +0.30% |
| As first shipped, full universe, 63-bar window | 940 | 7703 | +1.4 | 2.6 | +1.6 / +1.3 | +0.29% |
| **Current (review fixes), full universe** | 940 | 5659 | **+1.2** | 1.8 | +1.5 / +0.9 | +0.26% |
| Current, full universe, 63-bar window | 940 | 5633 | +1.1 | 1.7 | +1.4 / +0.8 | +0.27% |

The full universe includes the first study's 200 tickers; that sampling seed was not recorded, so they cannot be excluded. Treating the rest as a holdout puts the edge at roughly +1pp.

**Review fixes in the current row**, both applied as specified before re-measuring, not chosen by result:
- A hold now requires the bar's low (its high, for down-legs) to stay within the zone plus the 0.25 ATR tolerance on both sides. Before, a bar that traded far through the zone, even below the leg's low, and closed back above it counted as a hold. This removed about 27% of events, which were slightly positive (a flush-and-reclaim pattern). The difference, +1.6 vs +1.2pp, is within one standard error (about 0.6pp).
- A high/low pivot pair on the same bar (a wide outside bar) is no longer treated as a leg.

**History length:** limiting the detector to 63 bars barely moves the edge (+1.2 to +1.1pp). *Corrected 2026-09-28:* an earlier version of this paragraph said the scheduled scan fetches 63 bars. It asks for `"3mo"`, but `data/fetcher.py` `_WARMUP_PERIOD_OVERRIDE` widens that to `"1y"` (~252 bars). The 63-bar run is a stricter lower bound, and production falls between the two measured windows.

**Reading:** the volume-confirmed bullish golden pocket hold has a small positive edge that does not reach conventional significance on its own. It stays the default because it is still the only fib signal above baseline, and it enters scoring as one technical vote among many, not as a standalone trade signal. Its `STRONG BULLISH` grade overstates the evidence; downgrading it is a scoring decision left open.

## Other timeframes: not evaluated (added 2026-09-28)

Every number above is **daily bars**. Nothing here says the signal works on hourly or weekly bars, and nothing in the app computes fib on them yet. The scan matrix's 1D–6M columns are lookbacks, and `_WARMUP_PERIOD_OVERRIDE` turns all five into the same year of daily bars.

**Why the daily result doesn't transfer.**
- *What carries over:* the level math. A 0.618 retracement is a fraction of the leg on any interval.
- *What doesn't:*
  - volatility, which scales with time;
  - intraday volume, which follows a U-shape through the day, so a 20-bar average compares the open with lunch;
  - which traders watch which chart. Under the heterogeneous-market view, a weekly level is watched by slower, larger capital than a daily one.

  So the golden-pocket edge could be bigger, smaller or absent on another interval.

**How to evaluate another interval with this script** (proposed, not built). Weekly needs no new data, because it can be resampled from the cached daily bars:
1. Add `--interval 1wk`. Resample daily bars to W-FRI, using only completed weeks.
2. Run the unchanged detector. Its ATR, 0.25-ATR tolerance, 3-ATR minimum leg and `Volume_MA_20` are all then in weekly units.
3. Pre-register the horizon as about 13 weekly bars rather than 21. A 21-bar weekly horizon is about 5 months, which is a different question.
4. Use 10 years of history. Weekly bars give roughly a fifth as many events, so 5 years is underpowered.
5. Count it as a new variant. It earns its own place in the default set or doesn't; it doesn't inherit the daily result.

Hypotheses worth testing first, with pre-registered goal and kill lines:
- **Daily hold inside a weekly pocket:** goal +1.5pp over the plain daily hold, kill ≤ +0.5pp.
- **Daily hold only when the latest completed weekly leg is up:** goal +1.0pp, kill ≤ +0.3pp.
- **Weekly depth distribution:** does it show more excess mass at 0.618 than daily? This directly tests whether the golden pocket works because traders watch it.

Theory, causal rules and the full hypothesis list are in the homebase harness doc `FIBONACCI.md` §12.

## Caveats
- **Daily bars only.** See the section above.
- One 5-year window, dominated by a rising market (baseline 54% up); a bear regime could differ. Regime split not done.
- Context filters (RSI < 45, close vs. SMA) looked stronger in a scan of about a dozen filters but that is a multiple-comparisons search; none were built into the detector.
- No transaction costs, no position sizing: this is a hit-rate and mean-return study, not a strategy backtest.
- Sample is the seed universe, which includes thin and low-priced names.
- 14 of the 954 seed tickers lacked enough history and were skipped in the re-evaluation.
