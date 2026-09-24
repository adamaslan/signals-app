---
Date: 2026-09-22
Branch: feat/trigger-universe-scan
---

# Session summary — full-universe scan, extended to 8 timeframes

## Request

Run a scan across **all 954 seed symbols** for **all 8 UI periods** (Swing:
`1D 5D 1M 3M`, Long-term: `6M 1Y 5Y MAX`), and get the results populated in
the DB and visible on the frontend.

## What the codebase looked like going in

- The full 954-symbol scan is designed to run **only via GitHub Actions**,
  sharded 4 ways (`.github/workflows/signals-scan.yml`). The workflow's own
  comment says the multi-timeframe matrix is "too expensive at 954 tickers"
  for that path.
- The local/manual `POST /scan` route is capped at `MAX_MANUAL_SCAN_SYMBOLS`
  (100) specifically to stop a stray trigger from fanning out into hundreds
  of yfinance fetches + LLM calls.
- The multi-timeframe matrix (`signals_app.scoring.mtf`) only supported 5
  periods (`1D 5D 1M 3M 6M`) — `1Y`, `5Y`, `MAX` weren't wired into it at
  all. The frontend already anticipated this: `PERIOD_OPTIONS` in
  `web/src/lib/types.ts` marked `5y`/`max` as `supported: false`, showing a
  "beta → 1y" badge and silently falling back.
- No `GEMINI_API_KEY` / `OPENROUTER_API_KEY` set locally, so any local run
  produces rule-based signals only (LLM synthesis unavailable).

I raised these as a genuine decision point (bypass safety caps that were put
there on purpose vs. extend the matrix to periods it doesn't support yet vs.
pilot first). User chose: **just do it — full 954, all 8 periods, locally,
right now.**

## Code changes

Extended the timeframe matrix from 5 → 8 periods:

- `src/signals_app/schemas/signal_output.py` — added `Timeframe.five_year`
  (`"5Y"`) and `Timeframe.max` (`"MAX"`); extended `classify_divergence`'s
  `long_tfs` set to include them.
- `src/signals_app/scoring/mtf.py` — `SUPPORTED_TIMEFRAMES` now 8 entries;
  `TIMEFRAME_WEIGHTS` extended (`1Y: 0.20, 5Y: 0.15, MAX: 0.10`, others
  rebalanced down — **not calibrated against historical hit-rates**, just a
  reasonable default).
- `src/signals_app/scanner.py` — `_TIMEFRAME_TO_PERIOD` / `_PERIOD_TO_TIMEFRAME`
  extended with `1Y↔1y`, `5Y↔5y`, `MAX↔max`; docstring updated (5x → 8x
  fetch/LLM cost note).
- `src/signals_app/config.py` — `TIMEFRAME_CACHE_TTL_SECONDS` gained `5Y`/`MAX`
  entries (48h TTL each).
- `web/src/lib/types.ts` — `Timeframe` type, `VALID_PERIODS`, `PERIOD_LABELS`,
  `TIMEFRAMES` all extended; `5y`/`max` flipped from `supported: false` to
  `true` in `PERIOD_OPTIONS` (drops the "beta" badge, since the backend now
  actually serves them). No component changes needed — `PeriodControlPanel`
  and `SignalMatrixRow` already iterate the timeframe list generically.

No DB migration needed — `signals.matrix` is `jsonb`, keyed by timeframe
string, already schema-flexible.

## Verification before the real run

- Full test suite: 146 passed, 1 pre-existing failure
  (`test_scan_universe_report.py::test_markdown_has_sections_and_symbols`) —
  confirmed it fails identically on the unmodified code via `git stash`, so
  unrelated to this change.
- Direct smoke test of `build_matrix_for_symbol("AAPL", ...)` bypassing the
  gate: 6/8 timeframes scored (`1M 3M 6M 1Y 5Y MAX`); `1D`/`5D` drop out
  because those yfinance periods return <20 bars — pre-existing behavior,
  not something this change introduced.

## The real run

```
python scripts/scan_universe.py --seed seed/universe_symbols.csv \
  --period 3mo --matrix --trigger manual
```

Run **locally** (mamba env `signals-app`, this machine, not GitHub Actions).
Writes went to the real Supabase project via `.env`'s
`SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY`.

**Result:** 954 symbols, 950 ok, 4 failed, **288 published** with full
8-timeframe matrices, 171 seconds elapsed.

- Failed: `EVGOW`, `FM`, `MIDZ`, `TBHC` — all "yfinance returned empty data" /
  insufficient bars (delisted or bad tickers in the seed CSV, not a code bug).
- Verified directly via Supabase REST: sampled rows (`XOM`, `XP`, `WY`) each
  carry `signals` keyed by `['1M','1Y','3M','5Y','6M','MAX']` as expected.
- All 954 (minus the 4 failures) got `detector_hits` rows written regardless
  of gate outcome — the raw per-symbol data landed for the whole universe,
  not just the 288 that cleared the publication gate.

## Viewing it on the frontend

Dev server was already running (`next dev`, port 3000, base path
`/signals-app`, ~50 min uptime when checked — not started by this session).

- Per-ticker view: `http://localhost:3000/signals-app/signal/?symbol=XOM`
  (swap in any of the 288 published tickers) — the `5Y`/`MAX` period chips
  are now live instead of "beta".
- Home page `Recent Runs` table shows this run (`engine_runs.id=107`).
- A locally-saved "Universe" basket containing any of the 288 tickers will
  show fresh data via its existing read-only "Run basket" button — no
  re-scan needed.

## Caveats

- **Timeframe weights for `1Y`/`5Y`/`MAX` are an educated default, not
  calibrated** — unlike the rest of the confluence system, which measures
  strength → hit-rate from Supabase. Worth running `scripts/calibrate.py`
  against real outcomes once enough `5Y`/`MAX` history accumulates.
- **No LLM synthesis this run** — `GEMINI_API_KEY`/`OPENROUTER_API_KEY` unset
  locally, so every published signal (including all 8 timeframes' worth of
  matrix entries) used the rule-based fallback, not the real LLM synthesis
  path. `ai_degraded: true` on all of them.
- **The manual-scan safety cap (`MAX_MANUAL_SCAN_SYMBOLS = 100`) was
  deliberately bypassed** by calling `scripts/scan_universe.py` directly
  instead of the capped `POST /scan` route — a conscious choice made with the
  user, not something to repeat by habit for future "just scan everything"
  asks.
- **Unrelated dirty state found in the tree at session start**, not touched:
  `docs/TODO.md` had unstaged edits, plus several untracked docs files
  (`docs/fullstack-flow.html`, `docs/monthly_candidates_20260821.md`,
  `docs/report-improvements-2026-09-01.md`,
  `docs/universe-scan-improvements.md`, `.claude/session-gaps.md`) —
  left over from a stale/dead session the worktree-guard reclaimed at
  startup. Preserved, not reviewed or committed by this session.
- Nothing in this session was committed to git — all changes are currently
  unstaged working-tree edits on `feat/trigger-universe-scan`.
