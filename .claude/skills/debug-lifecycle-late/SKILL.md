---
name: debug-lifecycle-late
description: Finish a debugging cycle correctly — build and rank a hypothesis ledger, bisect regressions, divide-and-conquer multi-component bugs, write a tested root-cause statement, run five-whys, sweep for sibling defects, design and implement a minimal fix (code or data), independently verify it, confirm it in production, write the PR evidence pack, then run a blameless postmortem and turn the root cause into guardrails. Covers phases 4-8 of the full debug cycle. Use once a bug is reproduced and has an evidence timeline (see debug-lifecycle-early), through to shipping and capturing the lesson.
---

# Debug Lifecycle — Isolation, Root Cause, Fix, Verification, Learning (Phases 4–8)

Pick up with the repro and evidence timeline from **debug-lifecycle-early**. **Rule across these phases:** no fix before a repro, no root cause without evidence that explains every observation, and no "done" without PASS/FAIL/UNVERIFIED and where the evidence came from.

---

## Phase 4: Isolation & Hypotheses

### Hypothesis Ledger

```text
Using the evidence timeline for <bug>, build a hypothesis ledger.

For each hypothesis:
- Statement: one sentence, specific enough to be wrong.
- Evidence for and evidence against, citing timeline items.
- The cheapest test that would falsify it, and its expected result if the hypothesis is true.
- Status: OPEN, RULED OUT (with the test that ruled it out), or CONFIRMED.

Rank by (likelihood × cheapness to test). Run the top test, update the ledger, and repeat. Keep ruled-out hypotheses in the ledger with their evidence. That negative knowledge is the most valuable part of the handoff.
```

### Bisect the Regression

```text
<bug> is a regression: it works at <good commit/date> and fails at <bad commit/date>.

Bisect it:
- Write a script that exits 0 on good and 1 on bad, using the minimal repro.
- Run `git bisect start <bad> <good>` and `git bisect run <script>`.
- If the bisect hits commits that cannot build or run, `git bisect skip` them and report them.
- If code bisect is inconclusive, bisect the other axes: dependency versions, config/flag history, and data (which input rows trigger it).

Output: the first bad commit (or config change / dependency / data slice), its diff summary, the bisect log, and why that change explains the symptom. Leave the repo on the branch you started on.
```

### Divide and Conquer the System

```text
<bug> spans multiple components: <list>. Isolate the faulty one.

At each boundary, replace one side with a known-good stand-in (a fixture, recorded response, stub, or golden file) and re-run the repro:
- If the bug persists, the fault is downstream of the boundary.
- If it disappears, it is upstream.

Halve the search space each time. Output: the boundary map, each substitution made, the result, and the single component (and function) that remains. Do not modify production code; stand-ins live in tests or a scratch harness.
```

---

## Phase 5: Root Cause

### Root-Cause Statement

```text
Write the root-cause statement for <bug>.

It must include:
- The defect: what the code or data does, at file:line.
- The trigger: the exact condition that activates it.
- The mechanism: how the trigger produces the observed wrong value, step by step.
- Why it was not caught: missing test, wrong assumption, silent fallback, or unmonitored path.
- Confidence: HIGH (reproduced and explained end to end), MEDIUM, or LOW, with what would raise it.

Test it: every item in the evidence timeline must be explained by this statement, or listed as unexplained. If anything is unexplained, the root cause is incomplete. Say so.
```

### Five Whys with Evidence

```text
Run a five-whys analysis on <bug>, starting from the observed symptom.

For each "why":
- The answer, as a factual statement.
- The evidence that supports it (log line, query result, file:line, commit).
- Whether it is a FACT or an ASSUMPTION.

Stop when you reach a cause that is actionable and systemic (a process, a missing guard, or a design choice), not a person. Flag any link in the chain that rests on an assumption, and give the check that would turn it into a fact.
```

### Sibling Defect Sweep

```text
The root cause of <bug> is <root-cause statement>. Find every other place with the same defect.

Search for:
- The same pattern: same API misuse, same unchecked assumption, same copy-pasted block.
- The same data path in other surfaces (web, mobile, API, jobs) and other repos that share the module.
- The same vendor/config dependency used elsewhere.

For each hit: file:line, whether it is affected today (with evidence) or latent, and severity. Output the sweep list. The fix is not complete until every affected sibling is fixed or explicitly parked with a reason.
```

---

## Phase 6: Fix

### Minimal Fix Design

```text
Design the fix for <root cause> before writing it.

Compare up to three options:
- Minimal fix at the defect site.
- Fix at the boundary that should have caught it (validation, type, contract).
- Structural fix that removes the class of bug.

For each: diff size, files touched, risk, what it protects against, and what it does not. Recommend one, and say why. Flag any option that touches auth, sessions, CSRF, rate limiting, crypto, webhook signatures, payments, or migrations. Those need explicit human confirmation before implementation.
```

### Fix with a Failing Test First

```text
Implement the chosen fix for <bug>.

Steps:
1. Convert the minimal repro into a regression test. Run it and capture the failure.
2. Apply the smallest change that implements the chosen design.
3. Re-run the regression test and capture the pass.
4. Run the impacted test suites and the type checker/linter.
5. Remove all temporary instrumentation (search for the `DEBUG-<ID>` markers).

Do not weaken assertions, skip tests, widen catch blocks, or add a fallback that hides the error. Output: the diff, the failing → passing logs, the suite results, and confirmation that zero debug markers remain.
```

### Data Repair Plan

```text
<bug> wrote bad data to <store>. Plan the repair. Do not run any writes.

Produce:
- A read-only query that identifies every affected row/document, with the current count.
- How to compute the correct value for each one, and the source of truth used.
- The repair as a dry-run script that prints the planned changes without writing.
- A backup/snapshot step that runs before the real write.
- A verification query that proves the repair worked.
- The rollback procedure if the repair is wrong.

Run only the read-only and dry-run steps. The real write requires explicit owner confirmation.
```

---

## Phase 7: Verification & Release

### Independent Verification

```text
Verify the fix for <bug> as if you did not write it.

Check:
- The original repro from Phase 2 now produces the expected output (run it, capture it).
- The regression test fails when the fix is reverted (revert locally, run, restore).
- Every sibling from the defect sweep is fixed or parked.
- Every item in the evidence timeline is consistent with the fix.
- Feature-flag ON and OFF paths both behave correctly.
- No unrelated files changed.

Mark each check PASS, FAIL, or UNVERIFIED, and say where the evidence came from: local, CI, or production. "Verified" without a location is not verified.
```

### Production Confirmation

```text
The fix for <bug> is deployed as <deploy ID / commit>. Confirm it in the environment where the bug occurred.

Do:
- Confirm the deployed commit includes the fix (check the deploy, not the branch).
- Re-run the original repro against that environment using read-only checks.
- Compare the error rate and wrong-value counts before and after the deploy.
- Confirm the data repair (if any) by running its verification query.
- Watch for <time window> for any new errors on the touched paths.

Output: before/after numbers, the commands used, and a verdict of FIXED IN PROD, FIXED LOCALLY ONLY, or NOT FIXED. A fix that passes in CI but has not been checked in production is FIXED LOCALLY ONLY.
```

### Fix PR Evidence Pack

```text
Write the PR description for the fix to <bug>, for a reviewer who has not seen the investigation.

Include:
- The symptom, with the concrete wrong value.
- The root-cause statement.
- The fix and why this option was chosen over the alternatives.
- Evidence: failing → passing test output, repro before/after, and links to CI artifacts.
- The sibling sweep: what was fixed, and what was parked and why.
- Data repair status, if any.
- Risks, rollback steps, and anything left UNVERIFIED.

Summarize the evidence; do not paste the diff or commit messages. Redact secrets and PII.
```

---

## Phase 8: Learning & Prevention

### Blameless Postmortem

```text
Write a blameless postmortem for <bug / incident>.

Sections:
- Summary: what happened, the impact, and the duration.
- Timeline: detection, triage, mitigation, root cause found, fix deployed, confirmed fixed.
- Root cause, from the Phase 5 statement.
- What went well and what slowed us down, especially the dead ends and why they looked promising.
- Detection gap: how long the bug existed before anyone noticed, and why.
- Action items: each with an owner, a due date, and the type (prevent / detect / mitigate).

Focus on systems and process, not people. Keep the ruled-out hypotheses. They stop the next person from repeating them.
```

### Prevention Guardrails

```text
Turn the root cause of <bug> into guardrails so this class of bug cannot recur silently.

Propose, in order of cost:
- A test or property-based test that covers the whole class, not just this case.
- A type, schema, or contract check that rejects the bad state at the boundary.
- A lint rule, CI check, or pre-commit hook that flags the pattern.
- A runtime assertion or alert that fires on the wrong value in production.
- A source/provenance field on data, so mixed or stale sources are visible.

For each: what it catches, the false-positive risk, and the effort. Implement the cheapest one that would have caught this bug. Then prove it would have: run it against the pre-fix commit and show it fails.
```

### Debugging Knowledge Capture

```text
Capture what this debugging cycle taught us about <bug>, so the next session starts ahead.

Write durable records (not chat):
- Incident/wiki page: symptom, root cause, fix, and the exact error strings people will search for.
- Caveats: anything shipped with an edge (partial verification, a parked sibling, an arbitrary threshold, fixed locally but not confirmed in production).
- Manual TODOs: anything only a human can do, as copy-paste commands or marked dashboard steps.
- Runbook update: how to detect, mitigate, and verify this failure next time.
- Debug tooling: any scratch harness, query, or repro script worth keeping, proposed as a repo script.

Never write real keys, tokens, URLs, or IDs. Name the variable instead. Output the list of files written and one line each on why a future reader needs it.
```

## Chains

**Full cycle (with debug-lifecycle-early):** Intake → Repro → Evidence/Write-trace → Hypothesis Ledger → Bisect/Divide-and-Conquer → Root-Cause Statement → Sibling Sweep → Minimal Fix Design → Fix-with-Test-First (→ Data Repair) → Independent Verification → Fix PR Evidence Pack → Production Confirmation → Postmortem → Prevention Guardrails → Knowledge Capture.

**Short loop for small bugs:** Deterministic Repro → Hypothesis Ledger → Root-Cause Statement → Fix-with-Test-First → Independent Verification.
