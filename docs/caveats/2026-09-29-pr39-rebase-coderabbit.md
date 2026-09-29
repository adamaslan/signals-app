---
date: 2026-09-29
session: 2026-09-29
keywords: [pr39, v1-api, rebase, coderabbit, backtest-coalescing]
repos: [signals-app]
---

## [2026-09-29] PR #39 (/v1 integration API) — rebase, 2 CodeRabbit rounds, cloud-scan gap — signals-app

**Shipped:** PR #39 merged to `main` as `4d18174` — the versioned `/v1` API
(typed signals/backtests/briefs, RAG documents, opt-in API-key auth), the
`state` block on `SignalOutput`, the daily-bars backtest fix, and the
LLM-synthesis-over-HTTP fix. Plus two follow-up review-fix commits on the
same branch before merge.

### Current state

| Item | Status |
|---|---|
| PR #39 | Merged (squash), branch deleted, worktree removed |
| signals-app open PR queue | 0 (verified via `gh pr list --state open`) |
| Test suite | 292 passing (up from 233 pre-rebase), verified locally with `pytest -q` |
| CI (backend/frontend/fib-guard/Vercel) | All green on final push |
| CodeRabbit | Round 1 reviewed and addressed; round 2 **rate-limited, no bot re-review ran** |
| Cloud signal-scan (`.github/workflows/signals-scan.yml`) | Last scheduled run: 2026-09-29T01:06 UTC — **before** this merge (06:11 UTC); success |

### Timeline

| When | What | Outcome |
|---|---|---|
| Start | Read PR #39 state via `gh pr view` | `mergeable: CONFLICTING`, Vercel check failing; `docs/manual-todo.md` already had an entry flagging the rebase as a semantic conflict needing human judgment |
| Fix pass 1 | Read all 3 CodeRabbit comment endpoints, addressed 2 findings (Cache-Control auth-scoping; backtest request-coalescing) | Full suite green (233), pushed |
| Rebase | `git rebase origin/main` on `feat/api-v1-integration` | 3 files conflicted (`routes.py`, `config.py`, `service.py`); `routes.py`/`config.py` resolved cleanly (additive) |
| Rebase — near miss | Resolving `service.py`'s `_backtest_sync` conflict | Git's 3-way auto-merge had **silently spliced** an `await asyncio.to_thread(_replay)` call from `main`'s async `backtest()` into this branch's non-async `_backtest_sync()`, with **no conflict marker at all**. Caught only by reading the full function body against both parents' intent, not by trusting the auto-merge output — a blind `git add` + continue would have shipped a `SyntaxError` on every `/backtest` call. |
| Verify | Full pytest (292) + ruff on touched files after resolving | All pass; pre-existing ruff findings in `config.py`/other files confirmed unrelated via `git show origin/main:...` |
| Force-push (lease) | `git push --force-with-lease` | Clean; PR flipped to `mergeable: MERGEABLE` |
| Fix pass 2 | CodeRabbit's re-review found 2 new findings on the pushed diff: `/v1` framework errors not using the uniform error body; the backtest-coalescing fix from pass 1 was itself vulnerable to caller-cancellation propagating to every other caller sharing the in-flight task | Both fixed; verified with two scratch scripts (below) rather than trusting the bot's diff alone |
| Push 2 | Fast-forward push (0 behind main, no rebase needed) | CI green again |
| Wait | Monitored for CodeRabbit + CI on push 2 | CI green; **CodeRabbit came back rate-limited** — no actual re-review of push 2's diff |
| Merge decision | Checked `mergeStateStatus: CLEAN`, all required checks green, no new PR comments since push 2 | Merged rather than waiting out a further cooldown, since both fixes were already independently verified locally |

### Unlocking commands

```bash
PYTHONPATH="$(pwd)/src" python - <<'EOF'
# forces the worktree's local src/ ahead of the conda env's editable install,
# which pointed at a DIFFERENT checkout (the main clone dir, not this
# worktree) — a bare `python -c`/`python -` here silently imports stale code
EOF
```
Needed whenever ad-hoc verification is run from a git worktree that shares a
conda env's editable install with another checkout of the same repo — `pytest`
gets this right automatically (rootdir insertion), a bare script does not.

### Wiki candidates — suggested, NOT written

| Target page | Exists? | What it would say | Why it belongs there |
|---|---|---|---|
| `entities/api-endpoints.md` | yes (already updated by this PR's own wiki-ingest commit) | none — already current | n/a |
| `ops/known-issues.md` | yes | Git 3-way auto-merge can silently produce syntactically-invalid Python across an `async`/non-`async` function-signature refactor with **no conflict marker** — always re-read the full function body after a non-trivial rebase resolution, don't trust a clean `git add`. | This is a tooling gotcha that will recur on any future rebase touching `service.py`'s sync/async boundary, not specific to this PR. |

### Caveats — shipped, but

- **"PR #39 was reviewed by CodeRabbit before merge"** — only round 1 was.
  Round 2 (the fix for round 1's own findings, including the cancellation-
  safety fix) was never re-reviewed by the bot — it was rate-limited and
  merge proceeded on green CI + independent manual verification instead.
  - *Risk if ignored:* the cancellation-shielding fix (`asyncio.shield` +
    done-callback in `service.backtest()`) or the new `/v1` exception
    handlers in `main.py` could have a defect CodeRabbit would have caught
    that manual testing didn't — the manual verification covered the
    specific scenarios described in round 1's findings, not an open-ended
    review.
  - *To close:* trigger `@coderabbitai review` on the merge commit, or watch
    for it to surface in a future PR that touches the same files.

- **"The merge is exercised by the cloud signal pipeline"** — it is not, at
  least not yet, and possibly not ever for most of this PR's surface. The
  scheduled `signals-scan.yml` workflow runs `scripts/scan_universe.py` →
  `service.scan()` directly, in-process — it never starts the FastAPI app
  and never calls `/v1` routes, `main.py`'s new exception handlers, or
  `service.backtest()`'s coalescing/shielding logic. The repo has **no
  Cloud Run deploy workflow and no Dockerfile** (checked: none found), so
  there is no evidence the FastAPI server with the new `/v1` surface runs
  anywhere reachable outside a local `uvicorn` process. This matches the
  PR's own unchecked test-plan box: "Point nuwrrrld-portal
  `SIGNALS_ENGINE_URL` at a deployed build and confirm hit-rates populate
  the council brief."
  - *Risk if ignored:* the entire `/v1` API — the actual point of this PR —
    has been verified only by the local test suite and this session's
    scratch scripts, never against a live deployment or the cloud scan.
    A production/portal integration bug would surface for the first time
    when nuwrrrld-portal's `SIGNALS_ENGINE_URL` is actually pointed here.
  - *To close:* deploy the FastAPI app somewhere reachable (Cloud Run or
    equivalent) and complete the two unchecked boxes in PR #39's own test
    plan — LLM synthesis against a real provider key, and the portal
    integration smoke test.

- **"docs/manual-todo.md's PR #39 blocker entry was resolved"** — it was
  never actually added to the file. The branch carried a commit
  (`5c8d50f`) that created this entry; `main` had independently created
  `docs/manual-todo.md` with a different, unrelated entry (1D/5D matrix
  columns) in the meantime, producing an add/add rebase conflict. Since the
  PR #39 entry would have been stale the instant it landed (the blocker it
  described is exactly what this session resolved), it was dropped rather
  than merged in — the file now contains only main's pre-existing entry.
  - *Risk if ignored:* none directly; this is a deliberate omission, noted
    here so a future `git log` archaeology of `docs/manual-todo.md` doesn't
    wonder where the PR #39 entry went.
  - *To close:* n/a — working as intended.

### Undone — in scope, not delivered

- **PR #39's own two unchecked test-plan boxes** (LLM synthesis with a real
  provider key; portal `SIGNALS_ENGINE_URL` pointed at a live deploy) —
  *Why not:* out of scope for a bugmerge1 review-fix pass; both require
  either a live provider key or an actual cloud deployment target that
  doesn't currently exist for this app. *Blocked on:* a Cloud Run (or
  equivalent) deploy target being stood up for the FastAPI app.

### Next steps — resume here

None — ended clean. PR #39 is merged, the queue is drained, CI is green. The
items above are standing caveats, not blocked work from this session.

### Unverified assumptions

- **The `asyncio.shield` + done-callback fix in `service.backtest()` behaves
  correctly under real concurrent HTTP load** — *Would break if:* the
  scratch-script verification (a synthetic 2-caller cancellation test) didn't
  capture a real failure mode, e.g. many concurrent callers, a task that
  raises instead of cancels, or interaction with the 6h TTL cache under
  memory pressure.
- **The new `/v1` exception handlers don't change legacy route behavior** —
  *Would break if:* some legacy route relies on FastAPI's default `Exception`
  handler behavior in a way the smoke test (unknown-route + validation-error
  cases only) didn't cover — e.g. a legacy route that currently leaks a
  traceback in a way something downstream depends on (unlikely, but
  unverified beyond the two smoke-test cases).
