# Running signals-app from chat + GitHub only

This is the operating model for driving `signals-app` without a local
terminal: Claude Code (this chat) handles code and CI, GitHub's web UI
handles secrets and one-off approvals. It maps the [TODO.md](TODO.md)
"Suggested order" onto that split.

## Division of labor

| Task | Who does it | How |
|---|---|---|
| Write/edit code, run tests, lint, typecheck | Claude (this chat) | Directly in the session |
| Commit + push to a feature branch | Claude | `git push -u origin <branch>` |
| Open a PR | Claude | GitHub PR-creation tool |
| Watch a PR's CI / review comments and fix failures | Claude | `subscribe_pr_activity`, then autonomous fix-and-push loop |
| **Set repository secrets** (`OPENROUTER_API_KEY`, `SUPABASE_ANON_KEY`, …) | **You** | GitHub web UI — see below |
| **Trigger a spend-incurring workflow run** (full-universe scan) | **You** confirm, Claude triggers | Actions tab "Run workflow" button, or ask Claude to dispatch it once you've said go |
| Read workflow run status / logs | Claude | GitHub Actions API tools, summarized back to you in chat |
| Merge a PR | You (or ask Claude, with explicit confirmation) | GitHub UI or asked explicitly |

Claude has no `gh` CLI and no tool that can read or write secret *values* —
that's deliberate: secrets never pass through chat, so there's nothing here
that could leak one into a transcript or a pasted shell command.

## 1. Setting secrets (you, one-time, in the browser)

Go to the repo on github.com → **Settings → Secrets and variables →
Actions → New repository secret**. Add:

- `OPENROUTER_API_KEY` — from your OpenRouter account
- `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` — likely already set (TODO.md
  P0 #1 says these two exist; only the OpenRouter key is missing)
- `SUPABASE_ANON_KEY` — only needed if Playwright E2E is wired to read live
  data (TODO.md P1 #4)

Confirm under the same Secrets page that the count matches what you expect
(TODO.md's check: 3 secrets, not 2).

## 2. Getting code changes in

Just ask, in this chat: "implement #5", "fix the flaky test", "add the
support/resistance detector," etc. Claude edits, tests, commits, pushes to
the session's branch, and — only when you ask for one — opens a PR.

If you want Claude to keep a PR green on its own (fix CI failures, respond
to review comments) say so; Claude will call `subscribe_pr_activity` and
handle events as they arrive, without you needing to check back.

## 3. Running a workflow (spend-incurring or not)

Free/no-spend runs (dry-run, lint, tests in CI) — Claude can trigger these
directly when useful, or they run automatically on push/PR.

Spend-incurring runs (anything that calls the LLM or writes to Supabase —
TODO.md P0 #3's pilot and full-universe runs) — **always confirm with you
first**, even though Claude has the technical ability to dispatch a
workflow run via the GitHub API. Say "go" and Claude will trigger it and
report back the run's status/cost-relevant numbers (published count, wall
time per shard) once it finishes.

You can also trigger any workflow yourself from the repo's **Actions**
tab → select the workflow → **Run workflow**, no CLI needed.

## 4. Checking results

Ask Claude to check a run — it reads the Actions run status, job logs, and
step summaries via the GitHub API and reports back in chat (counts, errors,
timings) rather than dumping raw logs. For a live dashboard view, the
deployed GitHub Pages site (`deploy-pages.yml`, already automatic on push
to `main`) is the place to look at published signals.

## Suggested order (unchanged from TODO.md, annotated)

1. **#2** dry-run pricing — Claude runs locally in-session, free, no secrets needed
2. **#4** Playwright scaffold — Claude, code-only (already scaffolded)
3. **#1** key wiring — **you**, GitHub Secrets UI
4. **#3** pilot run (5 tickers) — Claude dispatches, **after you say go**
5. **#4** E2E into CI, blocking — Claude, code-only
6. **#3** full universe run — Claude dispatches, **after you say go**, this is the real spend
7. **#7/#8** lint + type cleanup — Claude, code-only, separate PR
8. **#6** flaky-test fix — Claude, code-only

Everything marked "Claude, code-only" needs nothing from you but the
go-ahead in chat. Everything marked **you** is a deliberate stop so a
credential or a real charge never happens without your say-so.
