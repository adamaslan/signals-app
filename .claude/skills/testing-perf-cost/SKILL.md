---
name: testing-perf-cost
description: Assess test coverage by risk and quality (mutation-style breaks, realistic fixtures/goldens, critical-journey E2E), and address performance and cost (measure before optimizing, LLM/token cost audits, cloud free-tier exposure, shared rate-limit budgets). Use when asked about test coverage, flaky/weak tests, something feels slow or expensive, or before a cost-sensitive launch.
---

# Testing Strategy & Performance/Cost (Areas C–D)

House rule: measure before optimizing; a change without a measured gain is reverted.

---

## Area C: Testing Strategy

### Test Gap Analysis

```text
Assess the test coverage of <module or feature> by risk, not by percentage.

List the behaviors that would hurt most if they broke (money, auth, data loss, wrong numbers). For each, say which test covers it, or NONE. Check that existing tests actually assert behavior: flag tests with no assertions, mocks that replace the thing under test, and snapshots nobody reads.

Output a ranked gap list: behavior, risk, current coverage, and the cheapest test that closes the gap. Write the top 3 tests. Do not weaken or delete existing tests.
```

### Test Quality Check (Mutation Style)

```text
Check whether the tests for <module> would catch a real bug.

Make 5–10 small deliberate breaks, one at a time, on a scratch branch: flip a comparison, off-by-one a boundary, drop a null check, return early, swap two arguments. After each, run the tests.

Report which breaks were caught and which survived. For each survivor, write the test that would kill it. Revert every break, confirm the tree is clean with `git status`, and list the surviving mutants as the real measure of coverage.
```

### Fixtures and Golden Data

```text
Build realistic test data for <feature>.

Produce:
- A small fixture set drawn from real shapes (sanitized): typical, boundary, empty, malformed, and very large cases.
- Golden outputs for the pure functions, stored beside the tests.
- Deterministic clocks, seeds, and IDs, so runs do not depend on today's date or on network access.
- A note on how to regenerate the goldens, and a rule that a golden changes only with a reviewed diff.

Remove all real PII, keys, and account identifiers. Record where each fixture came from.
```

### Critical Journey E2E Plan

```text
Pick the 3–5 user journeys that must never break in <app>: for example sign-up, pay, core action, data export.

For each: the steps, the assertions at each step (including the final persisted state), the data setup and teardown, and the environment it runs in. Say which steps need real third parties and which can use a stub, and what the stub could hide.

Write the tests for the first journey. Run them three times and report flakiness. Keep each journey under <N> minutes. Mark any step you can only check manually, with the manual check written as a copy-paste command or a numbered list.
```

---

## Area D: Performance & Cost

### Measure Before Optimizing

```text
<operation> feels slow. Do not optimize yet.

1. Define slow: the metric, the current number, the target, and how it is measured.
2. Measure it three times under realistic load and record the spread.
3. Profile it and find where the time goes (CPU, I/O, network, queries, rendering). Show the top 3 costs with numbers.
4. Check the cheap suspects: missing index, N+1 queries, repeated work, an unbounded payload, no caching, cold starts.

Output the profile, the ranked causes, and the expected gain of each fix. Fix only the top one. Re-measure and report before and after. A change without a measured gain is reverted.
```

### LLM and Token Cost Audit

```text
Audit the AI usage in <repo / workflow> for cost.

For each call site or workflow: the model, average input and output tokens, calls per day or run, and an estimated monthly cost (show the arithmetic and mark prices as ASSUMPTION unless you verified them). Then find the waste: oversized context, repeated identical calls, a premium model on a mechanical task, retries without backoff, no caching.

Output a ranked savings list with the quality risk of each change. Propose routing: which tasks go to a judgment tier, which to a workhorse tier, and which to a cheap tier. Change nothing until I approve.
```

### Cloud and Free-Tier Cost Check

```text
Review the cost exposure of <project / cloud account> using read-only CLI queries.

List each billable service in use, its current usage against any free-tier or quota limit, and what would cause a surprise bill (unbounded loops, public endpoints without limits, logs that grow forever, scheduled jobs that overlap, forgotten dev resources).

Output: a table of service, usage, limit, headroom, and risk. Recommend budget alerts and hard caps, with the exact commands to set them. Do not change any resource, and do not print credentials.
```

### Rate-Limit and Quota Budget

```text
Several callers share one API key or quota: <list callers and vendor>.

Work out the budget: the vendor's published limits, each caller's request rate at normal and peak, and the sum against the limit. Find who can starve whom.

Design the controls: a shared counter or token bucket, batching, backoff with jitter, a priority order, and what each caller does when the budget is spent (wait, degrade, or fail loudly, never silently switch vendor). Output the budget table and the smallest implementation. Test it with a burst that exceeds the limit.
```

## Chains

- **Slow or expensive:** Measure Before Optimizing (code) or LLM/Cloud cost audit (spend), with Rate-Limit Budget when a shared quota is involved.
- **Clean up safely (with codebase-build-refactor):** Behavior-Preserving Refactor → Dead Code Cleanup, always with Test Quality Check to confirm the tests can fail.
