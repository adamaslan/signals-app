---
name: debug-lifecycle-early
description: Start a debugging cycle correctly — normalize a raw bug report, triage severity/scope, check for known duplicates, build a deterministic or local-first repro, tame a flaky bug, then collect timestamped evidence and trace the write path. Covers phases 1-3 of the full debug cycle (Intake&Triage, Reproduction, Evidence&Instrumentation). Use at the start of any bug investigation, before any theory or fix is proposed.
---

# Debug Lifecycle — Intake, Reproduction, Evidence (Phases 1–3)

Hand off to **debug-lifecycle-late** (isolation → root cause → fix → verification → learning) once you have a reproduced bug and an evidence timeline. Carry forward: the repro command, the evidence timeline, and (if used) the write-path trace.

**Rule across these phases:** no theorizing before reproduction, no root cause without evidence that explains every observation.

---

## Phase 1: Intake & Triage

### Bug Report Normalizer

```text
Turn this raw report into a structured bug record. Do not investigate or fix yet.

Raw report: <paste user report / alert / screenshot description>

Produce:
- Expected behavior vs. observed behavior, each in one sentence.
- The concrete wrong value: the number, string, status code, or render, plus where it was seen.
- Environment: surface (web/mobile/API/job), host (local/CI/cloud), commit or deploy ID, feature-flag state, user/account type.
- First-seen time and whether it is still happening.
- Blast radius: one user, a cohort, or everyone.
- Unknowns: list every field you could not fill in, and the one question that would fill it.

If the report has no concrete wrong value, say so. "Looks wrong" is not a bug record yet.
```

### Severity and Scope Triage

```text
Triage bug <ID / record from Bug Report Normalizer>.

Classify:
- Severity: data loss/corruption, security/auth, money/payments, wrong numbers shown to users, degraded UX, cosmetic.
- Scope: which surfaces, hosts, and data paths are affected, and which are confirmed unaffected (with evidence).
- Trend: new regression, long-standing, or intermittent. Name the last known-good commit or date if you can.
- Urgency: is a kill switch, flag-off, or rollback warranted right now, before any root-cause work?

Output a triage card: severity, scope, trend, immediate mitigation (or "none needed" with the reason), and the owner. If severity is data loss, security, or money, stop and ask for human confirmation before any further action.
```

### Known-Issue and Duplicate Check

```text
Before debugging <issue>, check whether it is already known.

Search, in this order:
- Open and recently closed issues/PRs mentioning <error string / symptom / file>.
- The repo's manual-todo, caveats, incident, and wiki pages.
- Recent session handoffs and fix logs.
- `git log -S "<error string>"` and `git log --since=<date> -- <suspect paths>`.
- CI failure history for the same job or test.

For each match: link, how closely it matches (exact / likely / related), its status, and what was already ruled out. If an exact match exists with a recorded cause, stop and report it. Do not re-debug a diagnosed issue.
```

---

## Phase 2: Reproduction

### Deterministic Repro Builder

```text
Reproduce <bug> deterministically. Do not change production code.

Steps:
1. Write the exact repro: command, inputs, environment variables (names only, never values), commit, and flag state.
2. Run it and capture the wrong output verbatim.
3. Run it 3 more times. Report whether it fails every time, sometimes, or never.
4. Reduce it: remove inputs, steps, and dependencies until removing anything else makes the bug disappear.

Output: the minimal repro as a single copy-paste command or test, the captured failing output, and the failure rate. If you cannot reproduce it, say UNREPRODUCED and list exactly what you tried.
```

### Local-First Reproduction Ladder

```text
<bug> occurs in <cloud host / CI / production>. Reproduce it locally before touching the cloud.

Climb this ladder and stop at the first rung that reproduces it:
1. Local credentials and config that mirror the failing host (check variable names only, never print values).
2. The project's own runtime/env (named env, node_modules, test runner).
3. Read-only queries against the same database or store the failing host uses.
4. The same data vendor or external API, called from the local machine.
5. The same code path called directly: pure functions, a --dry-run flag, or a scratch harness that imports read functions only.
6. Cloud logs via CLI, read for the actual failure text.

All local runs are read-only. For each rung, report: what you ran, what it showed, reproduced yes/no. If local passes but the cloud fails, the difference between the two hosts is your first lead. Name it.
```

### Intermittent / Flaky Bug Repro

```text
<bug> is intermittent. Make it reproducible on demand.

Investigate the usual sources of non-determinism:
- Timing: races, timeouts, retries, cron overlap, cache TTL expiry.
- Ordering: test order, async resolution order, unordered collections.
- Environment: time zone, locale, clock, market hours, DST, date boundaries.
- External state: rate limits, vendor outages, eventual consistency, stale caches.
- Resource limits: memory, connection pool, cold starts.

Run the repro N=<20> times under varied conditions and record the failure rate per condition. Find the condition that pushes the failure rate toward 100%. Output: a table of conditions vs. failure rates, the trigger condition, and a repro that fails reliably under it.
```

---

## Phase 3: Evidence & Instrumentation

### Evidence Collection Pass

```text
For reproduced bug <ID>, collect evidence before forming any theory.

Gather and timestamp:
- Logs from every service in the request or job path, at the failure time.
- The exact inputs and outputs at each boundary (API request/response, DB row before/after, queue message).
- Recent changes: deploys, config/flag changes, dependency bumps, and data migrations in the window before first-seen.
- Metrics around first-seen: error rate, latency, and volume.

Output an evidence timeline as a table: time, source, observation, and link/command to re-fetch it. Mark each item as FACT (directly observed) or INFERENCE. Redact secrets and PII.
```

### Targeted Instrumentation

```text
The evidence for <bug> has a gap at <boundary / function / step>. Add temporary instrumentation to close it.

Rules:
- Log only at the boundaries that discriminate between hypotheses; no blanket debug logging.
- Log identifiers, types, lengths, counts, and sources, never secrets or full PII.
- Tag every added line with a removable marker (e.g. `// DEBUG-<ID>`).
- Instrument locally or on a non-production branch first.

Output: the instrumentation diff, the command to run it, the new evidence it produced, and the one-line command that finds every marker for cleanup.
```

### Write-Path Trace

```text
<bug> shows a wrong value at read time: <value> in <screen / endpoint / report>. Trace where that value was written, not just where it is read.

Trace backward from the display:
1. The read path: component → API → query → table/collection/cache key.
2. Every writer of that storage location: ingest jobs, API handlers, migrations, backfills, caches, manual scripts.
3. For each writer: what it wrote, when it last ran, what source it used, and whether that source is recorded on the row.
4. The first point where the value becomes wrong.

Output: the full read and write chain with file:line for each hop, the stored value at each layer (read-only queries), and the first layer where the value diverges from truth. Most "query bugs" are ingest bugs. Prove which one this is.
```

## Chain

**Intake → Known-Issue Check → (Deterministic or Local-First or Flaky) Repro → Evidence Collection → Write-Path Trace** → hand off to **debug-lifecycle-late**.
