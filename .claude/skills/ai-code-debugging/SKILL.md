---
name: ai-code-debugging
description: Audit AI-written code for its characteristic failure modes (hallucinated APIs, swallowed exceptions, masking fallbacks, race conditions, weak tests) and debug evidence-first without touching production code until root cause is known. Use when reviewing a PR another model wrote, when a bug is suspected in recently AI-generated code, or before trusting a "looks done" claim.
---

# Debugging AI-Written Code

House rule: reproduce and collect evidence before changing production code; identify root cause before proposing a fix.

## Evidence-First Debugging

```text
Debug <issue> without changing production code first.

Reproduce deterministically: exact steps, inputs, environment, commit, and feature-flag state. Collect logs, traces, metrics, network requests, database state, and screenshots/video if UI-related. Reduce the problem to a minimal repro or failing test. Identify the root cause before proposing a fix.

Output: evidence timeline, minimal repro, root cause, confidence level, and proposed fix with regression test.
```

## AI Failure-Mode Audit

```text
Audit AI-written code in <paths> for common failure modes:
- Hallucinated or outdated library APIs.
- Swallowed exceptions or silent catch blocks.
- Fallbacks that mask errors.
- Async/await, race condition, or cleanup bugs.
- Weak tests or assertions that do not prove behavior.
- Unnecessary dependencies or permission overreach.
- Incorrect state mutation or layer violations.

For each finding: severity, evidence, fix, and regression test. Do not approve until high-severity issues are fixed.
```

## Regression and Root-Cause Report

```text
After fixing <bug>, add a regression test that fails before the fix and passes after it.

Verify the fix with the feature flag ON and OFF and with the original reproduction steps. Produce a root-cause report covering:
- What failed and why.
- The fix and why it is minimal.
- Test evidence and command output.
- Monitoring, alerts, or runbook updates.
- Remaining risk and rollback plan.
```

## Chaining

For a full bug lifecycle instead of a quick audit, hand off to the **debug-lifecycle-early** and **debug-lifecycle-late** skills (the 8-phase cycle this is a fast-path through). For cross-model PR review specifically, see the Cross-Model Verification Handoff prompt in the **ops-security-data-delegation-docs** skill.
