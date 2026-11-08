---
name: planning-handoff-review
description: Turn a decision into a milestone plan with checkpoints, split work between human and AI (with model tiers), write a standalone session brief for a fresh agent, then review each checkpoint honestly, decide whether to pivot or persevere, and run a retrospective into a reusable playbook. Covers phases 3-4 of human-AI planning (Plan&Handoff, Review&Adaptation). Use after a decision is recorded (see planning-framing-options), and after every milestone finishes.
---

# Human–AI Planning — Plan & Handoff, Review & Adaptation (Phases 3–4)

Picks up from a decision record (see **planning-framing-options**). The human decides, the AI recommends. No plan rests on an unchecked high-impact assumption. No irreversible step happens without a human checkpoint. Every claim of "done" says where the evidence came from.

---

## Phase 3: Plan & Handoff

### Milestone Plan with Checkpoints

```text
Turn decision <record> into a plan of 3–6 milestones.

For each milestone:
- Outcome: what exists at the end, observable by me.
- Verification: the command, page, or check that proves it.
- Size: hours, not story points, with a confidence level.
- Depends on: which earlier milestone or external input.
- Reversibility: easy to undo, costly to undo, or irreversible.

Order the plan so the riskiest assumption is tested first. Insert a human checkpoint before every irreversible milestone, and after any milestone whose estimate slipped by more than 50%. End with the first milestone only, in enough detail to start it today.
```

### Human/AI Responsibility Split

```text
For the plan <paste plan>, assign every task to one of four owners.

- HUMAN ONLY: decisions, approvals, credentials, dashboard clicks, anything irreversible or outward-facing.
- AI, HUMAN CONFIRMS: the AI does it, and I approve the result before it takes effect.
- AI, AUTONOMOUS: reversible, read-only, or local work with a clear check.
- BLOCKED: waiting on an input nobody has yet.

For each HUMAN ONLY item, write the exact copy-paste command or the direct URL, so I never have to work out the step myself. Name the model tier for each AI task: judgment, workhorse, or cheap. Flag every task where the owner is ambiguous and ask me to resolve it.
```

### Session Brief for a Fresh Agent

```text
Write a session brief so a new AI session can start the next milestone with no memory of this conversation.

Include:
- Goal, success criteria, and non-goals in under 120 words.
- Decisions already made (link the records) and options already rejected, so they are not re-proposed.
- Current state: what is done, what is in flight, and the exact next step.
- Constraints and safety rules that apply, such as files not to touch, secrets policy, and the branch or worktree to use.
- Open questions and the assumptions still unverified.
- The definition of done for this milestone, and when to stop and ask me.

The brief must stand alone. Do not rely on "as discussed." Never include secret values; name the variable.
```

---

## Phase 4: Review & Adaptation

### Checkpoint Review

```text
We finished milestone <N>. Review it against the plan before starting the next.

Report:
- Planned outcome vs. actual outcome, with evidence for each (command output, link, file:line).
- Estimate vs. actual time, and the main cause of any gap.
- Which assumptions were confirmed, broken, or are still unverified. Update the register.
- Anything that changed scope, even slightly, and whether I approved it.
- Surprises: things we learned that the plan did not predict.

End with a verdict: CONTINUE as planned, ADJUST (say what), or STOP and re-decide. Mark each claim as verified locally, verified in production, or unverified. "Done" without a location is not done.
```

### Pivot or Persevere

```text
The plan for <goal> is not going as expected: <what is going wrong>. Help me decide whether to pivot.

Do not defend the current plan out of momentum. Cover:
- What we have spent so far, and what is genuinely reusable if we pivot. Sunk cost does not count as a reason to continue.
- Which decision-record assumption or revisit trigger has been hit.
- Three paths: continue unchanged, adjust scope or approach, or stop and take a different option from the matrix.
- For each path: time to the success criteria, risk, and what I give up.

Recommend one path and state the evidence that would prove the recommendation wrong. If the success criteria themselves are now wrong, say so and propose the revised criteria for my approval.
```

### Retrospective and Reusable Playbook

```text
The goal <name> is finished, or stopped. Run a retrospective on the planning itself, not just the work.

Cover:
- Where the plan was right, and where it was wrong, with the cause (a bad assumption, a missed constraint, a skipped check, or a scope change).
- Which human/AI handoffs worked, and which cost rework or confusion.
- Which decisions I would make differently now, and what I knew at the time.
- Which prompts from this set earned their keep, and which did not.
- Caveats: anything shipped with an unverified edge or a parked item.

Output 3 concrete changes to how we plan next time, each phrased as a rule that can be checked. Propose the reusable parts as a playbook or slash command, but do not create it until I approve. Write the retrospective to `docs/` with a date suffix.
```

## Chain

**Milestone Plan → Responsibility Split → Session Brief** (handoff) → **Checkpoint Review** after every milestone (**Pivot or Persevere** if it's going wrong) → **Retrospective** at the end.
