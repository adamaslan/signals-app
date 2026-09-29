# Fibonacci multi-timeframe (weekly) evaluation — small-scale (2026-09-28)

FIB-ICHIMOKU-MA.md §12.7 step 2: MTF2 (unchanged default detector on weekly
bars, 13-bar horizon, vs weekly baseline) and MTF5 (excess-mass test at 0.618
on weekly vs daily retracement depths). Both run here at **8 tickers**, not
the doc's full-scale requirement (10 years, full universe) — this is a
code-path proof, not a measured result at the power the doc specifies. Do not
treat these numbers as evidence for or against the weekly signal; treat the
*code* (`resample_to_weekly`, `retracement_depths`, `excess_mass_near`,
`--interval 1wk`, `--mtf5` in `scripts/eval_fibonacci.py`) as what's actually
being claimed done.

## Method
- **Universe:** 8 tickers, fixed seed 20260926 sample of `seed/universe_symbols.csv`
  (`AEE, AMKR, ASX, DHR, MCHP, ONON, UNP, URNM`).
- **Weekly resampling (M1):** cached daily bars grouped `W-FRI` (first Open,
  max High, min Low, last Close, summed Volume); the final week is dropped
  when the daily cache doesn't yet reach that week's Friday — never scores a
  forming week.
- **Pivot lag (M2):** detection runs directly on the weekly-resampled frame,
  so `PIVOT_WINDOW` (a bar count) is automatically counted in weeks, not
  days — no separate code path needed.
- **MTF2:** `FibonacciDetector` unchanged, run causally over the weekly
  frame with a 13-week forward horizon and the weekly-bar baseline (not the
  daily 21-day baseline).
- **MTF5:** for each bar with an active confirmed leg, the causal retracement
  depth `(leg.high - close) / leg.range` (up-legs; mirrored for down-legs) is
  recorded; "excess mass" is the observed fraction of depths in
  `[0.552, 0.684]` (0.618 ± 0.066) minus the `13.2%` a uniform distribution
  over `[0, 1]` would put there.

## Results

### MTF2 — weekly default-detector hit rate vs weekly baseline
| Interval | Tickers | n (events) | Hit rate | Baseline | Edge (pp) | z |
|---|---|---|---|---|---|---|
| Daily (21-bar, for comparison) | 8 | 49 | 55.1% | 52.8% | +2.3 | 0.32 |
| **Weekly (13-bar, MTF2)** | 7 | 15 | 73.3% | 59.9% | **+13.4** | 1.06 |

15 events is far too few to draw a conclusion. The z = 1.06 above is
unclustered and can't be compared against SA10's clustered-z floor — that
comparison would understate the real uncertainty. Reported honestly as a
small positive point estimate on a tiny sample, not a finding.

### MTF5 — retracement-depth excess mass near 0.618
| Interval | Tickers | n (bar-depths) | Observed mass in band | Expected (uniform) | Excess |
|---|---|---|---|---|---|
| Daily | 8 | 5786 | 10.7% | 13.2% | **-2.5pp** |
| Weekly | 8 | 1003 | 10.6% | 13.2% | **-2.6pp** |

Both daily and weekly show a small *negative* excess at this sample size —
depths are, if anything, slightly under-represented near 0.618 relative to a
uniform baseline, not clustered there. This is consistent with the existing
finding in `docs/fibonacci-signal-evaluation-2026-09-26.md` that
non-Fibonacci control zones showed similar edges to the golden pocket: this
run adds no evidence that 0.618 is special on either timeframe. Same sample
too small to be conclusive either way — L1 (SA14) at full scale, all 7
ratios, 3 regimes, is the actual gate.

## What this run does and doesn't prove
- **Does prove:** `resample_to_weekly` (M1), the weekly `--interval 1wk` path
  through `evaluate_ticker`, and the new `--mtf5` / `retracement_depths` /
  `excess_mass_near` code all run end-to-end on real yfinance data without
  error, and produce numbers in a sane range (hit rates and excess-mass
  fractions between 0 and 1, event counts matching expectations).
- **Does not prove:** anything about whether the weekly signal or the 0.618
  ratio itself has an edge — 8 tickers and 15 MTF2 events is far below any
  reasonable power requirement. FIB-ICHIMOKU-MA.md §12.6 calls for a 10-year,
  full-universe run for MTF2; SA13's 2,000-ticker slice and SA12's 10-year
  `--period` flag are the scale this needs before any conclusion is drawn.
