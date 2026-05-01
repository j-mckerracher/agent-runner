---
name: execution-discipline
description: |
  Mandatory execution discipline protocol for AI agent workflows. Use this skill whenever an agent needs to plan work, track progress, verify completion, or handle execution drift. Provides the standard operating procedures for: (1) Plan Mode Default — creating explicit plans/checklists before non-trivial tasks (2+ steps or architectural decisions), (2) Replan on Drift — stopping and reassessing when execution diverges from plan, (3) Verification Before Done — never marking work complete without evidence (tests, logs, diffs, rubric checks), (4) Track Progress — marking checklist items complete as work progresses, (5) Demand Elegance — preferring the simplest robust design and avoiding over-engineering. Keywords: plan mode, replan, drift, verification, evidence, checklist, track progress, elegance, non-trivial task, planning discipline.
---

## Dynamic context

No default `!` pre-execution injection is recommended for this skill. It captures process discipline rather than environment state, so the default guidance should remain static.

# Execution Discipline Protocol

Standard operating procedures for planning, tracking, verifying, and completing work in agent workflows.

## When to Use This Skill

Activate this skill when:

- Starting any non-trivial task (2+ steps or architectural decisions)
- Tracking work items through completion
- Verifying that work meets quality standards before marking done
- Execution has diverged from the original plan
- Evaluating whether a design is appropriately simple vs. over-engineered

## Core Principles

1. **Simplicity First**: Make changes/outputs as simple as possible; touch only what is necessary.
2. **No Laziness**: Fix root causes; avoid temporary patches.
3. **Minimal Impact**: Include only what is required to meet the objective.

## Protocol 1: Plan Mode Default

For any non-trivial task (2+ steps or architectural decisions):

1. **Create an explicit plan or checklist** before starting execution.
2. Apply the same planning discipline to **verification and remediation work** — not just primary tasks.
3. Track checkable work items before beginning implementation.
4. Confirm the plan is still valid and prerequisites are satisfied before executing.

### What Qualifies as Non-Trivial

- 2 or more discrete steps
- Architectural decisions involved
- Multiple files or components affected
- Coordination with other agents or stages
- Any task where the path to completion is not immediately obvious

## Protocol 2: Replan on Drift

If execution diverges from the plan:

1. **STOP** — do not continue down the divergent path.
2. **Reassess** — evaluate what changed and why.
3. **Re-plan** — create a revised plan that accounts for the new information.
4. **Resume** — continue execution under the revised plan.

### Drift Indicators

- Unexpected errors or failures
- Discovered complexity significantly exceeds estimates
- Prerequisites that were assumed to be met are not
- New information invalidates prior assumptions
- Scope is expanding beyond the original objective

## Protocol 3: Verification Before Done

**Never mark work complete without evidence.** Valid evidence types:

| Evidence Type             | When to Use              |
| ------------------------- | ------------------------ |
| Test results              | Code changes, bug fixes  |
| Build output              | Compilation, packaging   |
| Log excerpts              | Runtime verification     |
| Diff review               | Code changes             |
| Rubric checks             | Evaluator assessments    |
| Screenshots               | UI changes               |
| Manual verification steps | Functionality validation |

### Verification Checklist

Before marking any task complete:

- [ ] All stated objectives/DoD items are addressed
- [ ] Evidence exists for each completed item
- [ ] No known regressions introduced
- [ ] Output artifacts are valid and complete

## Protocol 4: Track Progress

1. Before starting work: identify all checkable work items.
2. As work progresses: mark items complete when verified.
3. Provide high-level summaries at each major step.
4. Record decisions and reasoning in output artifacts.

## Protocol 5: Demand Elegance (Balanced)

For non-trivial changes or decisions:

- **Challenge hacky fixes** — prefer the simplest robust design.
- **Avoid over-engineering** trivial work.
- **Follow existing patterns** and conventions unless there's a compelling reason to deviate.
- **Balance thoroughness with pragmatism** — the goal is the simplest solution that is also robust.

### When to Apply Full Rigor vs. Keep It Simple

| Situation                                    | Approach                                             |
| -------------------------------------------- | ---------------------------------------------------- |
| Trivial change (typo, config update)         | Minimal process — just verify                        |
| Standard implementation (following patterns) | Plan briefly, verify, done                           |
| Complex/architectural decision               | Full plan, explicit checklist, thorough verification |
| Bug fix with unclear root cause              | Investigate first, plan fix, verify no regressions   |
