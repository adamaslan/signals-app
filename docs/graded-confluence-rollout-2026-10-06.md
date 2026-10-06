# Graded confluence ranker: what was built, what is left

Implements §8.3 of `homebase/docs/states-and-near-a-level-as-signals-2026-10-06.md`.
Everything below is on branch `feat/graded-confluence` (worktree `~/code/signals-app-graded`),
cut from `origin/main` @ `db05386`. **No PR is open**: the repo already has 3 open PRs (#43, #44, #45),
which is the concurrency cap. Nothing here changes a production score, writes to the database, or
needs a flag to stay as it was. Every behaviour change is behind an env flag that defaults off.

## 1. Status

| Phase | State | What exists | What is left (needs you) |
|---|---|---|---|
| P0 Decide | **Decided 2026-10-06** | K = X 1.0, T 0.6, S 0.25, P 0.15, C 0 as *starting* values (fit on held-out data only); shadow runs 4 weeks and at least 300 graded BUYs, whichever is longer; both additive migrations approved (applied by pasting in the SQL editor) | none |
| P1 Taxonomy | Built | `scoring/kinds.py`, `SignalKind`, kind/concept stamped by the orchestrator | none |
| P2 Storage | Built, **not applied** | migration `20261006000001`, writer gated by `SIGNALS_WRITE_HIT_KINDS` | apply the migration (Step 3) |
| P3 Hygiene | Built, changes scores | MACD/RSI/OBV/BB/regime fixes, `scripts/flip_report.py` | real-data flip report; version bump at merge (Step 2) |
| P4 Events | Built | 7 detectors in `detection/events.py`, experimental-only | evaluator run (Step 4) |
| P5 Evaluator | Built, smoke-run only | `scripts/eval_signals.py`, `scoring/evidence.py`, baseline table | full-universe run (Step 4) |
| P6 Ranker + shadow | Built, **off** | `scoring/graded.py`, `context.py`, `scripts/graded_shadow.py`, migration `20261006000002` | apply migration, switch shadow on, wait (Steps 5-6) |
| P7 Cutover | **Machinery only** | `SIGNALS_RANKER` switch, `scoring/thresholds.py`, `graded_shadow.py thresholds` | shadow data, your sign-off, consumer sweep (Step 7) |
| P8 ML rung 3 | **Features only** | `FEATURE_SETS["rung3"]`, AUC/leakage gate, `train_scorer.py --rung3` | retrain after P7 (Step 8) |

Tests: full suite green on the branch (see Step 1 for the current count). Smoke-run on 6 live tickers:
shadow payload present, 0 unclassified labels, production result unchanged.

## 2. Where this deviates from the spec, and why

1. **New events are not in `get_default_detectors()`.** The spec says register them there and rely on
   E = 0. But `ConfluenceRanker` and `FamilyConfluenceRanker` vote on any directional signal and ignore E,
   so registering them would have changed production scores in P4. They live in
   `get_experimental_detectors()` and run only for the graded ranker (`include_experimental=True`).
2. **The category bonus is multiplied by E.** The spec adds it outside E, which would make an unearned
   MACD-category event worth +0.5. An E = 0 event is now fully inert.
3. **`MACD HIST TURN` replaces the spec's "histogram sign flip".** A histogram sign flip is the MACD
   signal-line cross that `MACDSignalDetector` already emits, so it would be one event counted twice.
   The new one is the inflection that *leads* the cross.
4. **Shadow scores go in a new table, `confluence_shadow`, not a column on `signals`.** `signals` only holds
   rows that cleared the publication gate, but the flips worth measuring include symbols production gated out.
5. **Experimental hits are not written to `detector_hits`.** The calibration job mines that table by
   (category, strength); experimental rows would pollute `strength_hit_rates` and shift confidence labels.
   The offline evaluator works from price history instead.
6. **The ranker is `scoring/graded.py`, not inside `confluence.py`.** Same reason the spec gives for not
   editing `FamilyConfluenceRanker`: keep the ML-feeding module untouched.
7. **P3 skips rows 3.1, 3.5 and the Kumo removal.** Open PRs #44 and #45 already do them. Merge those
   first; this branch will conflict in `config.py` and `detection/trend.py` and the resolutions are trivial.
8. **Graded mode stamps `code_version` with `+graded`.** Rows upsert on (ticker, bar, code_version); without
   the suffix graded rows would silently overwrite the old ranker's.
9. **K is fitted by `graded_shadow.py fit-k`, not by the evaluator.** Fitting K needs the ranker's score.

## 3. Caveats worth distrusting

- **The scheduled scan fetches `3mo` (~63 bars).** `signals-scan.yml` defaults `PERIOD` to `3mo`. The graded
  features that need `SMA_200` (risk context, location) and the Ichimoku events (52 + 26 bars) are mostly
  unavailable at that length, so shadow rows from the cron under-represent them. Dispatch a full-universe scan
  with `period=1y` to collect representative shadow data (Step 5).
- **The P3 flip report is a 40-ticker, ~7.9k-bar sample**, not the universe (numbers in section 3a). It is also
  overlapping 21-day windows, so the hit-rate gap is a sanity check on direction, not evidence. Re-run it on a
  bigger sample before merging P3.
- **The evaluator was smoke-run on 12 live tickers, not the universe.** It produced 217 label rows and 0 with
  E > 0, which is the right answer for that sample size (MIN_N = 300), not evidence about any signal.
- **`REF_EDGE_PP = 2.0`, the grid in `K_GRID`, and the shadow-default thresholds (0.20 / -0.20) are untuned
  starting values.** Only the evaluator, `fit-k` and `thresholds` are allowed to move them.
- **`evidence-baseline.json` is hand-set from documented measurements**, not measured by the evaluator. Its
  `note` field says so.
- **Lint:** new files carry some E501 (>100 chars) warnings; the repo already has them. F401/I001 were fixed.

## 3a. Measured so far (real data, read-only)

P3 flip report: 40 seed-universe tickers, 5y daily, every 5th bar from bar 210, 21-day forward return,
`origin/main` @ `db05386` vs this branch (`scripts/flip_report.py`):

| | old (main) | new (P3) |
|---|---|---|
| bars compared | 7,881 | |
| bars whose action changed | | 582 (7.4%): BUY->HOLD 163, HOLD->BUY 149, HOLD->SELL 118, SELL->HOLD 152 |
| mean score shift | | -0.0035 (max abs 0.267) |
| BUY hit rate (n) | 0.504 (1,467) | 0.506 (1,452) |
| SELL hit rate (n) | 0.497 (949) | 0.501 (919) |
| unconditional up-rate | 0.524 | |

Read: P3 did not make BUY or SELL worse on this sample. Both rankers' BUY hit rate sits *below* the 0.524
unconditional up-rate, which fits the spec's finding that these signals carry little directional edge. A 0.2pp
gap on ~1,450 overlapping calls is noise either way.

Other runs: the evaluator on 12 live tickers (pipeline ok, 0 labels earned E, expected at that size); the graded
ranker on 6 live tickers (payload sane, 0 unclassified labels, production result unchanged); the dry-run scan with
`SIGNALS_SHADOW_GRADED=1` on 3 tickers (0 failed; one earlier attempt hit a transient yfinance empty-data error
for all three and passed on retry, with shadow on and off).

Local runs after the P0 decision (2026-10-06):

- **Evaluator, 939 tickers, step 5** (`calibration/evidence/evidence-2026-10-06.json`, an artifact, not activated).
  Concepts with E > 0: `ichimoku_tk` (+4.5pp, holdout +3.3), `macd_zero` (+2.5, holdout +0.7), `kumo_twist`
  (+2.1, holdout +0.6), `rsi_divergence` (+2.8, holdout +1.8), `vol_divergence` (E 0.27). The weak states and
  breaches got E = 0. Distrust: current-constituents universe, z ignores cross-ticker correlation on shared
  dates, one holdout year, and `ichimoku_tk` contradicts the earlier 62-ticker pilot. Re-test before using.
- **`fit-k`, 100 tickers:** the graded score's rank correlation with forward return is *negative* before and after
  fitting (holdout -0.046 at the start K, -0.040 at the fitted K T=0.4 S=0.1 P=0.3). Starting K kept; the gain is
  tiny and the sign is wrong either way. This is on baseline evidence (default E 1.0 for live signals), so a
  re-run with the earned evidence file is the fairer test.
- **Rung-3 smoke, 60 symbols, horizon 20:** rung 3 rank IC 0.0014 vs rung 2 0.0114, buckets not monotone, ship bar
  missed, so no model was written. Not adopted; the 60-symbol sample is small, so this is a smoke result only.

**Not run here:** Steps 3, 5 (workflow dispatch), 6 (needs shadow rows in Supabase), 7 and 8. Their commands were
checked against the code (flags, env var names, file paths), not executed. `calibrate_supabase.py` flags were
not checked at all.

## 4. Steps

Each step is one paste; read the output before the next. Run from `~/code/signals-app-graded` unless stated.

### Step 1: preflight

```bash
cd ~/code/signals-app-graded && git status -sb | head -1 && git log --oneline origin/main..HEAD | wc -l && mamba run -n signals-app python -m pytest -q 2>&1 | tail -1
```
Expect: `## feat/graded-confluence...origin/main [ahead 9]`, `9`, and `NNN passed` with no failures.

### Step 2: real-data P3 flip report (read-only)

Preflight, checks you have network and a reference tree of `origin/main`:
```bash
cd ~/code/signals-app-graded && git fetch -q origin main && git worktree add --detach ../signals-app-main-ref origin/main && git -C ../signals-app-main-ref log --oneline -1
```
Expect: one line starting `db05386` (or newer).

Export 40 real tickers to CSV (uses the same cache the evaluator uses):
```bash
cd ~/code/signals-app-graded && mkdir -p /tmp/sigcache /tmp/flipcsv && PYTHONPATH=src:scripts mamba run -n signals-app python -c "
from pathlib import Path
import eval_fibonacci as ef, eval_signals as es
for t in ef.sample_tickers(40):
    df = es._load(t, Path('/tmp/sigcache'))
    if df is not None: df.to_csv(f'/tmp/flipcsv/{t}.csv')
print(len(list(Path('/tmp/flipcsv').glob('*.csv'))), 'csv files')
"
```
Expect: about `40 csv files`.

Dump both versions, then diff:
```bash
cd ~/code/signals-app-graded && PYTHONPATH=../signals-app-main-ref/src mamba run -n signals-app python scripts/flip_report.py dump /tmp/old.json --csv-dir /tmp/flipcsv && PYTHONPATH=$PWD/src mamba run -n signals-app python scripts/flip_report.py dump /tmp/new.json --csv-dir /tmp/flipcsv && mamba run -n signals-app python scripts/flip_report.py diff /tmp/old.json /tmp/new.json
```
Expect: two `N bars from <path>` lines (the paths must differ: `signals-app-main-ref` then `signals-app-graded`),
then `bars compared`, `action flips`, a transition table, and BUY/SELL hit rates for old and new. Read it; this is
the P3 gate (graded BUY hit rate not worse). Section 3a has the result of running exactly this.

Clean up the reference tree:
```bash
cd ~/code/signals-app-graded && git worktree remove ../signals-app-main-ref && git worktree prune && git worktree list | grep -c main-ref
```
Expect: `0`.

### Step 3: ⛔ apply migration 1 (`detector_hits` kind/concept/context)

🖱 **Dashboard:** Supabase project → SQL Editor → paste the file below and run. Additive only: three nullable
columns, no backfill. There is no Supabase CLI or `psql` on this machine, and the repo does not document a
migration command, so the dashboard is the safe path.
```bash
pbcopy < ~/code/signals-app-graded/supabase/migrations/20261006000001_detector_hits_kind.sql && echo "copied"
```
Expect: `copied`. Apply, then verify (read-only):
```bash
cd ~/code/signals-app-graded && set -a && . ~/code/signals-app/.env && set +a && mamba run -n signals-app python -c "
import os, httpx
u, k = os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_ROLE_KEY']
r = httpx.get(f'{u}/rest/v1/detector_hits?select=kind,concept,context&limit=1', headers={'apikey': k, 'Authorization': f'Bearer {k}'})
print(r.status_code, 'columns exist' if r.status_code == 200 else r.text[:120])
"
```
Expect: `200 columns exist`. Rollback (only if needed): `alter table detector_hits drop column kind, drop column concept, drop column context;`

### Step 4: earn evidence on the full universe (read-only)

Dry check on a small sample first (also warms the cache):
```bash
cd ~/code/signals-app-graded && PYTHONPATH=src mamba run -n signals-app python scripts/eval_signals.py --cache /tmp/sigcache --sample-size 12 --step 10 --workers 4 --out /tmp/evidence-smoke.json | tail -3
```
Expect: `12 tickers, NNN labels, 0 earned E>0` and `wrote /tmp/evidence-smoke.json`.

Real run (long: it replays every detector on every 5th bar; start with `--step 5` and let it run):
```bash
cd ~/code/signals-app-graded && PYTHONPATH=src mamba run -n signals-app python scripts/eval_signals.py --cache /tmp/sigcache --sample-size 0 --step 5 --out calibration/evidence/evidence-$(date +%F).json | tail -20
```
Expect: `940 tickers`-ish, then a JSON object of labels with E > 0 (possibly empty: that is a valid result).

Verify the output loads and show what earned weight:
```bash
cd ~/code/signals-app-graded && PYTHONPATH=src mamba run -n signals-app python -c "
import glob, json
from pathlib import Path
from signals_app.scoring.evidence import load_evidence
f = sorted(glob.glob('calibration/evidence/evidence-2*.json'))[-1]
t = load_evidence(Path(f))
print(f, t.version, 'default_E', t.default, 'labels', len(t.labels), 'concepts', len(t.concepts))
print({k: v for k, v in t.labels.items() if v > 0})
"
```
Expect: a line with the file name, `default_E 0.0`, and the earned labels. To use it, set `SIGNALS_EVIDENCE_FILE` to that path (Step 5).
Fallback if the run is interrupted: re-run the same command; the OHLCV cache in `/tmp/sigcache` is reused.

### Step 5: ⛔ start shadow mode

🖱 **Dashboard:** apply `20261006000002_confluence_shadow.sql` the same way as Step 3.
```bash
pbcopy < ~/code/signals-app-graded/supabase/migrations/20261006000002_confluence_shadow.sql && echo "copied"
```
Verify (read-only):
```bash
cd ~/code/signals-app-graded && set -a && . ~/code/signals-app/.env && set +a && mamba run -n signals-app python -c "
import os, httpx
u, k = os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_ROLE_KEY']
r = httpx.get(f'{u}/rest/v1/confluence_shadow?select=id&limit=1', headers={'apikey': k, 'Authorization': f'Bearer {k}'})
print(r.status_code, r.text[:60])
"
```
Expect: `200 []`.

📝 **File edit** (a separate PR, after #44/#45 merge): in `.github/workflows/signals-scan.yml`, add to the `env:` of
each "Run scan" step: `SIGNALS_SHADOW_GRADED: "1"`, `SIGNALS_WRITE_CONFLUENCE_SHADOW: "1"`, optionally
`SIGNALS_WRITE_HIT_KINDS: "1"` (needs Step 3) and `SIGNALS_EVIDENCE_FILE: calibration/evidence/evidence-<date>.json`.
Then dispatch one full-universe scan with a long period so the graded features have history (see Caveats):
```bash
gh workflow run signals-scan.yml -f full_universe=true -f period=1y -f dry_run=false
```
Expect: `Created workflow_dispatch event`. Check it started:
```bash
gh run list --workflow=signals-scan.yml --limit 3
```

Local smoke of the same path without touching the database (dry run, 3 tickers):
```bash
cd ~/code/signals-app-graded && SIGNALS_SHADOW_GRADED=1 PYTHONPATH=src mamba run -n signals-app python scripts/scan_universe.py AAPL MSFT NVDA --period 1y --dry-run 2>&1 | tail -5
```
Expect: three result lines and no traceback.

### Step 6: after four weeks and at least 300 graded BUY calls

```bash
cd ~/code/signals-app-graded && set -a && . ~/code/signals-app/.env && set +a && PYTHONPATH=src:scripts mamba run -n signals-app python scripts/graded_shadow.py report --horizon 21 --since 2026-10-07 --out /tmp/shadow-report.json | tail -40
```
Expect: JSON with `production`, `graded`, `flips`, and `cutover.ready` plus `reasons`. Exit code 0 only when ready.
**Read `flips.examples` yourself**: the gate cannot sign that off.

Fit the kind multipliers (offline, long; needs the Step 4 cache):
```bash
cd ~/code/signals-app-graded && PYTHONPATH=src:scripts:. mamba run -n signals-app python scripts/graded_shadow.py fit-k --cache /tmp/sigcache --sample-size 100 --step 5 --out /tmp/fit-k.json
```
Expect: `K`, `fit_spearman`, `holdout_spearman`, and the `start_*` values beside them. If `holdout_spearman`
does not beat `start_holdout_spearman`, keep the starting K.

Derive thresholds from the shadow bars:
```bash
cd ~/code/signals-app-graded && set -a && . ~/code/signals-app/.env && set +a && PYTHONPATH=src:scripts mamba run -n signals-app python scripts/graded_shadow.py thresholds --since 2026-10-07
```
Expect: JSON with `buy`, `sell`, `target_calls`, `achieved_calls`, and `wrote calibration/evidence/thresholds-<date>.json`.
It exits 1 with a reason if there are too few shadow rows or production BUY calls.

### Step 7: ⚠️ cutover (not done; needs your sign-off)

Decide first, and record it (this is P0): the scheme (K starting values), the shadow length, and approval of the
two migrations. Then:

1. Sweep the consumers of the production score, all listed from a grep of this branch:
   Python: `scripts/analyze.py`, `best1_scan.py`, `calibrate_supabase.py`, `generate_signal_report.py`,
   `scan_21_day_ds.py`, `scan_bullish_2wk.py`, `scan_optimal_monthly.py`, `scan_universe_report.py`, `sigloc.py`,
   `signals_engine_single.py` (hard-codes `PUBLISH_MIN_CONFLUENCE_SCORE = 0.35`), `src/signals_app/scoring/mtf.py`,
   `synthesis/mtf_llm.py`, `db/calibration_store.py`. Web: `web/src/lib/{api,db,landing,types,universe,universeView}.ts`
   and the landing/universe components. Mobile (`gcp3-mobile/lib/shared/signal-policy.ts`) treats
   `confluence_score` as 0-100, the portal and gcp3 sit on other scales: check the units before flipping.
   `ConfluenceRanker` is still called directly in all the scripts above and does not follow the switch.
2. Bump `LLM_PROMPT_VERSION` if the synthesis prompt text changes (`config.py`).
3. Switch on (the scan refuses to start without the thresholds file):
```bash
cd ~/code/signals-app-graded && SIGNALS_RANKER=graded SIGNALS_THRESHOLDS_FILE=$(ls calibration/evidence/thresholds-*.json | tail -1) PYTHONPATH=src mamba run -n signals-app python scripts/scan_universe.py AAPL --period 1y --dry-run 2>&1 | tail -3
```
Expect: a result line. Without `SIGNALS_THRESHOLDS_FILE` it must instead fail with
`SIGNALS_RANKER=graded needs SIGNALS_THRESHOLDS_FILE`.
4. Re-run calibration on the `+graded` code version (`scripts/calibrate_supabase.py`; run it with `--help`
   first, its flags were not verified here), and check the `"CATEGORY|STRENGTH"` keys still match after P3's regrades.
5. Wiki ingest in the same task as the PR (global wiki rule).
Rollback: unset `SIGNALS_RANKER` (the legacy ranker is the default; no code revert needed).

### Step 8: P8 retrain (after Step 7)

Smoke on a small universe, no publish:
```bash
cd ~/code/signals-app-graded && PYTHONPATH=src:. mamba run -n signals-app python scripts/train_scorer.py --rung3 --limit 60 --horizon 20 --out /tmp/scorer-rung3.json 2>&1 | tail -15
```
Expect: a report table with `rung1`, `rung2`, `rung3`, then `rung3 adopted` or `rung3 NOT adopted:` with reasons
(AUC gain below 0.005, AUC above 0.60 as a leakage flag, or ship bar missed). The model is only written when the
ship bar is met. Do not add `--publish` until the full-universe run adopts it.

## 5. Where to look

| Thing | File |
|---|---|
| Kind / concept table | `src/signals_app/scoring/kinds.py` |
| Graded ranker | `src/signals_app/scoring/graded.py` |
| Evidence table and baseline | `src/signals_app/scoring/evidence.py`, `calibration/evidence/evidence-baseline.json` |
| Thresholds and the ranker switch | `src/signals_app/scoring/thresholds.py`, `scoring/production.py` |
| New events | `src/signals_app/detection/events.py` |
| Evaluator, shadow analysis, flip report | `scripts/eval_signals.py`, `scripts/graded_shadow.py`, `scripts/flip_report.py` |
| Env flags | `src/signals_app/config.py` (`SIGNALS_WRITE_HIT_KINDS`, `SIGNALS_SHADOW_GRADED`, `SIGNALS_WRITE_CONFLUENCE_SHADOW`, `SIGNALS_EVIDENCE_FILE`, `SIGNALS_RANKER`, `SIGNALS_THRESHOLDS_FILE`) |
