---
name: planning-framing-options
description: Frame a goal with the human before planning — a 7-question goal interview, a goal brief with checkable success criteria and non-goals, an assumption/unknowns register — then generate genuinely different options and build a decision matrix leading to a recorded decision. Covers phases 1-2 of human-AI planning (Framing, Options&Decisions). Use at the start of any nontrivial project or feature before committing to an approach.
---

# Human–AI Planning — Framing, Options & Decisions (Phases 1–2)

The human owns goals, priorities, risk tolerance, and every irreversible decision. The AI owns breadth: surfacing options, finding gaps, keeping the record straight. Carry forward: the goal brief, the assumption register, and the decision record. Hand off to **planning-handoff-review** once a decision is made.

---

## Phase 1: Framing

### Goal Interview

```text
I want to plan <rough goal>. Before proposing anything, interview me.

Ask at most 7 questions, one message, ordered by how much each answer would change the plan. Cover:
- The outcome: what is true when this is done that is not true now?
- Why now: what happens if we do nothing for a month?
- Constraints: time, budget, tools, people, things that must not change.
- Who else is affected or must approve.
- What I already tried or decided.

Do not suggest solutions yet. For each question, say what you will assume if I skip it. After I answer, restate the goal in 3 sentences and wait for me to confirm or correct it.
```

### Success Criteria and Non-Goals

```text
Using the confirmed goal <paste goal restatement>, write the goal brief.

Include:
- Success criteria: 3–5 observable, checkable statements. Each names how it will be measured and by whom. "Better" and "cleaner" are not criteria.
- Non-goals: things that sound adjacent but are explicitly out of scope, each with a one-line reason.
- Hard constraints vs. soft preferences, listed separately.
- Definition of done, and the point at which we stop even if it is not perfect.

Flag any criterion you cannot verify with the tools and access available. Ask me to approve the brief before any planning starts.
```

### Assumption and Unknowns Register

```text
For the goal brief <paste or link>, list every assumption the plan will rest on.

For each assumption:
- The statement, in one sentence.
- Source: FACT (I checked it, and how), STATED (the human said it), or GUESS (you inferred it).
- Impact if wrong: low, medium, or high.
- The cheapest check that would settle it, and who runs it.

Sort by impact, then by cheapness. Do the checks you can run read-only now, and update the sources. Leave the rest as questions for me. Do not build any plan on a high-impact GUESS.
```

---

## Phase 2: Options & Decisions

### Option Generator

```text
Generate approaches for <goal brief>. Do not pick one yet.

Give 3–4 genuinely different approaches, including:
- The smallest thing that could work.
- The conventional approach most people would take.
- One that reframes the problem (for example, buy instead of build, delete instead of add, or change the process instead of the code).

For each: how it works in 3 sentences, effort, what it makes easy, what it makes hard later, and the assumption it depends on most. Do not pad with near-duplicates. If only two real options exist, say so.
```

### Decision Matrix with a Recommendation

```text
Compare the options from <Option Generator output> against my success criteria and constraints.

Build a table: options as rows, criteria as columns. Score each cell with a short reason, not just a number. Then:
- State which criteria are decisive and which barely differentiate the options.
- Recommend one option and give the single strongest reason.
- Say what would change your recommendation.
- Mark any score that rests on a GUESS from the assumption register.

Give a recommendation, not a survey. I make the final call.
```

### Decision Record

```text
We decided on <option>. Write the decision record now, while the reasoning is fresh.

Include:
- Decision: one sentence.
- Context: the goal and the constraints that mattered.
- Options considered and why each was rejected.
- Trade-offs we are knowingly accepting.
- Revisit trigger: the specific observation that means we should reconsider.
- Who decided, and the date.

Save it as `docs/decisions/<topic>-YYYY-MM-DD.md`. Never record a decision I did not make. If I only agreed with a recommendation without discussion, mark it "accepted as recommended."
```

## Chain

**Goal Interview → Success Criteria and Non-Goals → Assumption Register → Option Generator → Decision Matrix → Decision Record** → hand off to **planning-handoff-review** for the milestone plan.

**Short loop for a small project:** Goal Interview → Decision Matrix → (planning-handoff-review's Milestone Plan) → (planning-handoff-review's Checkpoint Review).
