# Fibonacci signal evaluation (2026-09-26)

What the `FibonacciDetector` signals actually predict, measured out of sample before deciding what ships in the default detector set.

## Method
- **Universe:** random sample (fixed seed) of 200 tickers from `seed/universe_symbols.csv`; 197 had enough history. 5 years of daily bars, `compute_indicators` output.
- **Causal:** at bar `i` the detector sees only `df.iloc[: i + 1]`. First evaluated bar is 200 (indicator warm-up); last is `len - 21`.
- **Outcome:** close-to-close return 21 bars ahead. A bullish signal "hits" if that return is > 0, a bearish one if < 0. The baseline is the unconditional hit rate over every bar of the same tickers (P(up) = 54.1%, mean 21-day return +1.8%).
- **No overlap:** at most one event per ticker per signal per 21 bars.
- **Out of sample:** tickers split into two halves by sorted order (A/B); a result only counts if it holds in both.
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

## Caveats
- One 5-year window, dominated by a rising market (baseline 54% up); a bear regime could differ. Regime split not done.
- Context filters (RSI < 45, close vs. SMA) looked stronger in a scan of about a dozen filters but that is a multiple-comparisons search; none were built into the detector.
- No transaction costs, no position sizing: this is a hit-rate and mean-return study, not a strategy backtest.
- Sample is the seed universe, which includes thin and low-priced names.
