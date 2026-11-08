---
name: ops-security-data-delegation-docs
description: The broad operational toolkit — data quality audits and vendor fallback chains, schema/data migration plans, provenance/freshness, threat modeling, secrets hygiene, dependency/supply-chain review, PII/retention review, observability design, runbooks, backup/restore drills, live incident command, delegation briefs for cheaper models, parallel worktree planning, cross-model PR verification, LLM eval harnesses, documentation accuracy audits, non-expert change explainers, honest status updates, and user-facing message/accessibility review. Use for data pipeline issues, security/privacy reviews, pre-launch operational readiness, live outages, multi-agent delegation, or writing docs/status updates that must stay honest about what's unverified.
---

# Data/Pipelines, Security/Privacy, Operations/Reliability, Delegation/AI, Docs/Communication (Areas E–I)

House rules across every prompt here: read-only before write, no secret values (name the variable), and every claim marked PASS / FAIL / UNVERIFIED with where the evidence came from (local, CI, or production).

---

## Area E: Data, Pipelines & Vendors

### Data Quality Audit

```text
Audit the data in <table / collection / feed> using read-only queries.

Check: row counts and the latest timestamp, nulls and empty strings per column, duplicates on the natural key, impossible values (negative prices, future dates), distribution shifts against last week, orphaned references, and rows with no recorded source.

Output a findings table: check, query, result, expected, status. For each failure, say which writer most likely produced it (write-path trace style). Propose invariants that should be enforced at write time. Do not modify any data.
```

### Vendor Fallback Chain

```text
Design or review the fallback chain for <data need> across vendors <list>.

Define the order, per host: for each, which vendors work from there (cloud IPs get blocked), their limits, and their cost. For each step: the trigger for falling through, the retry and backoff policy, and the log line written when it happens.

Requirements: every result carries the vendor that produced it, mixed-vendor data is never indistinguishable, a skipped input is stated in the run summary, and a host where no vendor works fails closed. Output the chain table and a test that forces each fallback.
```

### Migration Plan (Schema or Data)

```text
Plan the migration for <schema or data change>. Do not run it.

Cover:
- The current and target shapes, and the exact diff.
- Order of operations that keeps old and new code both working during rollout (expand → migrate → contract).
- A dry run against a copy or a branch database, with the row counts expected.
- A backup or snapshot step and the restore command, tested on the copy.
- Locking, duration, and traffic impact.
- Verification queries before and after.
- The rollback, and the point of no return.

Migrations are a sensitive surface: stop and get my explicit confirmation before any real write.
```

### Provenance and Freshness

```text
Make the data in <feature> honest about where it came from and how old it is.

Add or verify, for every value shown or stored: the source, the feed or version, the fetch time, and the staleness threshold. Define what the UI and API do when data is stale, missing, or from a fallback (label it, degrade it, or hide it). Show where stale data is currently presented as fresh.

Output the gap list, the schema change for provenance fields, and a check that fails when a value is older than its threshold. Test with a deliberately stale row.
```

---

## Area F: Security & Privacy

### Threat Model

```text
Threat-model <feature or system>.

Draw the data flow in text: actors, trust boundaries, entry points, stores, and third parties. For each boundary, list what could go wrong using spoofing, tampering, repudiation, information disclosure, denial of service, and privilege escalation as prompts.

For each threat: likelihood, impact, the existing control (with file:line), and the gap. Rank them. Propose controls for the top 5 and mark which need human sign-off. Do not touch auth, session, CSRF, rate limiting, crypto, or webhook code without confirmation.
```

### Secrets Hygiene Audit

```text
Audit <repo> for secrets exposure. Never print a secret value; report variable names, file paths, and line numbers only.

Check:
- Tracked files and git history for key-shaped strings and `.env*` files.
- Docs, logs, test fixtures, and CI output that contain real-shaped tokens.
- Which secrets exist in which hosts (local, CI, cloud), and which are missing or duplicated.
- Overbroad scopes: keys with more permission than the code uses.
- `.gitignore` and pre-commit coverage.

Output findings with severity. For any exposed secret, state the rotation steps as copy-paste commands, but do not rotate or delete anything without my confirmation.
```

### Dependency and Supply Chain Review

```text
Review the dependencies of <repo>.

List direct dependencies with: version, last release date, maintainer count or activity, known advisories (from the package audit tool), license, and whether it is still used. Flag packages that are abandoned, pinned to a vulnerable range, installed but unused, or pulled in for one small function.

Also check install scripts, lockfile integrity, and CI actions pinned by tag instead of by SHA. Output a ranked list with the action for each: update, replace, remove, or accept with a reason. Run the audit and tests before and after any bump. Update one dependency per commit.
```

### PII and Data Retention Review

```text
Map the personal data in <system>.

For each data type (email, name, IP, payment reference, device ID, free-text user input): where it is collected, stored, logged, sent to third parties, and cached. Then check: is it needed, how long is it kept, can a user export or delete it, and does it appear in logs, analytics, error reports, or AI prompts.

Output a table with the lawful need, the retention rule, and the gap. Propose the smallest changes that remove unneeded collection and add redaction in logs. Flag anything that needs a legal or owner decision. This is not legal advice. Mark it for human review.
```

---

## Area G: Operations & Reliability

### Observability Design

```text
Design what we need to see for <service or pipeline>.

Define:
- The 3–5 questions an on-call person must be able to answer in a minute ("is it up", "is it correct", "is it slow", "is the data fresh", "what changed").
- For each, the metric, log line, or trace that answers it.
- Alerts: threshold, window, severity, and who is paged. Each alert has a runbook link and a stated reason it would be actionable. Remove any alert nobody would act on.
- Correctness signals, not only uptime: a job that runs but writes nothing is down.

Output the signal table and add the smallest set of instrumentation that fills the biggest gaps. Verify each alert by forcing it to fire once.
```

### Runbook Writer

```text
Write the runbook for <failure or recurring task>.

Format for someone who is stressed and has never seen this system:
- Symptoms: the exact alert text or error string.
- Quick check: a copy-paste command with the expected output.
- Decision: if X, do A; if Y, do B.
- Mitigation steps, numbered, one command block each, with the expected output after each.
- Verification: how to know it is fixed.
- Escalation: who to contact, and when to stop.

Verify every command, path, and flag against the code or by running it read-only. A runbook with an unverified command is marked DRAFT. Mark which steps are destructive and require a warning first.
```

### Backup and Restore Drill

```text
Prove we can recover <data store or system>. A backup you have not restored is a guess.

Do:
- List what is backed up, how often, where, who can read it, and how long it is kept.
- Identify what is not backed up that should be (secrets store, config, uploaded files, DNS).
- Restore the latest backup into a scratch location (never over the live system) and run integrity checks: counts, checksums, and a sample of real records.
- Time the restore.

Output: recovery point (how much data could be lost), recovery time, gaps, and the restore command sequence. Mark the drill PASS only if the restored data was verified.
```

### Live Incident Commander

```text
Something is broken in production right now: <symptom>. Mitigate first, diagnose second.

In order:
1. State impact in one sentence and who is affected.
2. List the fastest reversible mitigations: flip a flag, roll back the last deploy, pause a job, scale up. Rank by speed and risk. Recommend one.
3. Before any action that could destroy state (kill a process, drop data, restart something holding credentials), stop and warn me, and wait for confirmation.
4. After mitigation, confirm impact has stopped with a number, not a feeling.
5. Keep a running timestamped log of every action taken and every observation.

Only then hand off to the full debugging cycle (see debug-lifecycle-early / debug-lifecycle-late) using that log as the intake record.
```

---

## Area H: Delegation & AI Features

### Delegation Brief for a Cheaper Model

```text
I want to hand <task> to a cheaper model or subagent. Write the brief.

The brief must stand alone and include:
- The exact objective and the output format, with a length limit.
- The files or paths to read (ranges, not whole files) and the ones not to touch.
- What it must not do: commit, push, open PRs, edit env files, call paid APIs, or make irreversible changes.
- The check I will use to verify its result, and the evidence it must return (file:line, command output).
- When to stop and report instead of guessing.

Decide first whether the task is mechanical enough to delegate. If it needs judgment, say so and keep it. Never delegate auth, session, payments, or migrations.
```

### Parallel Work Plan with Worktrees

```text
I have <N> units of work to do at once: <list>. Plan them so they do not collide.

For each unit: the branch name, the worktree path, the files it owns, and the files it shares with other units. Cap at 3 open PRs. Order merges by shared-file overlap, highest first, and say when each remaining branch rebases. Put high-churn files (indexes, logs, lockfiles, parity numbers) in their own last commit.

Check that no two sessions will share one working tree. Output the plan table, the `git worktree add` commands, and the merge order with reasons. If two units overlap heavily, recommend sequencing them instead.
```

### Cross-Model Verification Handoff

```text
<Model family A> wrote this change. You are model family B. Verify it; do not defend it.

Do:
- Read the diff and the PR description, then check each claim against the code.
- Run the tests, type checker, and linter locally and report the output.
- Look for the failure modes typical of the author model: confident wrong APIs, tests that assert nothing, fallbacks that hide errors, claims of "verified" without a location.
- Report findings as file:line with severity.

Output PASS / FAIL per claim, then a verdict: APPROVE, CHANGES REQUESTED, or BLOCKED. Put fixes in a new commit, not an amend. Sensitive surfaces need the owner's confirmation. Record author and verifier in the PR.
```

### LLM Feature Evaluation Harness

```text
We are shipping an AI-powered feature: <feature>. Build the evaluation before we tune the prompt.

Create:
- A test set of 30–50 real or realistic inputs with expected properties: typical, edge, adversarial, and out-of-scope cases.
- Checks that are as objective as possible: format validity, required fields, forbidden content, factual checks against known data, and a rubric for the rest.
- A baseline score for the current prompt and model, with run-to-run variance from at least 3 runs.
- A regression gate: the score below which a prompt or model change cannot ship.
- A cost and latency number per request.

Output the dataset, the runner command, and the baseline. Change the prompt only after the baseline exists, and report the before and after on the same set.
```

---

## Area I: Docs & Communication

### Documentation Accuracy Audit

```text
Check whether the docs for <repo or area> match the code.

For each doc (README, setup guide, runbook, API doc): run or trace every command, path, flag, route, and env var it names, and compare against the code. Report each as CORRECT, STALE (was true, no longer), or WRONG (never matched).

Output a table: doc, line, claim, status, evidence, correction. Fix the stale and wrong items in one commit. Do not delete outdated docs; move them to `docs/archive/` with an `ARCHIVED:` header and update the index. Mark anything you could not verify as UNVERIFIED.
```

### Change Explainer for a Non-Expert

```text
Explain <PR / release / session of work> to <audience: customer, manager, teammate in another area>. They do not know the codebase.

Cover:
- What changed, in terms of what they can see or do.
- Why it matters to them.
- What they need to do, if anything, as an exact step.
- What did not change, and any known limitation, stated plainly.
- What happens if something goes wrong, and who to contact.

No file names, no jargon, no unexplained acronyms. Under 200 words. Open with one line naming the audience as you understood it. Every claim must match the actual diff. Do not promise anything that is not verified.
```

### Status Update with Honest Edges

```text
Write a status update on <project> for <audience>.

Format:
- Done: only what is verified, with where it was verified (local, CI, production).
- In progress: current state and the next concrete step.
- Blocked: what, on whom, and what unblocks it.
- Risks and caveats: anything that shipped with an unverified edge, a parked item, or an assumption.
- Decisions needed from the reader, each phrased as a question with your recommendation.

Do not round up. "Mostly done" is replaced by the specific part that is not. Under 250 words. Put manual steps for the reader as copy-paste commands or direct URLs.
```

### User-Facing Messages and Accessibility Review

```text
Review the user-facing text and interaction of <screen / flow / CLI / email>.

Check:
- Error messages: do they say what happened, why, and what to do next, without codes or stack traces?
- Empty, loading, partial, and offline states exist and read well.
- Destructive actions state exactly what will be lost and require confirmation.
- Accessibility: keyboard reach and order, visible focus, labels on inputs, color contrast, text alternatives, screen reader names, motion preferences, and touch target sizes.
- Consistency of terms across screens.

Output a findings table: location, problem, the proposed text or fix, and severity. Verify accessibility claims with an automated checker plus a manual keyboard pass, and mark anything you could not test.
```

## Chains by situation

- **Bad data:** Data Quality Audit → Vendor Fallback Chain → Provenance and Freshness, then Migration Plan for the repair. Pair with the Data Repair Plan in **debug-lifecycle-late**.
- **Before launch:** Threat Model → Secrets Hygiene → Dependency Review → PII Review (security), Observability → Runbook → Backup/Restore Drill (operate), LLM Eval Harness (AI quality), Accessibility Review (users).
- **Live outage:** Live Incident Commander first, then the full debug cycle (**debug-lifecycle-early** / **debug-lifecycle-late**).
- **Run many agents:** Delegation Brief → Parallel Work Plan with Worktrees → Cross-Model Verification Handoff.
- **Close out:** Documentation Accuracy Audit → Change Explainer → Status Update.
