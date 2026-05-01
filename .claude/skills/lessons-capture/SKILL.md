---
name: lessons-capture
description: |
  Agent-scoped lessons protocol for AI agent workflows. Use this skill to (1) retrieve only lessons relevant to the current agent/stage/task-context via the Reference Librarian and (2) capture new lessons after user corrections. Provides bounded-context delivery, required lesson metadata, append-only storage rules for agent-context/lessons.md, and fallback artifact emission when direct append is unavailable. Keywords: lessons capture, scoped lessons, relevance filtering, context budget, mistake pattern, prevention rule, trigger check, lessons.md, continuous improvement.
---

## Dynamic context

No default `!` pre-execution injection is recommended for this skill. It describes a capture workflow, so baseline guidance should remain stable across runs.

# Agent-Scoped Lessons Protocol (Retrieval + Capture)

Standard protocol for delivering only relevant lessons to each agent while preserving append-only lesson capture.

## When to Use This Skill

Activate this skill when:

- An agent is starting work and needs applicable prevention rules
- An agent receives a user correction during workflow execution
- A mistake pattern is identified that should be prevented in the future
- An agent needs to emit or append a lesson entry for optimization

## Core Rules

1. **Scoped delivery only**: Non-librarian agents MUST NOT read `agent-context/lessons.md` directly to discover lessons.
2. **Retrieve before apply**: Before non-trivial work, obtain scoped `applicable_lessons` (agent + stage + task context filtered) via the Reference Librarian workflow or invocation payload sourced by the librarian.
3. **Bound context budget**: Return a small bounded set (default 3, hard max 5) plus an omission count.
4. **Capture immediately**: After ANY user correction, capture a lesson entry immediately (append or fallback artifact emission).

## Scoped Retrieval Workflow

### Request Contract (agent/orchestrator -> librarian)

```yaml
lesson_request:
  requesting_agent: '<agent name>'
  workflow_stage: '<intake|planning|assignment|execution|qa|evaluation|orchestration|exploration|librarian>'
  task_context:
    summary: '<short task/UoW summary>'
    keywords: ['<keyword1>', '<keyword2>']
    files_in_scope: ['<optional paths>']
  max_lessons: 3
```

### Librarian Selection Rules (deterministic-first)

1. Filter by `target_agents` (requesting agent match or global lesson).
2. Filter by `stage_tags` (workflow stage match).
3. Match `trigger_context` against task summary/keywords/files.
4. Rank by relevance and severity, then return top `max_lessons` (bounded by hard max 5).

### Response Contract (librarian -> requesting agent)

```yaml
applicable_lessons:
  - lesson_id: '<stable lesson id>'
    prevention_rule: '<concrete rule to apply>'
    trigger_check: '<explicit guard/check>'
    why_applicable: '<agent/stage/context match rationale>'
    confidence: 'high|medium'
omitted_due_to_budget: 0
no_match_reason: null
```

If no lessons match:

```yaml
applicable_lessons: []
omitted_due_to_budget: 0
no_match_reason: '<why no relevant lesson matched>'
```

## Lesson Entry Format (for Capture)

Every new lesson should include routing metadata plus prevention content:

```yaml
- lesson_id: '<optional stable id>'
  target_agents: ['<agent-name>', '<or global>']
  stage_tags: ['<planning|execution|qa|...>']
  trigger_context: ['<keyword-or-condition>']
  mistake_pattern: '<what went wrong — be specific>'
  prevention_rule: '<concrete rule to prevent recurrence>'
  trigger_check: '<explicit guard or check to detect the pattern early>'
  agent: '<capturing agent>'
  timestamp: '<ISO 8601>'
```

Legacy entries without these fields remain valid; librarian/optimizer must infer missing metadata conservatively.

## Capture Storage Protocol

### Primary: Direct Append

When the agent has write permission to `agent-context/lessons.md`:

1. **Append** the lesson entry to the end of the file
2. **Do NOT modify** existing entries
3. **Do NOT reorganize** the file
4. **Include timestamp**, agent identifier, and routing metadata where possible

### Fallback: Emit in Output Artifacts

When direct append is not permitted (e.g., read-only access):

1. **Emit** the lesson entry as an append-ready block in your standard output artifact
2. **Label it clearly** so downstream agents or the orchestrator can route it to `lessons.md`
3. **Format identically** to the direct-append format

```yaml
# In your output artifact:
lessons_to_capture:
  - mistake_pattern: '...'
    prevention_rule: '...'
    trigger_check: '...'
    target_agents: ['...']
    stage_tags: ['...']
    trigger_context: ['...']
    agent: '<your agent name>'
    timestamp: '<ISO 8601>'
```

## Canonical Source and Derived Index

- **Canonical append-only source**: `agent-context/lessons.md`
- **Derived retrieval index**: `agent-context/knowledge/lessons-index.json`

Ownership model:

- Most agents: append/fallback capture only, consume scoped lessons only
- Reference Librarian: routes scoped lessons and maintains retrieval metadata/index
- Lessons Optimizer: reads full lessons for optimization and recommendation quality

## When NOT to Capture

- Routine decisions that don't involve a correction
- Information that belongs in knowledge files (route through librarian instead)
- Feedback that is about scope/requirements rather than agent behavior
