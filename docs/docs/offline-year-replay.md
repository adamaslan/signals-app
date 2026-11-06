# Offline explicit-window historical replay

Research reconstruction, not contemporaneous calls, trades or portfolio returns.
No DB, network fetch or LLM calls are made by this runner.

```
python -m signals_app.historical_replay \
  --seed seed/universe_symbols.csv --bars-dir ./historical-bars \
  --output ./replay-results --start 2025-11-06 --end 2026-11-05
```

The above dates are the proposed one-year decision window ending on the last
closed US session at implementation time, not an owner-approved strategy choice.
Each ticker needs a matching `TICKER.csv` with Date,Open,High,Low,Close,Volume.
Include at least 200 prior observed bars. No implicit fill or symbol drop occurs.
`metadata.json` must include source, adjustment, retrieved_at and calendar_policy.
Data redistribution rights must be checked before committing or publishing prices.

The full current seed is 954 unique tickers, SHA256
`fe8b1e54f42d54cb48f0bcff573e5b45c48dcacebc4233bd31e41009667b74f8`.
SPY is not a member and benchmark/excess-return scoring is not implemented here.
The output manifest names every ticker, missing input, calendar gaps, hashes,
explicit horizons, pending outcomes, exact bucket counts and display gating.
Only complete status=ok symbols enter pooled results. Partial symbols retain
research-only detail and cannot silently enter a calibrated prior.

## Semantics and limitations

Every decision recomputes indicators from its prefix, using the existing 19
technical detectors. No future row goes into a decision. Directional outcomes
use subsequent observed closes, only through the observation cutoff. Defaults
1,5,20,21,63,126,252 are observed bars, not calendar months or years. A year of
decisions cannot mature the last 252-bar outcomes. Futures are pending, not misses.
No execution-at-close assumption is suitable for a trade: signals exist after
the close; these are diagnostic close-to-close labels, not executable fills.

Hits have a positive directional return. Flats are resolved non-hits. The
numerator and denominator are summed before division; hit rates remain null
when the actual category/strength/horizon count is below 30. The mean price
return has its own denominator. Signal occurrences are correlated and overlap;
a count of 30 is a display gate, not evidence of statistical significance.
Fees, slippage, dividends paid separately, borrow and capital allocation are not
modeled. Adjusted historical prices may be revised after corporate actions.
Current membership is survivorship-limited, not a historical point-in-time
universe. This does not reproduce old AI council commentary.

Crypto uses 24/7 UTC dates. Other assets use the XNYS reference calendar, not a
verified per-symbol listing venue. Calendar gaps are surfaced and excluded
from pooled aggregates; delisted, renamed and newly listed assets need explicit
coverage decisions. Input range includes pre-window warmup but no scoring
before start. Nothing imports the result into NWF's production database.

## Bounded local pilot, November 6, 2026

Actual public Yahoo daily-history inputs were read for AAPL, AGG, BTC-USD, VTSAX
and MSFT with serial requests at most once per two seconds, no retries or
account cookies. Four US-calendar assets had 251/251 decision bars and 250
pre-window bars. BTC-USD had 364/365 decision bars and 365 pre-window bars; the
provider's November 5 record had missing OHLCV, so it is partial, not complete.
The full seed ledger therefore reports 4 ok, 1 partial and 949 missing histories.
This pilot is not a full 954-ticker backtest or production hydration.
Adjusted-close/close ratios were applied to OHLC, with unchanged provider volume;
raw input, hashes and retrieval ledger remain local, not committed here.

Local focused plus existing historical-engine regression: 15 tests passed.
Complete backend suite: 305 tests passed with external socket access blocked
(loopback only); 10 dependency/deprecation warnings remain.
Tests include future mutation, truncated-prefix snapshot equality, known
bull/bear/flat outcomes, censoring, detector errors, malformed inputs and full
missing-symbol accounting. The real-input integration test skips when that
local pilot is absent. Independent verification and full CI remain required.
