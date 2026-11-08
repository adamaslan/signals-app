---
name: feature-gating-safety
description: Gate risky changes behind feature flags with kill switches, staged rollouts, and safety audits — flag-first implementation, a safety-controls risk review, and canary/rollback plans. Use before exposing a new or risky feature to users, when reviewing a PR for production risk, or when planning a staged rollout.
---

# Feature Gating & Safety Controls

House rule: do not expose a feature to users until evidence and safety gates pass. High-risk areas (auth, payments, data deletion, migrations, secrets, crypto, webhooks) need explicit human approval gates, not an agent's sign-off.

## Flag-First Implementation

```text
Implement <feature> behind feature flag <flag_name>, default OFF.

Include:
- Kill switch and rollback procedure.
- Flag owner and expiry date.
- Staged rollout plan: dark launch → 1% → 5% → 25% → 100%.
- Metrics and alerts for error rate, latency, and business impact.
- Tests for flag ON, flag OFF, and kill switch.
- Logging of flag evaluation in relevant events/errors.

Do not expose the feature to users until the evidence and safety gates pass.
```

## Safety Controls Audit

```text
Review <PR/change> for safety and production risk.

Check: authentication, authorization, payments, data deletion, migrations, secrets handling, dependency changes, least privilege, PII in logs, sandboxing, and policy-as-code/CI checks.

Require human approval gates for high-risk areas. Output a risk table with: risk, evidence, required control, blocker status, owner, and rollback plan. Do not approve if high-risk controls are missing.
```

## Canary and Rollback Plan

```text
Create a staged rollout plan for <feature> using flag <flag_name>.

Define:
- Cohorts and rollout percentages.
- Success metrics and guardrail metrics.
- Error/latency thresholds that trigger rollback.
- Alert routing and escalation.
- Manual kill-switch procedure.
- Verification checklist for each stage.
- Monitoring queries and owners.

Output a go/no-go checklist for each stage and a rollback runbook.
```

## Chaining

Flag-First Implementation → evidence-gated-delivery skill's Evidence-Gated PR → PR Gatekeeper Review. Use Safety Controls Audit on any PR touching a sensitive surface before merge.
