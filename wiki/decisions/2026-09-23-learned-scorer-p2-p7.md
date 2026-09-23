# 2026-09-23 — Learned scorer: family confluence → logistic model → rank + calibrated probability (plan P2–P7)

Implements phases P2–P7 of `docs/scoring-2x-plan.md` on top of the merged P-1/P0/P1
foundation (PR #29: detector bug fixes, `backtests/evaluate.py`, excess-return label).

## What exists now

| Phase | Piece | Where |
|---|---|---|
| P2 | Signals grouped into 5 families; `FamilyConfluenceRanker` counts agreeing *families*; bearish "overbought/extended" votes zeroed in `trend_up` | `scoring/families.py`, `scoring/regime.py`, `scoring/confluence.py` |
| P3 | L2 logistic regression, purged/embargoed walk-forward CV, final-holdout evaluated once, absolute ship bar | `scoring/model.py`, `scoring/features.py`, `backtests/dataset.py`, `backtests/train.py`, `scripts/train_scorer.py` |
| P4 | Isotonic calibration (pure numpy at inference), `rank_pct`, `p_outperform` in output + `signals` columns | `scoring/probability.py`, `schemas/signal_output.py`, migration `20260923000001` |
| P5 | Scan split into score-all → rank → gate/synthesize; EV gate; LLM sees `p_outperform`, `rank_pct`, top linear drivers | `scanner.py` |
| P6 | Real bar intervals (daily/weekly/monthly), stacking features with availability flags + dispersion, OOF stacking dataset | `scoring/mtf.py`, `backtests/stacking.py` |
| P7 | Weekly retrain workflow, `scorer_models` + `scorer_ic_history` tables, drift alert | `.github/workflows/retrain.yml`, `scoring/drift.py`, `scripts/check_scorer_drift.py`, `db/scorer_store.py` |

## Decisions worth knowing

- **Opt-in by artifact.** With no active model (`scorer_models` row or
  `calibration/scorer_model.json`) the scan runs the legacy path unchanged; a missing
  or corrupt artifact never breaks a scan.
- **Nothing is validated on the full universe yet.** The ship bar (IC ≥ 0.02 at 20d,
  t ≥ 2, non-negative per regime, reliability ±5pp) is enforced in code; a model that
  misses it is not written or published (`train_scorer.py` exits 3; the workflow treats
  that as "keep current model"). Smoke runs on a small sample did not clear it.
- **Model columns are only sent when set**, so legacy scans keep writing before the
  migration is applied. Apply the migration before activating a model.
- **Drivers are exact linear contributions** (coef × standardised value), not SHAP —
  the model is logistic. The tree rung (LightGBM) from plan §5b is not built.
- **P6 is offline only.** The stacking dataset/comparison exists, and
  `stack_probabilities` has a shrinkage fallback, but no per-interval models are
  persisted or scored in the live scan yet.
- Gate constants (`PUBLISH_MIN_FAMILIES`, `EST_ROUND_TRIP_COST`) live in `scanner.py`;
  family thresholds live in `scoring/confluence.py` and are untuned starting values.
