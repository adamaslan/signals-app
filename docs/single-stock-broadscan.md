# Execute a broad scan against one stock

This runner executes the existing nineteen detector classes on one stock's local completed daily OHLCV history. It does not merely create catalog entries or repeat one formula 500 times. It retains neutral/context observations that the directional outcome replay omits.

```sh
PYTHONPATH=src python -m signals_app.single_stock_scan \
  --symbol AAPL --csv /absolute/path/AAPL.csv \
  --start 2026-03-16 --end 2026-11-10 \
  --source 'actual provider/export description and retrieval time' \
  --price-basis split-adjusted-ohlc > /private/local/AAPL-scan.json
```

AAPL is a usage example, not a stock chosen for the user. Use an already-held, permitted local snapshot. CSV columns: `Date,Open,High,Low,Close,Volume`, unique ascending `YYYY-MM-DD` dates, finite positive consistent OHLC and nonnegative volume. Open/high/low/close must share the declared split-adjustment basis. A field name alone proves nothing. Unknown/raw basis is refused. Use a Python environment installed from this repository's dependency declaration. Local focused validation additionally needs pytest.

At least 252 bars must precede the first evaluation bar. At most 366 evaluation bars are allowed. To match the older scan's daily span, supply 424 valid bars and choose the last 172 for evaluation. All indicators are recomputed from each prefix. Input hashes exclude price rows after `--end`; changing future prices cannot change earlier decisions. Daily gaps/staleness over seven days are refused, but this guard does not certify an exchange calendar or detect every mislabeled corporate action. Retrospectively adjusted prices and today's universe do not establish unbiased historical availability.

## What the actual output means

- `detector_count`: nineteen actual registered classes, **not 500 independent detectors**. A class may test parameter grids or emit several observations. `detector_evaluations` is classes times evaluated bars, not hits.
- `observations`: all emitted records, including neutral/context. Duplicate identical detector/name/category/strength tuples on a bar count once.
- `new_state`: a state absent from the prior successfully evaluated adjacent bar. First-bar initialization and recovery after an error are not new events. This is not a corporate-action or news timestamp claim.
- `nested_windows`: last 21/63/126/172 observed bars. They overlap. Never sum them to manufacture a total.
- `coverage`: per-bar, per-detector success/error and observation count. A successfully evaluated detector can emit zero; this is not the same as a failure or an unavailable provider.
- `source_declared`, `price_basis_declared`, input SHA256 and engine/runner versions preserve provenance. `basis_independently_verified:false` remains explicit.
- Errors produce degraded output and CLI exit2. Validation refuses unsafe input before scanning. No threshold guarantees 500 findings. Sparse or zero results remain real results.

No provider fetch, paid key, model call, database, migration, deployment or detector promotion is in this runner. It prints JSON to stdout. The CLI does not write a result-store artifact unless the user redirects output to their own local path. No automatic app activation is claimed by a local CLI run.

## Relation to the 18-million script

Homebase PR75 `18-mill-scan.py` at `840c8d5090346aabbfc5f61dd7319bddbff3fd8d` combines172bar nested-window detector counts with a100-source registry containing implemented, gated and stub entries. Its18,417,198 is scenario arithmetic across954tickers, not a measured single-stock count. It has no symbol selector and depends on an experimental-detector function absent from current signals-app main. This new runner supplies the real symbol-selectable bar-detector execution path without inventing that missing function, calling every extra provider, or counting current snapshots as historical changes.

The100-source extension, trades/quotes/options/news/fundamentals and experimental extras remain unexecuted here. That is a real coverage gap relative to the older broad scenario, not completed500signal activation. Existing trend-correctness PR44 and Ichimoku-vote PR45 remain separate unmerged work. This runner preserves current detector outputs rather than silently replacing their definitions or equating correlated states with independent votes.

Source reviewed: https://github.com/adamaslan/homebase/pull/75 and current `src/signals_app/detection/orchestrator.py` at main `d9cb9738dead78ea8e318f566ff6790d2590045b`.

## Actual validation

```sh
PYTHONPATH=src python -m pytest --noconftest tests/offline/test_single_stock_scan.py -q
```

`--noconftest` intentionally excludes the repository's service/DB-wide fixtures. Eight focused tests cover actual19-detector execution, neutral-state retention/repetition, failure/recovery accounting, future-prefix invariance, input/basis/date/symbol validation, duplicate detector rejection and overlapping windows. Network-free synthetic172bar run:3268evaluations,0errors,1792observations,447new-state transitions. These figures are fixture results, not stock results or trading performance. This local environment used Python3.10 with the required detector libraries; the repository declares Python3.12, so native3.12/full-service suite remains unverified.

Remaining: user's stock selection, actual permitted historical input/source/basis verification, real named-stock execution and reviewable results, broader source coverage, app-facing UI/result wiring, independent exact-head review/CI and normal release gates. No deployment/merge is bundled with this runner.
