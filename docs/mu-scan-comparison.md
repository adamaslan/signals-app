# Keep both scans, but join their evidence before NWF activation

Checked November 11, 2026. This comparison reads executable source and the committed November 8 MU report. It does not rerun MU or any provider.

## Decision

Keep the older multi-source scan and the new single-stock runner. The older scan is broader by data type. The new runner is a more bounded, inspectable bar-detector execution path. Neither currently establishes 500 independently working signals in NWF. The next useful outcome is a reproducible MU result with separate bar observations, dated source events, current snapshots and unavailable coverage, then app-facing results under normal release gates.

## What each actually does

| Question | Earlier 18M scan / MU test | New runner in signals-app PR53 |
| --- | --- | --- |
| Stock selection | Reads a universe CSV; `--limit` truncates it. The MU report overrides `load_symbols_from_csv` to return MU. No native symbol argument. | Requires one explicit `--symbol` plus a local OHLCV CSV. |
| Bar execution | 172 evaluation bars, at least 252 preceding bars; default, experimental and extra bar families through `windows.py`. | 19 registered detector classes; indicators recomputed from each prefix; at least 252 preceding bars, up to 366 evaluated bars. Supports the same 172-bar span. |
| Other inputs | Alpaca bars with Yahoo fallback; a 100-entry registry of corporate actions, fundamentals, ownership, estimates, news, options and other sources. Several entries share one underlying data object. | No provider calls. No news, options, fundamentals, trade/quote feeds or experimental extension. |
| Recorded quantities | MU report: 2,257 route-1 bar-family occurrences plus 107 source-tagged/date events = 2,364. Registry: 59 implemented, 24 key-gated, 17 stubs. | Synthetic 172-bar validation: 3,268 detector evaluations, 1,792 emitted observations, 447 new-state transitions, zero errors. These are fixture counts, not MU results. |
| Failures | Per-source errors/unavailable states are recorded; one scored ticker does not mean all sources succeeded. Report mentions MU fundamentals 404. | Each detector/bar has success or error coverage. Failed detector iterations publish no partial observations; errors mark output degraded and CLI exits 2. |
| Provenance | MU report says Alpaca bars and 6 seconds; generated JSON files were not present in the inspected current folder. Its raw results have not been recovered here. A matching bar-engine dependency was recovered later; see the follow-up below. | Output carries engine/runner version, declared source/basis and input SHA256. A declared split-adjusted basis is explicitly not independent verification. |
| Reproduction today | `windows.py` imports `get_experimental_detectors`, absent from the inspected current signals-app main. The matching `01eefd6` engine was recovered in the follow-up below; current main still lacks this API. | Ten focused offline tests pass locally on Python 3.10. Repository-native Python 3.12/full-service validation and exact-head review remain separate gates. |

Do not compare 2,364 with 1,792 as if both scanned the same MU history. Different data, detectors and counting rules make that comparison invalid. The old 6-second report is not a fresh benchmark or a prediction of the new runner's runtime.

## Counting and correctness differences that matter

- The old 18,417,198 figure is scenario arithmetic across 954 tickers, not measured MU output. Its summary uses the largest 172-bar window, not the sum of overlapping windows. The new runner also keeps overlapping 21/63/126/172-bar windows separate.
- A registry entry marked implemented means a fetch/detect path exists, not that it succeeded or adds independent evidence. Historical trades and quotes are key-gated in the inspected source. They were not demonstrated active by the MU report.
- Some old methods emit a current snapshot when a field exists: `_detect_info_change` does not compare two historical values; options, ESG and 52-week methods also emit snapshots. These must not be presented as verified historical changes or a backtested edge. Statement column dates also do not certify when the report became available to investors.
- The new `new_state` means a detector state absent on the preceding successfully evaluated adjacent bar. Initialization and recovery after failure are not new events. It is not a timestamp for a verified news or corporate-action event.
- Nineteen classes can emit multiple parameterized observations. That is neither 19 independent votes nor proof of 500 unique validated strategies. Existing detector-correctness changes remain separate review work.

## Concrete path to broad NWF activation

1. Recover the MU raw JSON and verify its engine revision against the recovered matching bar dependency. Otherwise label the November 8 numbers report-only. Preserve the older runner unchanged while establishing a reproducible baseline.
2. Produce a permitted, source-stamped MU daily snapshot with at least 424 valid bars for a 172-bar comparison. Independently check split basis and completed-session dates. A ticker match alone does not prove identical input. No paid/provider execution is authorized by this comparison.
3. Run the new runner on that exact frozen bar input and the older full runner only after its remaining import/provider dependencies are checked. Compare per-date/per-family outputs, not totals alone. Preserve fixture and real-stock results separately.
4. Bring forward useful older adapters in small tested slices. Store available-at timestamps for dated events, label snapshots as snapshots, cache shared source fetches, and distinguish unavailable/error/zero results. Do not manufacture missing feeds or pad counts to 500.
5. Define the user's activation target through actual exposed outputs: unique families, parameterized observations, dated events and source coverage. Add an NWF result contract/UI showing provenance, counts, freshness and failures. Keep research/context evidence out of directional scores until validated. Neither CLI is presently wired into that app path.
6. Pass independent exact-head review, required CI and the authorized merge/release process. This document does not claim production activation, deployment or a trading edge.

## Sources and verification boundary

- [Earlier source and report, homebase PR75](https://github.com/adamaslan/homebase/pull/75), inspected head `840c8d5090346aabbfc5f61dd7319bddbff3fd8d`: `18-mill-scan.py`, `scan2m.py`, `windows.py` under `signals app/depth-scan-2026-10-18-data/`.
- [Committed MU smoke-test report](https://github.com/adamaslan/homebase/blob/feat/signal-scan-robust-pr-20261108/signals%20app/depth-scan-2026-10-18-data/18-mill-mu-single-ticker-test-2026-11-08.md). Its figures are attributed report claims, not independently replayed output.
- [New single-stock runner, signals-app PR53](https://github.com/adamaslan/signals-app/pull/53), executable/tests checked at `e70d21b8a2e312a8fd3891379c92fa41e9b96220`; main detector basis `d9cb9738dead78ea8e318f566ff6790d2590045b`. `docs/single-stock-broadscan.md` documents its CLI and limits.

The separate older `single-max-oct-26-a.py` / `scan_one_ticker_max.py` documentation describes million-scale fetched rows. It is not the MU report and its original executable has not been recovered. Keep raw rows, detector evaluations, observations and events as distinct units.


## Offline recovery follow-up, 20:10 ET

A source-only inspection of 37 live signals-app branch heads found `get_experimental_detectors` on `feat/graded-confluence`, exact commit `01eefd60e5e0e2605f4e39edb6fbee86df570efc`. The earlier `windows.py` itself writes `code_commit: "01eefd6"` into its output, independently supporting this match. The recovered module imports and supplies 19 default plus seven experimental classes. This recovers the bar dependency, not the missing MU JSON or proof of the full November 8 runtime.

We then executed the older script's six pure bar-counter functions, extracted directly from the inspected `windows.py` source, against that recovered engine. This deliberately avoids importing its provider/scanner entry points. Socket connections were forbidden. It is a source-extracted bar-path test, not a full 18M CLI run.

Both paths read the same frozen synthetic 424-row daily OHLCV CSV and evaluated its last 172 bars, December 19, 2025 through August 17, 2026. It is not MU or real market data:

- Older source-extracted bar path: 2,442 occurrences, comprising 1,820 default-kind occurrences, 69 experimental-kind occurrences and 553 extra-family occurrences (274 Fibonacci, 21 price crosses, 258 options-style price-derived readings). "Options-style" here uses OHLCV formulas, not an options feed.
- New PR53 actual CLI: 19 classes, 3,268 evaluations, zero errors, 1,912 observations and 512 new-state transitions. This differs from the earlier 1,792/447 fixture because it uses a newly frozen 424-row fixture with its last 172 rows, rather than assuming unrelated prior fixtures match.

This comparison now holds the synthetic input and date span constant. The output units still differ: old family occurrences are not the new observation/state-transition contract. Neither count is an independent-vote count, 500 validated strategies or an edge measurement. The old path computes the indicator frame before slicing; this exercise does not certify its historical information availability or prefix invariance. The new path recomputes each prefix.

No Alpaca, Yahoo, news, fundamentals, quote/trade or other provider was called, no keys or database were used, and no production detectors were promoted. Local Python 3.10 was used; the historical repository declares Python 3.12. Full native-runtime and provider-run validation remain outstanding.

Frozen CSV SHA256: `74de761b78b7e16db3dcc803fe65f564baed6e8cc6d3b15f253865b9e16d582c`. Source `windows.py` SHA256: `c5c70fcf1cae3ffbf247444d0433ea43d52dfa8599c2830c8dce657117cc3c4e`.
