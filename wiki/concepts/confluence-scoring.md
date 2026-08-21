# Confluence Scoring

`ConfluenceRanker.rank_signals()` in
[`scoring/confluence.py`](../../src/signals_app/scoring/confluence.py) turns
the flat list of raw `MutableSignal` objects from
[detection](signal-detectors.md) into a single weighted `ConfluenceResult`.

## Vote weighting

Every signal's `strength` maps to a numeric vote via `_STRENGTH_BULL_WEIGHT`:

| Strength | Vote |
|---|---|
| EXTREME_BULLISH | +3.0 |
| STRONG_BULLISH | +2.0 |
| VERY_SIGNIFICANT | +1.5 |
| BULLISH / SIGNIFICANT / TRENDING | +1.0 |
| NEUTRAL | 0.0 |
| BEARISH | -1.0 |
| STRONG_BEARISH | -2.0 |
| EXTREME_BEARISH | -3.0 |

Certain high-conviction categories get a flat bonus added to the vote's
magnitude (`_CATEGORY_BONUS`): `MA_CROSS` +0.5, `MACD` +0.5, `VOLUME` +0.5,
`OBV_CMF` +0.3, `ICHIMOKU` +0.3. Everything else gets +0.

## Aggregation

For every signal: if the base vote is positive, `weighted_bull += vote` and
`bull_count += 1`; if negative, `weighted_bear += abs(vote)` and
`bear_count += 1`; if exactly zero, `neutral_count += 1` and
`max_weight += 0.1` (a small nonzero weight so neutral signals aren't
entirely free).

```
raw_score = (weighted_bull - weighted_bear) / max_weight   # in [-1, 1]
score = round(raw_score, 4)
```

If there are zero signals total, the result is a hardcoded neutral/HOLD/LOW
with all counts at 0 — never a divide-by-zero.

## Classification

- **bias**: `bullish` if `score >= 0.1`, `bearish` if `score <= -0.1`, else
  `neutral`.
- **confidence_label**: `HIGH` if `|score| >= 0.55`, `MEDIUM` if `>= 0.25`,
  else `LOW`. (Not the same field as `Signal.confidence` from LLM synthesis
  — this is the rule-based layer's own confidence label.)
- **action**: `BUY` if `score >= CONFLUENCE_BUY_THRESHOLD` (0.35) **and**
  `bull_count >= CONFLUENCE_BUY_MIN_SIGNALS` (3); `SELL` if
  `score <= CONFLUENCE_SELL_THRESHOLD` (-0.35) **and**
  `bear_count >= CONFLUENCE_SELL_MIN_SIGNALS` (3); otherwise `HOLD`. Note the
  **and**: a strongly negative score from just 1–2 loud signals still resolves
  to HOLD — the min-signal-count gate exists specifically to require breadth,
  not just intensity, before recommending a directional action.

## Where it plugs in

- Single-symbol route (`GET /signals/{symbol}`): `ConfluenceResult` feeds the
  feature dict passed to LLM synthesis (`_build_features()` in
  `api/routes.py`) — `confluence_score`, `bias`, `action`, `bull_count`,
  `bear_count`, `total_signals`.
  full `ConfluenceResult` (score/bias/confidence_label/action/counts) is not
  returned directly in the API response today; only the primary synthesized
  `Signal` and `feature_unavailable` flags are (see
  [architecture/pipeline.md](../architecture/pipeline.md)).
- Multi-timeframe scoring: each timeframe gets its own independent
  `ConfluenceResult` — see [concepts/multi-timeframe.md](multi-timeframe.md).
