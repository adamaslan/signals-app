---
date: 2026-10-03
session: 2026-10-03 (single session; covers everything up to the live engine pass)
keywords: [ichimoku, fibonacci, eval-detector, engine-pass]
repos: [signals-app, mcp-finance1, holdemfoldem, nuwrrrld-portal]
---

## [2026-10-03] Ichimoku/fib open-work items — signals-app, mcp-finance1, holdemfoldem, nuwrrrld-portal

**Shipped:** mcp-finance1 PR #48 (Chikou look-ahead guard + import fix), holdemfoldem PR #18 (fib dedup / ATR / enum), signals-app PR #45 (kumo states out of the vote, `signals-app@1.4.0`), branch `feat/eval-detector` (evaluator + four reports, no PR yet), and one live engine pass against production.

### Current state
| Item | Status |
|---|---|
| mcp-finance1 #48 | open; tests run locally only |
| holdemfoldem #18 | open, mergeable at last check; API not redeployed |
| signals-app #45 | open; changes production scores; local tests only |
| `feat/eval-detector` | pushed, no PR (signals-app at the 3-PR cap: #43, #44, #45) |
| Live engine pass | ran: written=975, skipped=58, failed=0, hits=95; the API served a current bar afterwards |

### Timeline
| When | What | Outcome |
|---|---|---|
| 1 | mcp-finance1 Chikou patch | the `shift(-26)` exists only in foreign uncommitted edits in the main checkout, not in origin/main; left alone, guard test added in #48 |
| 2 | same PR | origin/main failed to import (missing config constants); fixed in a separate commit |
| 3 | holdemfoldem fib fix | doc named the wrong repo slug; rebased and opened #18 with wiki ingest |
| 4 | signals-app rows 2 and 3 | #45 and the evaluator; first eval run died with ModuleNotFoundError because the editable install points at the main checkout |
| 5 | full eval run | H1, H3, H4 all KILL on full history; H2 not run |
| 6 | live engine pass | first attempt denied by the auto-mode classifier as a production deploy; not worked around; ran after the user said to retry |

### Unlocking commands
```bash
cd ~/code/signals-app-evaldet && PYTHONPATH=src mamba run -n signals-app python scripts/eval_detector.py --cache <cache-dir> --run H1 H3 H4 --window 0 --window 63 --report-dir docs   # PYTHONPATH=src is required in a worktree
cd ~/code/nuwrrrld-portal-fixes && PORTAL_URL=https://financial.nuwrrrld.com node scripts/engine-run.mjs --mode=live --feed=iex --limit=400   # production write; needs PORTAL_PUSH_SECRET in .env.local
```

### Wiki candidates — suggested, NOT written
| Target page | Exists? | What it would say | Why |
|---|---|---|---|
| signals-app `decision-*` for kumo out of vote | new | cloud states are true on nearly every bar, so they were removed from the vote | road taken, changes scores |
| signals-app `concept-*` for pre-registered hypotheses | new | H1/H3/H4 thresholds and the KILL results | recurring evaluation pattern |

### Caveats — shipped, but
- **"Kill verdicts mean these signals do not work"** — the point estimates fell under the pre-registered lines, but the 95% intervals are wider than the goal-to-kill gap.
  - *Risk if ignored:* a real edge gets discarded, or a null one gets trusted.
  - *To close:* rerun with the full universe and more history.
- **"The eval used CI-grade data"** — it used laptop-cached yfinance data, 940 of 954 tickers.
  - *Risk if ignored:* survivorship and the 14 missing tickers bias the rates.
  - *To close:* rerun from a complete cache.
- **"H3 tests the kumo breakdown"** — it tests a column rule (first bar where `Ichimoku_CloudPos` is -1 after >= 0), because row 2 removed the kumo states from `IchimokuDetector`.
  - *To close:* won't — accepted.
- **"Everything is verified"** — nothing was observed in CI on #48, #18 or #45; `signals_engine_single.py` still emits the old kumo signals.
  - *To close:* watch CI; port the change to that file.
- **"The doc's premises were right"** — the repo slug was wrong, and the Chikou leak is not in origin/main. Foreign uncommitted WIP sits in the mcp-finance1 main checkout.
- **Engine pass verification** — checked only through the public API for one ticker, not by a database query; the doc's expected 974 rows came from the plan (actual: 975).

### Undone — in scope, not delivered
- **H2** — *Why not:* the §6.2 level field is not implemented · *Blocked on:* that implementation.
- **63-bar window for H1/H3/H4** — *Why not:* not computable (cloud needs 78 bars) · *Blocked on:* nothing, by design.
- **holdemfoldem-api redeploy** — *Why not:* #18 is unmerged and a deploy is a production action · *Blocked on:* merge plus an explicit go-ahead.
- **Eval PR** — *Blocked on:* a free PR slot in signals-app.

### Next steps — resume here
1. **Check PR slots, then open the eval PR.**
   ```bash
   cd ~/code/signals-app-evaldet && gh pr list --json number,headRefName
   ```
   Expect fewer than 3 open PRs.
   ```bash
   cd ~/code/signals-app-evaldet && gh pr create --base main --head feat/eval-detector --title "feat(eval): generalized detector evaluator + H1/H3/H4 reports" --body "Adds scripts/eval_detector.py and four pre-registered hypothesis reports under docs/. H1, H3, H4 are KILL on full history with wide intervals; H2 not run."
   ```
   Expect a PR URL. Then do the signals-app wiki ingest.
2. **Verify the engine pass in Neon (read-only).**
   ```sql
   SELECT mode, count(*) AS runs, max(started_at) AS latest FROM engine_runs GROUP BY mode;
   ```
   Expect a `live` row dated 2026-10-03. Run `\d engine_runs` first if the columns differ.

### Unverified assumptions
- **The cached yfinance data matches what CI would fetch** — *Would break if:* adjusted closes differ.
- **#18 stays mergeable** — *Would break if:* #16 or #17 merge first and touch `backend/core.py`.
