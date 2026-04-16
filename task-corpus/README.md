# Task Corpus

This directory contains evaluable tasks for the Agent Runner evaluation harness.

## Directory Structure

Each task lives in its own subdirectory named after the task ID:

```
task-corpus/
  <task-id>/
    task.yaml                    — task definition (required)
    inputs/                      — input files referenced by task
    criteria/
      deterministic/             — scripts for deterministic ACs
```

## Task Format (task.yaml)

Tasks follow the **dual-format convention**: acceptance criteria are split into
`deterministic` (verifiable without an LLM) and `rubric` (require a judge LLM).

```yaml
id: my-task-id              # lowercase-hyphenated, matches directory name
version: 1
title: "Human-readable title"
difficulty: easy | medium | hard
tags: []
substrate:
  ref: baseline-2026-04-16  # references substrates/substrates.yaml
workflow:
  id: standard
  version: 1
agents:
  - agent-name@1.0.0
inputs: {}                  # key-value inputs passed to the runner
models:
  worker: claude-sonnet-4-5
  judge: gpt-5.4-high       # only needed for rubric criteria
acceptance_criteria:
  deterministic:            # at least one of deterministic/rubric required
    - id: unique-criterion-id
      description: "What this checks"
      kind: file_exists | script | event_assertion | schema_valid
      path: relative/path   # for file_exists, schema_valid
      script: path/to/check.py  # for script (relative to task dir)
      event: {kind: eval.pass, stage: qa}  # for event_assertion
  rubric:
    - id: rubric-criterion-id
      description: "What this evaluates"
      scale: "0-3"
      threshold: 2
      judge_prompt: |
        Instructions for the judge LLM...
calibration:                # optional: calibration metadata
  model: claude-sonnet-4-5
  runs: 5
  target_pass_rate: 0.8
```

## Dual-Format Convention

- **Deterministic criteria** are evaluated without LLM calls. They check
  for file existence, run scripts, verify events, or validate schemas.
  These are fast, cheap, and reproducible.

- **Rubric criteria** require a judge LLM to score the agent's output
  on a numeric scale. Use these for subjective quality assessments.

Tasks may have only deterministic, only rubric, or both types of criteria.

## Tasks

| Task | Difficulty | Description |
|------|-----------|-------------|
| `ado-normalize-embedded-ac` | medium | Extract ACs from embedded HTML ADO payload |
| `simple-readme-update` | easy | Update README with a one-liner change |
| `refactor-util-module` | hard | Refactor utility module (calibration pending) |

## Adding a New Task

1. Create a directory: `task-corpus/<new-task-id>/`
2. Write `task.yaml` following the format above.
3. Add any input files to `inputs/`.
4. Add criterion scripts to `criteria/deterministic/`.
5. Run `agent-runner-harness evaluate --task <new-task-id>` to test.
6. Run `agent-runner-harness calibrate --task <new-task-id> --k 5` to set a baseline.
