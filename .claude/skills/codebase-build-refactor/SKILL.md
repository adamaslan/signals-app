---
name: codebase-build-refactor
description: Understand an unfamiliar codebase (map it, trace a feature end to end, find source-of-truth vs duplicate logic, assess change impact) before building or refactoring it (spec-to-acceptance checklist, behavior-preserving refactor, archive-not-delete cleanup, API/contract design). Use when new to a repo, before editing something with unclear blast radius, before implementing a request, or before refactoring/cleaning up existing code.
---

# Codebase Understanding & Building/Refactoring (Areas A–B)

House rule: read before you write; cite file:line for every claim; mark anything inferred as INFERENCE.

---

## Area A: Codebase Understanding

### Codebase Map

```text
I'm new to <repo or directory>. Build a map before anyone changes anything.

Read ranges and search (rg, file lists), not whole files. Produce:
- Purpose in 2 sentences, and the entry points (apps, jobs, scripts, routes).
- The 5–8 modules that matter, each with one line on what it owns and what it depends on.
- Where state lives: databases, caches, files, queues, env vars (names only).
- How it is built, run, and tested, as copy-paste commands you have verified exist.
- What you could not figure out, listed as questions.

Cite file:line for every claim. Mark anything inferred from naming alone as INFERENCE. Under 400 words.
```

### Feature Trace

```text
Trace how <feature or user action> works end to end, from trigger to final effect.

Follow it hop by hop: UI or caller → API/handler → business logic → storage or external service → response and side effects (emails, jobs, webhooks, cache writes). For each hop give file:line, what it passes on, and what can fail there.

Output a numbered chain and a "failure points" list. Do not propose changes. Mark any hop you could not find code for as MISSING, and any hop you read but did not run as UNVERIFIED.
```

### Source-of-Truth and Duplicate Logic Finder

```text
For the concept <e.g. price, score, plan tier, permission check>, find every place the codebase defines, computes, or stores it.

List each location with file:line, whether it is the original or a copy, and whether the copies agree today (check, don't assume). Name the one place that should be the source of truth and the shortest path to make the others call it.

Output a table: location, role (source / copy / cache / derived), agrees (yes / no / unchecked), risk if it drifts. Do not refactor yet.
```

### Change Impact Analysis

```text
Before I change <function / file / schema / config key>, tell me what depends on it.

Find:
- Direct callers and importers, with file:line.
- Indirect dependents: other surfaces (web, mobile, jobs), other repos, scripts, docs, and tests.
- Runtime-only dependents that grep cannot see: reflection, string-built keys, env-driven switches, stored data shaped by the old behavior.
- Which tests would catch a break, and which dependents have no test.

Output blast radius as: certain, likely, possible. For every "possible", give the check that would settle it. State the smallest change that avoids touching the riskiest dependents.
```

---

## Area B: Building & Refactoring

### Spec to Acceptance Checklist

```text
Turn this request into a buildable spec before writing code: <paste request>.

Produce:
- Behavior as 5–10 Given / When / Then statements, including at least two error cases and one empty-state case.
- Inputs, outputs, and data shapes, with the exact types.
- What is out of scope.
- Open questions, each with the default you will use if I do not answer.
- A test for each statement, named but not yet written.

Ask me to approve the spec. Do not implement until I do.
```

### Behavior-Preserving Refactor

```text
Refactor <target> to <goal, e.g. split by responsibility, remove duplication>. Behavior must not change.

Steps:
1. Pin current behavior first: add characterization tests that capture today's outputs, including the odd ones. Run them and save the output.
2. Refactor in small commits, each leaving all tests green.
3. Do not mix in bug fixes or features. If you find a bug, log it and leave it.
4. Re-run the characterization tests after every step.

Output: the pinned-behavior tests, the commit-by-commit summary, before/after test output, and a list of bugs found but deliberately not fixed.
```

### Dead Code and Cleanup, Archive Not Delete

```text
Find code, files, flags, and docs in <path> that look unused.

For each candidate, prove it is unused: search imports, string references, config, routes, cron jobs, docs, and other repos. Classify as DEAD (no references anywhere), DORMANT (referenced only by other dead code or by a disabled flag), or LIVE (do not touch).

Do not delete anything. For DEAD and DORMANT items, propose moving them to `file-archive/` (code, data) or `docs/archive/` (docs) with an `ARCHIVED:` / `REASON:` header. Output the move list and wait for my approval.
```

### API and Contract Design

```text
Design the interface for <new endpoint / module / event> before implementing it.

Specify:
- Request and response shapes, with types and one realistic example each.
- Error cases: status codes or error types, and what the caller should do for each.
- Idempotency, pagination, ordering, and rate-limit behavior.
- Versioning: how this can change later without breaking callers.
- Auth: who may call it, and what they may see.
- A validation schema at the boundary that rejects bad input.

Compare against two existing interfaces in this codebase and match their conventions unless there is a stated reason to differ. Output the contract and the contract tests.
```

## Chains

- **Start in an unfamiliar repo:** Codebase Map → Feature Trace → Source-of-Truth Finder, then Change Impact Analysis before any edit.
- **Build a feature:** Spec to Acceptance Checklist → API and Contract Design (if applicable) → tests (see **testing-perf-cost** skill) → Change Impact Analysis → ship via **evidence-gated-delivery**.
- **Clean up safely:** Behavior-Preserving Refactor → Dead Code Cleanup, always paired with the Test Quality Check in **testing-perf-cost** to confirm tests can actually fail.
