# agent-runner

A workflow runner that executes multi-stage AI agent pipelines against either:
- **Local synthetic story fixtures** — for offline testing without Azure DevOps
- **Live Azure DevOps work items** — for integration with ADO projects

The workflow executes 6 stages: intake → planning (task-gen) → assignment → implementation → QA → lessons-optimization.

> **Testing & evaluation:** see [`eval/README.md`](eval/README.md)

---

## Quick Start

### Run with the bundled TEST-AC-001 synthetic story (default)

```bash
python run.py --repo /absolute/path/to/target/repo
```

This uses `agent-context/test-fixtures/synthetic_story.json` by default—no additional arguments needed.

### Run with a custom synthetic story fixture

```bash
python run.py --repo /absolute/path/to/target/repo --story-file /path/to/custom_story.json
```

### Run against Azure DevOps

```bash
python run.py --repo /absolute/path/to/target/repo --ado-url 'https://dev.azure.com/<org>/<project>/_workitems/edit/123456'
```

### Choose a runner

```bash
python run.py --repo /path/to/repo --runner gemini   # gemini (default model: gemini-2.5-flash)
python run.py --repo /path/to/repo --runner claude   # default
python run.py --repo /path/to/repo --runner copilot
```

Run `python run.py --help` for all options.

---

## Build a Custom Workflow

The default `run.py` flow is now built on reusable primitives in `workflow_api.py`.

Use that module when you want to keep Prefect and Opik instrumentation but swap in your own agents, prompts, and evaluation loops:

```python
from prefect import flow

from workflow_api import (
    WorkflowContext,
    make_agent_step,
    make_sdk_evaluator_step,
    render_prompt_template,
    run_eval_optimizer_loop,
)

intake_step = make_agent_step(
    agent_name="research-intake-agent",
    trace_name="research-intake",
    prompt_template="Intake this assignment for {change_id}: {assignment}",
)
chunk_step = make_agent_step(
    agent_name="research-chunker",
    trace_name="research-chunker",
    prompt_template="Break the assignment in {artifact_root}/intake/ into chunks.",
)
chunk_eval_step = make_sdk_evaluator_step(
    agent_name="research-plan-evaluator",
    trace_name="research-plan-evaluator",
    prompt_template="Evaluate the chunk plan in {artifact_root}/planning/chunks.yaml.",
)

@flow
def run_research_workflow(assignment: str, repo: str, change_id: str) -> None:
    context = WorkflowContext(run_id=change_id, repo=repo, runner="gemini", runner_model="gemini-2.5-pro")
    intake_step(workflow_context=context, assignment=assignment)
    run_eval_optimizer_loop(
        producer_func=chunk_step,
        producer_input=render_prompt_template(
            "Create a chunk plan for {change_id} using {artifact_root}/intake/.",
            context,
        ),
        evaluator_func=chunk_eval_step,
        evaluator_prompt=render_prompt_template(
            "Review {artifact_root}/planning/chunks.yaml for {change_id}.",
            context,
        ),
        runner=context.runner,
        runner_model=context.runner_model,
    )
```

The reusable API gives you:
- `WorkflowContext` for standard repo/run metadata
- `make_agent_step()` for Prefect + Opik wrapped agent tasks
- `make_sdk_evaluator_step()` for SDK-based evaluator tasks
- `run_eval_optimizer_loop()` and `run_uow_eval_loop()` for iterative producer/evaluator flows

---

## Synthetic Mode vs. ADO Mode

| | Synthetic | ADO |
|---|---|---|
| Credentials needed | ❌ None | ✅ Azure CLI |
| Network required | ❌ No | ✅ Yes |
| Input source | Local JSON file | Live ADO work item |
| ADO operations | Skipped | Active |

**Synthetic mode** is selected automatically when you pass `--story-file` (or use the default fixture). **ADO mode** is selected when you pass `--ado-url`.

---

## Synthetic Fixture Format

All synthetic story fixtures must be valid JSON objects with these required fields:

| Field | Type | Notes |
|---|---|---|
| `change_id` | string | e.g. `TEST-AC-001`. Can instead be passed via `--change-id`. |
| `title` | string | One-line title |
| `description` | string | Multi-line narrative |
| `acceptance_criteria` | list or object | See below |

### Acceptance Criteria

Either a list of strings or a keyed object — both are normalized to `AC1`, `AC2`, ... during intake:

```json
{ "acceptance_criteria": ["First criterion", "Second criterion"] }
```
```json
{ "acceptance_criteria": { "AC1": "First criterion", "AC2": "Second criterion" } }
```

Optional fields: `examples`, `constraints`, `non_functional_requirements`, `raw_input_notes`, `ado_metadata`.

### Bundled Fixtures

| File | Change ID | Purpose |
|------|-----------|---------|
| `agent-context/test-fixtures/synthetic_story.json` | `TEST-AC-001` | Smoke-test — validates workflow stages |
| `agent-context/test-fixtures/synthetic_story_medium.json` | `TEST-MEDIUM-001` | Multi-task decomposition scenario |

---

## Artifact Layout

```
agent-context/<change-id>/
├── intake/
│   ├── story.yaml        # Normalized story + acceptance criteria
│   ├── config.yaml       # Workflow config (includes project_type marker)
│   └── constraints.md    # Extracted constraints and open questions
├── planning/             # tasks.yaml, assignments.json
├── execution/            # impl_report.yaml per UoW
├── qa/                   # qa_report.yaml
└── logs/
```

---

## Workflow Stages

1. **Intake** — Normalizes fixture/ADO context into `intake/*` artifacts
2. **Task Generation** — Decomposes story into `tasks.yaml`
3. **Task Assignment** — Schedules units of work into `assignments.json`
4. **Implementation** — Executes each UoW, writes `impl_report.yaml`
5. **QA** — Validates outputs, writes `qa_report.yaml`
6. **Lessons Optimization** — Captures learnings and best practices

---

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `Synthetic story fixture not found` | Bad path | Use an absolute path or `~` expansion |
| `must be a JSON object` | Array at top level or invalid JSON | Wrap in `{}`, validate syntax |
| `missing required field(s)` | `change_id`/`title`/`description`/`acceptance_criteria` absent or empty | Add the missing field |
| `acceptance_criteria must be a non-empty list...` | Empty, `null`, or non-string values | Use a non-empty list or map of strings |
| `change_id does not match` | `--change-id` and fixture `change_id` conflict | Remove one, or make them match |
| `Provide either ado_url or story_file, not both` | Both flags passed | Pick one mode |

---

## Synthetic Mode Markers

After intake, synthetic runs are identifiable by:

- `config.yaml` → `project_type: 'synthetic-fixture'`
- `story.yaml` → `raw_input.source_type: synthetic_fixture`
- `story.yaml` → **no** `ado_provenance` key
