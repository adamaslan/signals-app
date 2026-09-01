# Universe Scan — Correctness & Report Improvement TODO

Source run: `scans3/universe-scan_20260901-002239.{html,json,md}`
(2026-09-01T04:22:39Z · period `3mo` · 12 symbols · 0.7s · 5 published · 7 gated)

The run "looked fine" — 12/12 ok, 0 failed, every symbol at data-quality **1.0**.
It was not fine. The scan fabricated indicator values and published trade-shaped
verdicts built on them. The report had no way to show that, because nothing in
the pipeline knew it had happened.

---

## P0 — Correctness. The scan is currently publishing fabricated signals.

### P0.1 Moving averages longer than the fetched window are silently faked

`src/signals_app/indicators/compute.py:65`

```python
cols[f"SMA_{period}"] = close.rolling(window=period, min_periods=1).mean()
```

`min_periods=1` means a 200-period SMA computed over 63 bars does not return
`NaN` — it returns the mean of whatever 63 bars exist. `SMA_100`, `SMA_200`, and
`SMA_63+` all collapse to *the same number*: the full-window mean.

**Proof from the run itself.** Symbol `A` has 63 bars, and its own `top_signals`
report both of these:

| signal | reported distance |
|---|---|
| `>5% ABOVE 100SMA` | 10.8% above 100-period SMA |
| `>5% ABOVE 200SMA` | 10.8% above 200-period SMA |

Two moving averages of different length cannot sit at an identical distance from
price. They are identical because both are the same 63-bar mean. The 100SMA and
200SMA do not exist in this dataset.

**Blast radius in this run.** Every scanned symbol had 63–64 bars, so *every*
100/200-period MA in the entire scan is fabricated. Within the truncated top-8
signal lists alone: 14 phantom-MA signals across `A`, `AA`, `ABNB`, `ABT`.
Counting the full `MA_DISTANCE` category:

| ticker | bars | confluence | MA_DIST hits | bull | bear | published |
|---|---|---|---|---|---|---|
| A | 63 | -0.399 | 5 | 0 | 5 | **PUBLISHED** |
| ABT | 63 | -0.414 | 5 | 0 | 5 | **PUBLISHED** |
| ADBE | 63 | +0.177 | 14 | 0 | 14 | gated |
| ACN | 63 | +0.151 | 11 | 0 | 11 | gated |
| ABNB | 63 | -0.264 | 9 | 0 | 9 | gated |

`A` and `ABT` are two of the five published names. **100% of their MA_DISTANCE
signals (5 each, all bearish) rest on moving averages that do not exist.**
MA_DISTANCE feeds confluence directly, so these fabricated bearish hits are a
material part of what pushed both past the `|confluence| >= 0.35` gate into a
`SELL`. Remove them and the SELL verdicts plausibly collapse below the gate.

**Fix:** set `min_periods=period` so an unsatisfiable MA yields `NaN`, and make
detectors skip `NaN` inputs instead of emitting a signal. A signal that cannot
be computed must be absent, never approximated.

**Regression test:** feed 63 bars, assert `SMA_200` is all-`NaN`, and assert no
`*200SMA*` signal appears in the detector output.

### P0.2 Nothing fetches indicator warmup history

`src/signals_app/data/fetcher.py:191` requests exactly the display period:

```python
df = ticker.history(period=period, interval=interval, auto_adjust=True)
```

`3mo` yields ~63 trading days, but `config.py:141` declares
`MIN_HISTORICAL_LOOKBACK = 200` and the indicator layer computes SMAs up to 200
plus rolling H/L windows up to 252. The pipeline asks for indicators it never
fetched the data to support.

**Fix:** fetch `max(requested_period, longest_indicator_window + buffer)`, compute
indicators over the full history, then trim to the requested window for display
and scoring. This is the real fix; P0.1 alone would just turn the fabricated
signals into missing ones.

### P0.3 Data quality scores 1.0 while indicators are unsatisfiable

`src/signals_app/indicators/data_quality.py:58` only compares bar count against
`MIN_BARS_BY_PERIOD` for the requested period. 63 bars is a legitimate `3mo`, so
it scores a clean **1.0** — which is exactly why all 12 symbols reported perfect
quality while feeding a 200-period SMA. The publication gate then trusts that 1.0.

**Fix:** add a deduction (and an explicit reason string, e.g.
`indicator_warmup_short:63<200`) when bar count is below the longest indicator
window in use. Data quality should describe fitness for *the computation being
run*, not just fitness for the period label.

---

## P1 — Report. Make the failure visible instead of invisible.

The current HTML would have rendered identically whether the numbers were real
or fabricated. That is the deeper reporting problem: it reports *outputs* and
never reports *confidence in its own inputs*.

- [ ] **Surface indicator coverage per symbol.** Show `bars=63` against the
  longest window actually used, and mark which indicators were fully warmed,
  partially warmed, or unavailable. This alone would have caught P0.1 on sight.
- [ ] **Visually flag derived-from-insufficient-data signals** rather than
  listing them identically to real ones.
- [ ] **Show the gate as a margin, not a boolean.** `A` at `-0.399` against a
  `0.35` threshold cleared by `0.049`. "PUBLISHED" hides how close that was; a
  distance-to-threshold column makes fragile verdicts obvious.
- [ ] **Report bars/period mismatch in the header.** The header says
  `period 3mo` and nothing else; it should say what that yielded and whether it
  sufficed.
- [ ] **`0.7s` for 12 symbols indicates cache hits, not fresh fetches.** Label
  data provenance (cached vs. fetched, and last-bar timestamp per symbol) so a
  fast run is not mistaken for a thorough one.

## P2 — Scan functionality

- [ ] **Default period is too short for the indicator set.** `DEFAULT_PERIOD = "3mo"`
  (`config.py:39`) cannot satisfy indicators the same config declares need 200
  bars. Either raise the default or gate the long-window indicators off by period.
- [ ] **12 symbols of a 954-row universe** — the run scanned an alphabetical
  head (`A`…`ADBE`), not a sample. Confirm this was an intentional `--limit`, and
  make the report state the selection rule; an alphabetical prefix is not
  representative of the universe.
- [ ] **`0 failed` is untested.** No symbol exercised the failure path, so the
  failure rendering is unverified. Test with a known-bad ticker.
- [ ] **All 7 gate rejections share one reason** (`|confluence| < 0.35`). The
  other gate conditions (data-quality, min-signals) never fired — expected given
  every dq was 1.0, but worth re-checking after P0.3 lands.

---

## Suggested order

1. **P0.2** (fetch warmup history) — the upstream cause.
2. **P0.1** (`min_periods`) — makes any remaining shortfall fail loudly.
3. **P0.3** (dq deduction) — stops the gate trusting unfit data.
4. **Re-run the same 12 symbols and diff confluence scores.** Expect `A` and
   `ABT` to move materially; if they still publish, the verdict is real this time.
5. P1 reporting, so the next regression of this class is visible in the artifact.

## Status: P0.1–P0.3 implemented (branch `fix/scan-warmup-and-min-periods`)

Implemented:
- `src/signals_app/indicators/compute.py` — `_smas_series`/`_volume_ma_series`
  now use pandas' default `min_periods=window` instead of `min_periods=1`, so
  an unsatisfiable SMA/volume-MA is `NaN`. Detectors already treat `NaN` as
  "no signal" via `_sf()`, so no detector changes were needed.
- `src/signals_app/data/fetcher.py` — `DataFetcher.fetch()` transparently
  widens daily-interval periods shorter than the 200-bar warmup floor
  (`_WARMUP_PERIOD_OVERRIDE`: `1d`/`5d`/`1mo`/`3mo`/`6mo` → `1y`) before
  calling yfinance, while the returned `OHLCVResult.period` and cache key stay
  the originally requested period. Intraday periods are left unwidened.
- `src/signals_app/indicators/data_quality.py` — `score_data_quality` now
  deducts 0.2 and reports `indicator_warmup_short:{n}<200` whenever bar count
  is under `MIN_DATA_POINTS_200MA`, independent of the period-based check.
- Regression tests added: `tests/test_detection.py` (NaN SMA on short
  history + no fabricated 100/200SMA signals end-to-end),
  `tests/test_calibration_and_data_quality.py` (warmup-short deduction),
  `tests/test_fetcher_warmup.py` (fetch widening, cache-key stability,
  intraday exemption). Two pre-existing tests that asserted a perfect 1.0
  score for a 60-bar/"3mo" window were updated — that assertion **was** the
  bug, codified as a passing test.

**Verified against the actual regression.** Re-running the same 12 tickers
from the 2026-09-01 scan after the fix:

| ticker | before: confluence / action | after: confluence / action |
|---|---|---|
| A | -0.399 / **SELL** (published) | -0.159 / HOLD (gated) |
| AAPL | +0.557 / **BUY** (published) | +0.286 / HOLD (gated) |
| AAPU | +0.586 / **BUY** (published) | +0.222 / HOLD (gated) |
| ABBV | -0.454 / **SELL** (published) | -0.336 / HOLD (gated) |
| ABT | -0.414 / **SELL** (published) | -0.333 / HOLD (gated) |

**All 5 previously published symbols dropped out of the gate.** Bar counts
correctly rose from 63 to ~250 (warmup fetch), confirming the fabricated
long-window signals were the deciding factor behind every published verdict
in that run, not incidental noise.

Not yet done: P1 (report-side visibility — indicator coverage, gate margin,
provenance) and P2 (default period, universe sampling, failure-path test).
