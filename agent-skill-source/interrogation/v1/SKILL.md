---
name: interrogate-eng
description: Resolve planning-blocking engineering ambiguity during intake without overriding the host agent's scope, artifact contract, or workflow rules.
---

# interrogate-eng: Intake Clarification

Use this skill to remove planning-blocking ambiguity during engineering intake.

This skill is intentionally **supportive**, not supervisory. It sharpens intake quality without taking over orchestration, changing artifact schemas, or inventing requirements.

## Host-Agent Contract

1. The host agent prompt always wins. This skill must complement the host prompt, not override it.
2. Do not take over workflow orchestration, retries, branching, or stage ownership.
3. Do not broaden file access, delegation privileges, or repo exploration beyond what the host prompt allows.
4. Recommended answers are **provisional defaults**, not confirmed facts, until the user or source evidence supports them.
5. When the host agent is the intake agent, the canonical outputs remain:
   - `intake/story.yaml`
   - `intake/config.yaml`
   - `intake/constraints.md`
6. When the host agent is the intake agent, keep `story.yaml.acceptance_criteria` as the downstream-compatible `AC1`, `AC2`, ... mapping. Do **not** replace it with a new list-of-objects schema.
7. Use this skill to improve artifact quality, not to create a parallel handoff format that downstream stages must learn.

## When To Use This Skill

Use `interrogate-eng` only when **all** of the following are true:

1. A requirement is materially ambiguous or missing.
2. The ambiguity affects acceptance criteria, scope boundaries, behavior, contracts, data, security, rollout, or testing.
3. The answer is not already available in the provided context, explicitly referenced docs, or host-allowed repository evidence.
4. Resolving the ambiguity would make downstream planning or QA more reliable.

## When Not To Use This Skill

Do **not** use this skill for:

- procedural questions such as “how should I continue the workflow?”
- permission-seeking questions
- broad discovery prompts such as “what else should I know?”
- implementation details the downstream engineer can safely choose later
- speculative product design beyond the supplied scope
- non-interactive synthetic runs where the right outcome is to document the gap and recommended default

## Operating Rules

1. Ask **exactly one question at a time**.
2. Ask only **decision-forcing** questions.
3. If the answer can be obtained from host-allowed evidence, inspect that evidence before asking the user.
4. If evidence answers part of the question, summarize only the unresolved decision.
5. Keep each clarification compact, concrete, and easy to follow.
6. Treat recommended answers as the safest minimal default, not as permission to invent scope.
7. If the host prompt restricts repo exploration, delegation, or interaction, obey that restriction.
8. If the run is synthetic or otherwise non-interactive, record the open question, recommended default, and impact instead of blocking.
9. Continue only until planning-blocking ambiguity is resolved or explicitly documented as an accepted assumption/risk.

## Ollama / Local-Model Optimization

When the host uses a smaller local model, including an Ollama-backed runner:

- keep summaries to **4 bullets or fewer**
- ask **1 concrete question** only
- provide **1 recommended default**
- keep the “why this matters” explanation to **1–2 short sentences**
- avoid large schemas mid-conversation
- restate only changed facts between turns
- cite only the specific evidence that matters to the current decision

This keeps the clarification flow precise, stable, and low-noise.

## First Response Format

When clarification is needed, respond in this format:

```markdown
What I understand so far:
- ...
- ...

Question 1: <one concrete decision>

Recommended default: <one conservative default>

Why this matters: <brief planning / QA consequence>

Artifact impact: <how this changes ACs, constraints, or open questions>
```

Do not ask “anything else?” unless you have first identified the exact missing decision.

## Question Priority

Ask the highest-impact unresolved decision first, in this order:

1. user-visible behavior or definition of done
2. interface / contract changes
3. data model or migration implications
4. failure-mode or security behavior
5. rollout / compatibility constraints
6. testing and verification expectations
7. scope boundaries, non-goals, or explicit deferrals

## Coverage Checklist

Before declaring intake clarification complete, pressure-test the story against these areas when relevant:

- **Behavior**: actor, trigger, expected outcome, preserved behavior
- **Contracts**: UI, API, CLI, event, job, schema, integration boundaries
- **Data**: storage, defaults, migration order, rollback implications
- **Failure / Security**: validation, errors, permissions, privacy, auditability
- **Rollout**: feature flags, deployment order, compatibility, monitoring
- **Testing**: automated proof, manual QA, regression expectations
- **Scope**: non-goals, unsupported cases, intentionally deferred work

Skip an area only when it is clearly not applicable, and record that judgment in the host agent’s artifact language.

## Evidence Rule

If the host agent is allowed to inspect the repo or retrieve docs, do that **before** asking the user when the answer may already exist.

When evidence is relevant, summarize it briefly:

```markdown
Relevant evidence:
- `path/to/file.ext`: <pattern or fact>
- `path/to/other.ext`: <constraint or precedent>

Question 1: ...
Recommended default: ...
Why this matters: ...
Artifact impact: ...
```

Do **not** turn this skill into a broad repo-audit workflow.

## Intake-Agent Mapping Rules

When the host agent is the intake agent:

### `story.yaml`

- keep the existing canonical schema
- preserve `change_id`, `title`, `description`, `raw_input`, and existing provenance fields
- keep `acceptance_criteria` as `AC1`, `AC2`, ...
- fold clarified decisions into the existing fields instead of inventing a new top-level structure
- use `metacognitive_context` only for meaningful rationale, open gaps, or clarification notes

### `constraints.md`

Use clarification output to strengthen:

- confirmed scope
- explicit non-goals
- engineering constraints
- testing expectations
- open questions with:
  - blocking / non-blocking status
  - recommended default
  - downstream impact

### `config.yaml`

Do not use this skill to redefine config ownership. Only the host agent should update runner-facing metadata already defined by the intake contract.

## Suggested Scratchpad Shape

If the host agent needs a temporary working structure while clarifying, use a lightweight scratchpad like this and then translate it back into the host artifact contract:

```markdown
## Confirmed decisions
- ...

## Recommended defaults awaiting confirmation
- ...

## Refined AC notes
- AC1: ...
- AC2: ...

## Open questions
- OQ-001 | blocking: yes | recommended default: ... | impact: ...
```

This scratchpad is a thinking aid only. It is **not** the final downstream contract unless the host prompt explicitly asks for it.

## Completion Gate

Clarification is complete when:

- each must-have behavior is testable
- no planning-blocking ambiguity remains hidden
- any unresolved question is explicitly recorded with a recommended default and impact
- scope boundaries and non-goals are explicit
- the downstream planner can proceed without inventing product decisions

## Final Rule

If blocking questions remain, do not pretend intake is fully clarified.

Instead, make the assumptions explicit, label their impact, and let the host agent record them in the canonical artifacts.

