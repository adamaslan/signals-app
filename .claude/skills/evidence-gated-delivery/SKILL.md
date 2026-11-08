---
name: evidence-gated-delivery
description: Ship a change only with proof — PR evidence packs, test-first bug fixes, UI runtime proof, end-to-end safe delivery, strict PR gatekeeping, and incident-to-prevention loops. Use when asked to implement a feature/fix "safely", to prove a change works, to review a PR as a strict gatekeeper, or to run an incident through to prevention. Every claim gets PASS/FAIL/UNVERIFIED and a location (local/CI/production).
---

# Evidence-Gated Delivery

Core rule across every prompt here: **do not say "done" without evidence**, and name where the evidence came from (local, CI, production). Redact secrets and PII in every output.

## Evidence-Gated PR

```text
You are a coding agent working in <repo>. Implement <change>.

Do not claim completion until you can prove it. Produce a PR evidence pack containing:
- Changed files and summary of behavior.
- Targeted unit/integration tests added or updated, with exact commands and output.
- Runtime logs showing the feature executing, saving, or returning expected results.
- UI screenshot/video for any user-facing change, including success and error states.
- CI artifact links, not just pasted claims.
- Feature-flag state tested: ON, OFF, and kill switch.
- Residual risks and known limitations.

Mark every claim as PASS, FAIL, or UNVERIFIED. If evidence is missing, do not say "done." Redact secrets and PII.
```

## Test-First Proof Builder

```text
For <bug or feature>, write the smallest failing test that reproduces the issue before changing production code.

Steps:
1. Add a targeted test and run it; capture the failure output.
2. Implement the smallest fix.
3. Re-run the targeted test and impacted regression tests; capture passing output.
4. Add edge-case tests for the acceptance criteria.
5. Do not weaken assertions or delete tests to make them pass.

Output: failing log, passing log, test paths, acceptance-criteria coverage, and any remaining gaps.
```

## UI Runtime Proof

```text
For UI change <feature>, prove it works at runtime using browser automation.

Verify:
- Page loads and renders the feature.
- User interaction works.
- Save action persists after reload.
- Error state is handled correctly.
- Feature flag ON and OFF paths both behave as expected.

Capture before/after screenshots or a short video, console logs, network logs, and deterministic selectors/test IDs. Attach artifacts and summarize each assertion as PASS, FAIL, or UNVERIFIED. Redact PII.
```

## End-to-End Safe Delivery

```text
Deliver <change> end-to-end safely.

Requirements:
- Implement behind a default-OFF feature flag.
- Add targeted and regression tests.
- Capture runtime/UI proof with logs, screenshots/video, and network evidence.
- Ensure CI independently re-runs checks and verifies artifacts.
- Provide dark-launch/canary plan, monitoring, and rollback/kill switch.
- Produce a PR evidence pack and a go/no-go checklist.

Do not mark complete if any evidence is missing or unverifiable.
```

## PR Gatekeeper Review

```text
Act as a strict PR reviewer for an AI-generated change.

Verify:
- Tests fail before the change and pass after it.
- CI artifacts match the agent's claims.
- UI proof is deterministic, not staged or ambiguous.
- Feature flag ON/OFF and kill switch are tested.
- Rollback is documented and practical.
- No secrets or PII are exposed.
- High-risk changes have human approval.

Output PASS/FAIL per criterion. Reject if evidence is missing, weakened, or unverifiable.
```

## Incident-to-Prevention Loop

```text
For incident <ID>, run an evidence-based debugging and prevention loop.

Correlate logs, traces, metrics, and feature-flag state. Reproduce the issue, identify root cause, implement a fix with a regression test, verify rollback/kill switch, and update alerts, runbooks, and CI gates.

Output: incident timeline, evidence, fix proof, prevention actions, owners, and deadlines.
```

## Chaining

- Delivery: **Flag-first implementation (feature-gating-safety skill) → Evidence-Gated PR → PR Gatekeeper Review**.
- Incident: **ai-code-debugging skill's evidence-first debugging → Incident-to-Prevention Loop**.
