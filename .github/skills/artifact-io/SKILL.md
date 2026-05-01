---
name: artifact-io
description: |
  Artifact I/O conventions and directory structure for multi-agent workflows. Use this skill when an agent needs to read or write workflow artifacts, understand the CHANGE-ID directory structure, or determine correct file paths for inputs and outputs. Provides: (1) Artifact root convention — {{artifact_root}}{CHANGE-ID}/ as the base path for all workflow artifacts, (2) Standard directory structure — intake/, planning/, execution/, qa/, summary/, logs/ with defined purposes, (3) Read/write permission model — which paths each agent role may access, (4) CHANGE-ID templating — how to construct artifact paths using the change identifier. Keywords: artifact root, CHANGE-ID, directory structure, intake, planning, execution, qa, summary, logs, artifact path, workflow artifacts, read write permissions, file path conventions.
---

## Dynamic context to inject

Use Claude Code's `!` pre-execution syntax when you need to resolve the active change identifier from the local workflow state instead of leaving `{CHANGE-ID}` abstract.

```text
!`cat .current-change-id 2>/dev/null || echo 'no active CHANGE-ID'`
```

Use the injected value to replace the placeholder when the workflow has already established an active change.

# Artifact I/O & Directory Conventions

Standard conventions for reading and writing workflow artifacts in multi-agent systems.

## When to Use This Skill

Activate this skill when:

- An agent needs to determine where to read input artifacts
- An agent needs to determine where to write output artifacts
- Setting up artifact directory paths for a new workflow stage
- Understanding the permission model for artifact access
- Constructing file paths with CHANGE-ID templating

## Artifact Root

All workflow artifacts live under a single root:

```
{{artifact_root}}{CHANGE-ID}/
```

- `{{artifact_root}}` is configured at workflow start
- `{CHANGE-ID}` is the unique identifier for the current workflow run
- The workflow runner defaults this root to `<code_repo>/agent-context` unless an explicit artifact root is provided

## Standard Directory Structure

```
{CHANGE-ID}/
├── intake/                    # Stage 1: Normalized inputs
│   ├── story.yaml             # Normalized story with numbered ACs
│   ├── config.yaml            # Model assignments, run metadata
│   └── constraints.md         # Technical context, examples, PRD refs
│
├── planning/                  # Stages 2-3: Task planning and assignment
│   ├── tasks.yaml             # Broad task plan
│   ├── assignments.json       # Execution schedule
│   ├── eval_tasks_k.json      # Task plan evaluations (k = attempt #)
│   └── eval_assignments_k.json # Assignment evaluations
│
├── execution/                 # Stage 4: Per-UoW implementation
│   └── {UOW-ID}/
│       ├── uow_spec.yaml      # UoW specification and DoD
│       ├── impl_report.yaml   # Implementation report
│       ├── eval_impl_k.json   # Implementation evaluations
│       └── logs/              # Per-UoW execution logs
│
├── qa/                        # Stage 5: Quality assurance
│   ├── qa_report.yaml         # QA validation report
│   ├── eval_qa_k.json         # QA evaluations
│   └── evidence/              # Screenshots, logs, test output
│       ├── screenshots/
│       ├── test_output/
│       └── logs/
│
├── summary/                   # Stage 6: Post-workflow
│   └── lessons_optimizer_report.yaml
│
├── status/                    # Escalation and pause state
│   ├── escalated.json         # Written when workflow needs human input
│   ├── paused.json            # Marker: workflow is paused
│   ├── resume.json            # Created by human to clear escalation
│   └── escalated_archive/     # Archived escalations (timestamped)
│
└── logs/                      # Workflow and agent execution logs
    ├── workflow_runner/
    ├── intake/
    ├── reference_librarian/
    ├── task_generator/
    ├── assignment/
    ├── software_engineer/
    ├── qa/
    ├── information_explorer/
    └── lessons_optimizer/
```

## Permission Model

### By Agent Role

| Agent Role               | May Read                                                                                | May Write                                                                                                                  |
| ------------------------ | --------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| **Workflow Runner**      | All artifacts                                                                           | `logs/workflow_runner/` (and stage artifact synthesis during dry-run only)                                                 |
| **Intake Agent**         | workflow context, explicit planning docs, `intake/*`                                    | `intake/*`, `logs/intake/`                                                                                                 |
| **Task Generator**       | `intake/*`, knowledge (via librarian)                                                   | `planning/tasks.yaml`, `logs/task_generator/`                                                                              |
| **Task Assigner**        | `intake/*`, `planning/tasks.yaml`, knowledge (via librarian)                            | `planning/assignments.json`, `logs/assignment/`                                                                            |
| **Software Engineer**    | `intake/*`, `planning/*`, `execution/{UOW-ID}/uow_spec.yaml`, knowledge (via librarian) | `execution/{UOW-ID}/impl_report.yaml`, `execution/{UOW-ID}/logs/`, source code in `code_repo`                              |
| **QA Engineer**          | All artifacts (read-only), `code_repo` (read-only)                                      | `qa/*`, `logs/qa/`                                                                                                         |
| **Evaluators**           | Stage-specific input + output artifacts                                                 | `planning/eval_*_k.json`, `execution/{UOW-ID}/eval_impl_k.json`, `qa/eval_qa_k.json`                                       |
| **Information Explorer** | `agent-context/knowledge/*`, repo docs, web                                             | `logs/information_explorer/`                                                                                               |
| **Lessons Optimizer**    | `agent-context/lessons.md`, agent prompts (read-only)                                   | `summary/*`, `logs/lessons_optimizer/`, `agent-context/rule-recommendations.md`, `agent-context/mistake-rate-tracker.json` |

### Universal Permissions

All agents may:

- **Read** artifacts relevant to their stage
- **Write** to `agent-context/lessons.md` (append-only)
- **Write** to their designated log directory

All agents MUST NOT:

- Write outside their designated paths
- Modify artifacts from other stages (read-only)
- Access credentials or environment files

## Path Construction

### Pattern

```
{artifact_root}{CHANGE-ID}/{stage}/{filename}
```

### Examples

```yaml
# Intake artifacts
input: "{CHANGE-ID}/intake/story.yaml"
input: "{CHANGE-ID}/intake/constraints.md"

# Planning artifacts
output: "{CHANGE-ID}/planning/tasks.yaml"
output: "{CHANGE-ID}/planning/assignments.json"

# Execution artifacts (per UoW)
output: "{CHANGE-ID}/execution/UOW-001/impl_report.yaml"

# QA artifacts
output: "{CHANGE-ID}/qa/qa_report.yaml"

# Evaluation artifacts (k = attempt number)
output: "{CHANGE-ID}/planning/eval_tasks_1.json"
output: "{CHANGE-ID}/execution/UOW-001/eval_impl_2.json"

# Log artifacts
output: "{CHANGE-ID}/logs/task_generator/20260127_143500_session.json"
```

## Knowledge Directory (Separate)

Knowledge files are NOT under `{CHANGE-ID}/` — they persist across workflow runs:

```
agent-context/knowledge/
├── accumulated-knowledge.md
├── learnings.json
├── information-index.json
├── questions.json
├── standing-questions.md
└── rls-system-architecture.md
```

Access to these files is managed exclusively through the Reference Librarian.

---

## Automated Scaffold Script

Use `{workflow_assets_root}/scripts/init-artifact-dirs.py` to create the standard artifact directory tree:

```bash
{workflow_assets_root}/scripts/init-artifact-dirs.py <artifact_root> <CHANGE-ID>
```

**When to use**: At the start of any workflow, before writing any artifacts. This replaces manual `mkdir -p` commands.

**Output**: JSON to stdout with `status`, `artifact_root`, `change_id`, `directories_created`.

**Exit codes**: 0 = success, 2 = usage error.
