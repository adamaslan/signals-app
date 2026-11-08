# Optimized Prompt Suite — Testing Index

Skills in `.claude/skills/` for signals-app. Each was distilled from the opt-prompts sets (1–4). This file is the index for the suite; add a row when a skill is added.

## Skills

| Skill | Use when |
|---|---|
| evidence-gated-delivery | Shipping a change with proof: evidence packs, test-first fixes, UI runtime proof, PR gatekeeping |
| feature-gating-safety | Flag-first rollouts, safety audits, canary/rollback plans |
| ai-code-debugging | Reviewing AI-written code for failure modes; evidence-first debugging |
| debug-lifecycle-early | Start of a bug: triage, reproduction, evidence, write-path trace |
| debug-lifecycle-late | Bug after repro: hypotheses, root cause, fix, verification, postmortem |
| planning-framing-options | Framing a goal, assumptions register, options, decision record |
| planning-handoff-review | Milestone plans, session briefs for fresh agents, checkpoint reviews |
| codebase-build-refactor | Mapping an unfamiliar repo, impact analysis, specs, safe refactors |
| testing-perf-cost | Test gap/mutation checks, measure-before-optimize, cost audits |
| ops-security-data-delegation-docs | Data/security/ops reviews, incidents, delegation, docs and status updates |

## Suite checks

Run these when the suite changes:

```bash
for d in .claude/skills/*/; do n=$(basename "$d"); grep -q "^name: $n$" "$d/SKILL.md" && echo "ok $n" || echo "BAD $n"; done
```

Expect every line to start with `ok`. A `BAD` line means the frontmatter `name` does not match its folder, so the skill will not load under that name.

## Growing the suite

1. Add `.claude/skills/<name>/SKILL.md` with frontmatter `name` equal to the folder name.
2. Add a row to the table above.
3. Run the suite check.
