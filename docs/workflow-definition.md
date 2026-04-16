# Workflow Definition

This document describes the workflow-as-data format: how workflows are defined in
YAML, what fields are available, how the imperative engine consumes them, and how
to read the canonical `standard` workflow. The JSON Schema is at
`packages/shared/agent_runner_shared/schemas/workflow.schema.json`.

---

## 1. Concept

A workflow definition is a **data document** — a YAML file that declares the stage
sequence, which agents participate, artifact flow, retry policy, and escalation
configuration. The imperative engine (`packages/runner/agent_runner/workflow/engine.py`)
reads this document at startup and drives execution stage by stage. The workflow
definition does not contain any Python code; all executable logic lives in the engine
and in the agent implementations.

The relationship is:

```
workflow YAML  ──(load_workflow)──→  WorkflowDefinition  ──→  engine.py executes
```

Downstream subsystems (harness, docs, tests) load workflows via
`agent_runner.workflow.definition.load_workflow(id)` to inspect the workflow structure
without importing engine internals.

---

## 2. Schema overview

The authoritative schema is `packages/shared/agent_runner_shared/schemas/workflow.schema.json`.
Required top-level fields: `id`, `version`, `stages`, `artifacts`.

```yaml
id: standard            # Unique workflow identifier; matches the filename.
version: 1              # Integer, minimum 1.
description: "..."      # Human-readable summary (optional but recommended).

defaults:               # Defaults applied to all stages unless overridden.
  retry:
    max_attempts: 3
    backoff: immediate  # immediate | linear | exponential
  timeout_seconds: 3600

stages:                 # Ordered list of stages; at least one required.
  - ...                 # See §3.

escalation:             # What to do when a stage's on_failure is "escalate".
  channel: discord      # discord | none
  pause_on:             # Stage IDs where the workflow pauses for human review.
    - task_gen
    - assignment

artifacts:              # Declare all artifact names produced by this workflow.
  story:
    schema: story.schema.json   # JSON Schema filename for validation.
    format: yaml                # yaml | json | md
  qa_report:
    schema: qa_report.schema.json
    format: yaml
```

---

## 3. Stage kinds

### 3.1 `single`

A single-agent stage: one agent runs, produces one or more artifacts.

```yaml
- id: intake
  kind: single
  agent: intake@v1              # name@version from agent-sources/
  artifacts_in: []              # Artifact names this stage reads.
  artifacts_out: [story, constraints, config]   # Artifact names it writes.
  retry:
    max_attempts: 1
  on_failure: halt              # halt | continue | escalate
```

**Fields:**
- `agent` (required for `single`): an agent ref in `name@version` format.
- `artifacts_in`: list of artifact names that must exist before this stage runs.
- `artifacts_out`: list of artifact names this stage is expected to produce.
- `retry.max_attempts`: number of times to retry on failure (default from `defaults`).
- `retry.backoff`: delay strategy between retries.
- `on_failure`: what to do if all retry attempts fail.

### 3.2 `producer_evaluator_loop`

A two-agent loop: the producer generates an artifact; the evaluator scores it. If
the evaluator rejects, the loop retries (up to `retry.max_attempts`).

```yaml
- id: task_gen
  kind: producer_evaluator_loop
  producer: task-generator@v1   # Generates the artifact.
  evaluator: task-plan-evaluator@v1  # Scores/approves the artifact.
  artifacts_in: [story, constraints]
  artifacts_out: [tasks]
  retry:
    max_attempts: 3
  on_evaluator_reject: retry    # retry | escalate | halt
  on_failure: halt
```

**Fields:**
- `producer` (required): agent ref for the producer.
- `evaluator` (required): agent ref for the evaluator.
- `on_evaluator_reject`: what to do when the evaluator rejects (default: `retry`).

The producer and evaluator are always separate agents. The evaluator must not be
the same model as the worker agent to preserve grading independence.

---

## 4. The `standard` workflow — canonical example

The `standard` workflow is the default for all corpus tasks. It lives at
`packages/runner/agent_runner/workflows/standard.yaml`.

```yaml
id: standard
version: 1
description: "Default WI -> PR workflow: intake, task gen+eval, assignment+eval, SWE, QA+eval, lessons."

defaults:
  retry:
    max_attempts: 3
    backoff: immediate
  timeout_seconds: 3600

stages:
  # Stage 1: intake — parse the work item into a structured story + constraints.
  - id: intake
    kind: single
    agent: intake@v1
    artifacts_in: []
    artifacts_out: [story, constraints, config]
    retry: { max_attempts: 1 }
    on_failure: halt

  # Stage 2: task generation — generate sub-tasks, evaluated in a loop.
  - id: task_gen
    kind: producer_evaluator_loop
    producer: task-generator@v1
    evaluator: task-plan-evaluator@v1
    artifacts_in: [story, constraints]
    artifacts_out: [tasks]
    retry: { max_attempts: 3 }
    on_evaluator_reject: retry

  # Stage 3: assignment — assign sub-tasks to agents, evaluated in a loop.
  - id: assignment
    kind: producer_evaluator_loop
    producer: task-assigner@v1
    evaluator: assignment-evaluator@v1
    artifacts_in: [tasks, story, constraints]
    artifacts_out: [assignments]
    retry: { max_attempts: 2 }

  # Stage 4: software engineering — implement the assigned tasks.
  - id: software_engineer
    kind: single
    agent: software-engineer-hyperagent@v1
    artifacts_in: [assignments, tasks, story]
    artifacts_out: [impl_report]
    retry: { max_attempts: 3 }

  # Stage 5: QA — review the implementation and approve or reject.
  - id: qa
    kind: producer_evaluator_loop
    producer: qa@v1
    evaluator: qa-evaluator@v1
    artifacts_in: [story, tasks, assignments, impl_report]
    artifacts_out: [qa_report]
    retry: { max_attempts: 3 }

  # Stage 6: lessons — extract learnings from the completed run.
  - id: lessons
    kind: single
    agent: lessons-optimizer-hyperagent@v1
    artifacts_in: [story, qa_report]
    artifacts_out: [lessons_report]
    retry: { max_attempts: 1 }
    on_failure: continue        # Non-fatal; proceed even if lessons stage fails.

escalation:
  channel: discord
  pause_on: [task_gen, assignment, qa]

artifacts:
  story:          { schema: story.schema.json,          format: yaml }
  constraints:    { schema: constraints.schema.json,    format: md   }
  config:         { schema: config.schema.json,         format: yaml }
  tasks:          { schema: tasks.schema.json,          format: yaml }
  assignments:    { schema: assignments.schema.json,    format: json }
  impl_report:    { schema: impl_report.schema.json,   format: yaml }
  qa_report:      { schema: qa_report.schema.json,     format: yaml }
  lessons_report: { schema: lessons_report.schema.json, format: yaml }
```

---

## 5. Relationship between the YAML definition and the imperative engine

The YAML definition is **read-only data**. It tells the engine *what* to run in *what
order*, but not *how* to invoke individual agents. The engine (`engine.py`) does the
following for each stage:

1. Reads the stage's `kind`, `agent`/`producer`/`evaluator`, and `artifacts_in`.
2. Checks that all `artifacts_in` artifacts are present.
3. Invokes the agent via the runner's agent-invocation mechanism (Claude Code subprocess).
4. For `producer_evaluator_loop`: repeats up to `retry.max_attempts` times, checking
   the evaluator's verdict after each producer attempt.
5. Handles failure according to `on_failure` / `on_evaluator_reject`.
6. Emits structured events (`##EVENT##` lines) at each state transition.

The engine does not validate the YAML at runtime (the loader does). The engine
does not know about the harness or grading; it only knows about stages and artifacts.

---

## 6. Loading a workflow in code

```python
from agent_runner.workflow.definition import load_workflow

wf = load_workflow("standard")      # loads from built-in workflows/standard.yaml
print(wf.id, wf.version)
for stage in wf.stages:
    print(stage.id, stage.kind, stage.agent or f"{stage.producer}+{stage.evaluator}")

# All unique agent refs referenced by the workflow:
print(wf.agent_refs())
# → ['intake@v1', 'task-generator@v1', 'task-plan-evaluator@v1', ...]
```

`load_workflow_file(path)` loads from an arbitrary path. Both functions accept a
`validate=False` flag to skip JSON Schema validation (useful in tests or dev mode).

---

## 7. Adding a new workflow

1. Create `packages/runner/agent_runner/workflows/<id>.yaml`.
2. Validate it conforms to `workflow.schema.json`.
3. Reference it from a `task.yaml` via `workflow: { id: <id>, version: 1 }`.
4. If the workflow introduces new artifact names, add them to the `artifacts` map.
5. Ensure all referenced agent bundles exist in `agent-sources/`.

There is no workflow registry beyond the filesystem; `load_workflow` scans the
`workflows/` directory for a matching filename.
