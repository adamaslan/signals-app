# Session summary: graded confluence ranker (2026-10-06)

Branch `feat/graded-confluence` in `signals-app`, worktree `~/code/signals-app-graded`, pushed to origin.
No PR is open (the repo is at the 3-PR cap: #43, #44, #45). Spec: `homebase/docs/states-and-near-a-level-as-signals-2026-10-06.md` §8.3.
Longer reference: `docs/graded-confluence-rollout-2026-10-06.md` (status, deviations, caveats, full steps).

## What was done

| Phase | Result |
|---|---|
| P0 | Decided by you: K starting values X 1.0, T 0.6, S 0.25, P 0.15, C 0; shadow 4 weeks and 300+ graded BUYs; both migrations approved |
| P1 | Kind and concept tags on every signal (`scoring/kinds.py`), no score change |
| P2 | Migration `20261006000001` written, **not applied**; writes gated by `SIGNALS_WRITE_HIT_KINDS` |
| P3 | Detector hygiene (changes scores): MACD/RSI/OBV/BB/regime fixes. Real-data flip report, 40 tickers: 7.4% of bars change action, BUY hit rate 0.504 to 0.506 |
| P4 | 7 event detectors, in `get_experimental_detectors()` only |
| P5 | `scripts/eval_signals.py` and `scoring/evidence.py`; full-universe run done (939 tickers) |
| P6 | `GradedConfluenceRanker`, shadow mode (off), migration `20261006000002` written, **not applied** |
| P7 | Machinery only: `SIGNALS_RANKER=graded` refuses to start without thresholds derived from shadow data. **Not flipped** |
| P8 | Rung-3 features and an AUC/leakage gate; smoke retrain not adopted. **No model published** |

Tests: 495 passing. Nothing changes production behavior unless an env flag is set.

## Measured results

- **P3 flip report** (40 tickers, ~7.9k bars): BUY hit rate 0.504 (old) vs 0.506 (new), SELL 0.497 vs 0.501, unconditional up-rate 0.524. P3 did not hurt; both rankers' BUY rate is below the up-rate.
- **Evaluator** (`calibration/evidence/evidence-2026-10-06.json`, not activated): E > 0 for `ichimoku_tk`, `macd_zero`, `kumo_twist`, `rsi_divergence`, partly `vol_divergence`. Weak states and breaches got E = 0.
- **`fit-k`, baseline evidence:** score vs forward return rank correlation negative (holdout -0.046 start, -0.040 fitted).
- **`fit-k`, earned evidence:** positive but small (fit +0.046, holdout +0.014 at start K, +0.011 at fitted K). The fit does not beat the start on the holdout, so the starting K stays.
- **Rung-3 smoke, 60 symbols:** rank IC 0.0014 vs rung 2 0.0114, ship bar missed, no model written.

## Where it differs from the spec

New events live in an experimental list (the old rankers would otherwise vote on them); shadow scores go in a new `confluence_shadow` table; the category bonus is scaled by E; `MACD HIST TURN` replaces the redundant sign-flip event; graded mode stamps `code_version` with `+graded`; P3 skips rows #44/#45 already cover.

## Distrust these

- Evidence file: current-constituents universe (survivorship), z ignores cross-ticker correlation, one holdout year. `ichimoku_tk` (+4.5pp) contradicts the earlier 62-ticker pilot.
- The scheduled scan fetches `3mo` (~63 bars), so SMA-200 and Ichimoku features are mostly missing from cron shadow rows.
- `REF_EDGE_PP`, the K grid and the shadow thresholds (0.20 / -0.20) are untuned starting values.
- The P3 hit-rate comparison is a 40-ticker sample on overlapping windows.
- Not run: the migrations, workflow dispatch, shadow `report` / `thresholds`, the cutover and any publish. Their commands were checked against the code only.

## Manual TODOs

Every item is yours: each needs the Supabase dashboard, a decision, or time to pass. Run from `~/code/signals-app-graded`.

### 1. 🖱 Apply migration 1 (`detector_hits` kind/concept/context)

**Dashboard:** Supabase project, SQL Editor. Additive only: three nullable columns.
```bash
pbcopy < ~/code/signals-app-graded/supabase/migrations/20261006000001_detector_hits_kind.sql && echo copied
```
Expect: `copied`. Paste into the editor and run, then verify:
```bash
cd ~/code/signals-app-graded && set -a && . ~/code/signals-app/.env && set +a && mamba run -n signals-app python -c "
import os, httpx
u, k = os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_ROLE_KEY']
r = httpx.get(f'{u}/rest/v1/detector_hits?select=kind,concept,context&limit=1', headers={'apikey': k, 'Authorization': f'Bearer {k}'})
print(r.status_code, 'columns exist' if r.status_code == 200 else r.text[:100])
"
```
Expect: `200 columns exist`. At last check (this session) it returned 400, i.e. not applied.

### 2. 🖱 Apply migration 2 (`confluence_shadow` table)

**Dashboard:** same SQL Editor. Creates one new table and one index; nothing else changes.
```bash
pbcopy < ~/code/signals-app-graded/supabase/migrations/20261006000002_confluence_shadow.sql && echo copied
```
Verify:
```bash
cd ~/code/signals-app-graded && set -a && . ~/code/signals-app/.env && set +a && mamba run -n signals-app python -c "
import os, httpx
u, k = os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_ROLE_KEY']
r = httpx.get(f'{u}/rest/v1/confluence_shadow?select=id&limit=1', headers={'apikey': k, 'Authorization': f'Bearer {k}'})
print(r.status_code, r.text[:60])
"
```
Expect: `200 []`. At last check it returned 404.

### 3. Free a PR slot, then open the PR

The repo has 3 open PRs. Merge or park #44 and #45 first (they contain P3 rows 3.1/3.5 and the Kumo removal; this branch will conflict in `config.py` and `detection/trend.py`, resolutions are small).
```bash
gh pr list --repo adamaslan/signals-app --json number,title,mergeable,reviewDecision
```
Expect: the three PRs above with their state. After one merges, rebase this branch:
```bash
cd ~/code/signals-app-graded && git fetch origin main && git rebase origin/main
```
Expect: conflicts only in `src/signals_app/config.py` and `src/signals_app/detection/trend.py`. The PR must include the wiki ingest if the repo has a `docs/wiki-*` folder.

### 4. Turn on shadow collection (in the PR, after steps 1 to 3)

📝 **File edit:** in `.github/workflows/signals-scan.yml`, add to the `env:` of each "Run scan" step: `SIGNALS_SHADOW_GRADED: "1"`, `SIGNALS_WRITE_CONFLUENCE_SHADOW: "1"`, `SIGNALS_WRITE_HIT_KINDS: "1"`. The cron fetches `3mo`; dispatch a long-period full scan for representative rows:
```bash
gh workflow run signals-scan.yml -f full_universe=true -f period=1y -f dry_run=false
```
Expect: `Created workflow_dispatch event`. Verify rows land after the run:
```bash
cd ~/code/signals-app-graded && set -a && . ~/code/signals-app/.env && set +a && mamba run -n signals-app python -c "
import os, httpx
u, k = os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_ROLE_KEY']
r = httpx.get(f'{u}/rest/v1/confluence_shadow?select=ticker,action,score&limit=3', headers={'apikey': k, 'Authorization': f'Bearer {k}'})
print(r.status_code, r.json())
"
```
Expect: `200` and up to 3 rows.

### 5. Re-test `ichimoku_tk` before trusting its evidence

It earned the largest E but contradicts the earlier pilot and the sample is survivor-biased. Read-only; also re-run with a different seed:
```bash
cd ~/code/signals-app-graded && PYTHONPATH=src mamba run -n signals-app python scripts/eval_signals.py --cache /tmp/sigcache --sample-size 300 --seed 7 --step 5 --out /tmp/evidence-seed7.json | tail -3
```
Expect: `N tickers, ... labels`. Compare `concepts.ichimoku_tk.fit.edge_pp` in `/tmp/evidence-seed7.json` against the committed file; a large drop means the +4.5pp is sample-specific.

### 6. After 4 weeks and 300+ graded BUYs: judge the shadow

```bash
cd ~/code/signals-app-graded && set -a && . ~/code/signals-app/.env && set +a && PYTHONPATH=src:scripts mamba run -n signals-app python scripts/graded_shadow.py report --horizon 21 --since 2026-10-07 --out /tmp/shadow-report.json | tail -40
```
Expect: JSON with `production`, `graded`, `flips`, `cutover.ready` and `reasons`. Read `flips.examples` yourself; the gate cannot sign that off.

### 7. ⚠️ Cutover (only if step 6 is ready and you decide to)

Derive thresholds, then dry-run the switch:
```bash
cd ~/code/signals-app-graded && set -a && . ~/code/signals-app/.env && set +a && PYTHONPATH=src:scripts mamba run -n signals-app python scripts/graded_shadow.py thresholds --since 2026-10-07
```
Expect: JSON and `wrote calibration/evidence/thresholds-<date>.json` (exits 1 with a reason if data is too thin).
```bash
cd ~/code/signals-app-graded && SIGNALS_RANKER=graded SIGNALS_THRESHOLDS_FILE=$(ls calibration/evidence/thresholds-*.json | tail -1) PYTHONPATH=src mamba run -n signals-app python scripts/scan_universe.py AAPL --period 1y --dry-run 2>&1 | tail -3
```
Expect: a published/gated result line. Before flipping live: sweep the consumers listed in `docs/graded-confluence-rollout-2026-10-06.md` Step 7 (about 14 scripts still call `ConfluenceRanker` directly; web and mobile read the score on other scales), and re-run calibration on the `+graded` code version (`scripts/calibrate_supabase.py --help` first; its flags were not verified). Rollback: unset `SIGNALS_RANKER`.

### 8. Rung-3 retrain on the full universe (only after step 7)

No publish until it adopts:
```bash
cd ~/code/signals-app-graded && PYTHONPATH=src:. mamba run -n signals-app python scripts/train_scorer.py --rung3 --horizon 20 --out /tmp/scorer-rung3.json --report-dir /tmp 2>&1 | tail -20
```
Expect: `rung3 adopted` or `rung3 NOT adopted:` with reasons (AUC gain under 0.005, AUC over 0.60 as a leakage flag, or ship bar missed). The 60-symbol smoke was not adopted.

## DB commands (`scripts/db_tool.py`), ranked

Added this session, tested against a fake runner (22 tests). **It could not be run against the live database:**
`SUPABASE_ACCESS_TOKEN` in `~/code/signals-app/.env` returns 401 (expired or revoked), there is no `DATABASE_URL` for
this project, and no Postgres driver is installed. The data is not precious (your call), so the only blocker on
applying the migrations from the terminal is a working credential.

Fix the credential first. Make a token at https://supabase.com/dashboard/account/tokens, then write it into `.env`
without echoing it or putting it in chat:
```bash
read -s -p "Supabase access token: " T; echo; sed -i '' "s|^SUPABASE_ACCESS_TOKEN=.*|SUPABASE_ACCESS_TOKEN=$T|" ~/code/signals-app/.env; unset T
```
Expect: a silent prompt, then no output. Check it works (read-only, prints routes and migration state):
```bash
cd ~/code/signals-app-graded && set -a && . ~/code/signals-app/.env && set +a && PYTHONPATH=src mamba run -n signals-app python scripts/db_tool.py status
```
Expect: JSON like `{"route": "api", "migrations": {"20261006000001_detector_hits_kind.sql": false, ...}}`. A 401 message means the token is still bad.

Every command below is a block you paste after sourcing the env (`set -a && . ~/code/signals-app/.env && set +a`), from `~/code/signals-app-graded`,
prefixed with `PYTHONPATH=src mamba run -n signals-app python`. Anything that writes needs `--yes`; reads never do.

| Rank | Command | What it does | Verdict |
|---|---|---|---|
| 1 | `scripts/db_tool.py status` | Reports the route used and whether each recent migration's objects exist | **Recommended.** Read-only; run it first and after every change |
| 2 | `scripts/db_tool.py migrate --since 20261006 --dry-run` | Lists the migration files that would run | **Recommended.** Read-only preview |
| 3 | `scripts/db_tool.py migrate --since 20261006 --yes` | Applies both new migrations in order, records each in `applied_migrations` | **Recommended.** Both are additive and idempotent (`if not exists`), so a re-run is safe |
| 4 | `scripts/db_tool.py tables` | Public tables with approximate row counts | **Recommended.** Read-only |
| 5 | `scripts/db_tool.py query "select ..."` | One read-only statement; refuses anything that could write, including `select ... into` and stacked statements | **Recommended.** Use for the verify checks in todos 1, 2, 4 |
| 6 | `scripts/db_tool.py apply supabase/migrations/<file>.sql --yes` | Applies one named migration file | OK. Use when you want one migration, not the batch |
| 7 | `scripts/db_tool.py purge-shadow --yes` | `delete from confluence_shadow`: clears shadow rows | OK once the table exists. Handy to reset after changing the ranker or evidence. Loses shadow history, which you said is fine |
| 8 | `scripts/db_tool.py exec "..." --yes` / `exec -f file.sql --yes` | Runs any statement | **Use with care.** Not recommended for routine use: no dry run, no undo. Prefer the specific commands above |
| 9 | `exec "truncate detector_hits, forward_returns" --yes` | Wipes the calibration inputs | **Not recommended.** `forward_returns` and `detector_hits` feed calibration and the shadow join; clearing them resets calibration history and the `strength_hit_rates` table is rebuilt from nothing. Only worth it if you are deliberately starting over |
| 10 | `exec "drop table confluence_shadow" --yes` | Removes the shadow table | **Not recommended.** Rollback for migration 2 only; it destroys shadow data and `write_confluence_shadow` fails if the flag is still on |
| 11 | `exec "alter table detector_hits drop column kind, drop column concept, drop column context" --yes` | Rollback for migration 1 | **Not recommended.** Same: turn `SIGNALS_WRITE_HIT_KINDS` off first or every scan write errors |
| 12 | `exec "delete from signals" --yes` (or truncate) | Empties published signals | **Not recommended, and the data is not the issue.** The web app and API read this table, so the site goes blank until the next scan. Also cascades from `engine_runs` |
| 13 | `--route postgres` with a pasted `DATABASE_URL` | Direct SQL instead of the Management API | Fallback only. Needs `psycopg` installed and a connection string in env; skip if the token works |

Order to run them: 1, 2, 3, 1 again, then 5 for the todo verify checks. Everything from rank 8 down is
optional and mostly destructive.

## Permissions and approvals needed

Checked on this machine today (2026-10-06): what is already in place, what is missing, and what only you can grant.
"Approval" rows are things Claude will not do without an explicit yes in the session, per the global safety and
outward-action rules.

### Account and tooling access

| Needed for | Status | Check |
|---|---|---|
| GitHub: push branch, open and merge PRs, dispatch workflows (todos 3, 4) | **In place.** `gh` is logged in as `adamaslan` with scopes `repo` and `workflow`, and has admin on `adamaslan/signals-app` | `gh auth status` and `gh api repos/adamaslan/signals-app --jq .permissions` |
| GitHub Actions secrets `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (todo 4 scan writes) | **In place** (both set 2026-08-19) | `gh secret list --repo adamaslan/signals-app` |
| Supabase service-role key in `~/code/signals-app/.env` (todos 1, 2, 4, 6, 7 read checks) | **In place** (names present; values never printed) | `grep -oE '^(SUPABASE_URL\|SUPABASE_SERVICE_ROLE_KEY)=' ~/code/signals-app/.env` |
| A working DDL route to Supabase (todos 1, 2) | **Missing.** `SUPABASE_ACCESS_TOKEN` in `.env` returns 401, there is no `DATABASE_URL`, no Supabase CLI or `psql`. Either paste the SQL in the dashboard, or fix the token and use `scripts/db_tool.py` (section above) | Open the project in the dashboard and confirm the SQL Editor runs a write |
| `pbcopy`, `mamba` env `signals-app`, network to yfinance (todos 1 to 8) | In place | `which pbcopy && mamba env list \| grep signals-app` |
| Disk and CPU for the evaluator and retrain (todos 5, 8) | In place; `/tmp/sigcache` holds ~940 cached price files, cleared on reboot | `ls /tmp/sigcache \| wc -l` |
| A free slot under the 3-PR cap (todo 3) | **Missing.** #43, #44, #45 are open | `gh pr list --repo adamaslan/signals-app` |
| Merge rights on #44 and #45 | In place (admin), but merging is your decision | |

If you want to stop pasting SQL by hand later, the missing piece is a Supabase **database connection string** (or the
Supabase CLI logged in with a personal access token) kept in a local env file, never in chat. Then migrations can be
applied from the terminal after a confirmation. That is optional and not set up.

### Approvals Claude needs from you, per action

| Action | Why it needs an explicit yes | Todo |
|---|---|---|
| Apply either migration to production Supabase | Schema change on a live database. Approved in principle this session, but you run it | 1, 2 |
| Merge or close PR #44 or #45 | Changes `main`; also makes this branch conflict | 3 |
| Open the PR for this branch | Outward-facing; needs a slot and the wiki ingest | 3 |
| Edit `.github/workflows/signals-scan.yml` to set the three shadow flags | Changes what the scheduled production scan writes | 4 |
| Dispatch `signals-scan.yml` with `dry_run=false` | A real scan writes rows and spends LLM tokens | 4 |
| Flip `SIGNALS_RANKER=graded` anywhere live | Changes published scores | 7 |
| Re-run calibration on the `+graded` code version | Writes calibration rows | 7 |
| Publish a retrained model (`train_scorer.py --publish`) | Activates a model in Supabase | 8 |
| Any `git push --force`, `git reset --hard`, or deleting `/tmp/sigcache` evidence while a run needs it | Destructive-state rule | any |

Already approved this session: P0 decisions (K starting values; 4 weeks and 300+ graded BUYs; both migrations in
principle), local read-only runs, committing results to this branch, and pushing this branch. Not approved: the PR,
the workflow edit and the cutover.

### Optional Claude Code permission allowlist

To avoid per-command prompts for the read-only commands used above, add these to the project's
`.claude/settings.json` (`fewer-permission-prompts` can generate it). Do **not** allowlist `gh pr merge`,
`gh workflow run`, `git push`, or anything that writes to Supabase:
```json
{"permissions": {"allow": [
  "Bash(gh pr list:*)", "Bash(gh pr view:*)", "Bash(gh auth status)", "Bash(gh secret list:*)",
  "Bash(gh run list:*)", "Bash(git status:*)", "Bash(git log:*)", "Bash(git fetch:*)",
  "Bash(mamba run -n signals-app python -m pytest:*)", "Bash(pbcopy:*)"
]}}
```

## Optional

📓 Record this session's caveats durably with `/cave` (the run shipped with unexecuted gated steps and untuned constants).
