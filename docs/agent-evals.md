# Agent-Level Eval Strategy

Agent Workbench now has a lightweight eval layer for optimizing individual
agents before running the full hidden-test benchmark suite.

## Recommended loop

1. Use the existing `eval/runner.py` hidden-test workflow benchmark as the final
   release gate.
2. Use `eval/agent_runner.py` for cheaper prompt and context-pack iteration on a
   single agent.
3. Track every variant by dataset SHA, prompt SHA, context-pack SHA, runner,
   model, and report fingerprint.
4. Promote a candidate only after it passes the relevant single-agent suite and
   does not regress the full easy/medium workflow benchmarks.

## First pilot: task-generator

The initial supported agent is `task-generator`. It consumes prepared intake
artifacts and writes `planning/tasks.yaml`.

```bash
python3 eval/agent_runner.py \
  --agent task-generator \
  --dataset eval/agent_datasets/task-generator/smoke.jsonl \
  --dry-run
```

Live model run:

```bash
python3 eval/agent_runner.py \
  --agent task-generator \
  --dataset eval/agent_datasets/task-generator/regression.jsonl \
  --runner openai-compat \
  --model minimax-m2.7:cloud \
  --context-pack schema-examples-v1 \
  --runs 3
```

## Scoring

The task-generator scorer combines:

- deterministic gates: artifact presence, schema shape, dependency validity, and
  task-count sanity;
- AC coverage: every required AC must appear in at least one task mapping;
- dependency and granularity: no cycles, no missing dependency references, sane
  number of tasks;
- scope and testability: no forbidden-scope phrases and at least one test or
  verification task when required.

Default weights are defined in `eval/agent_metrics.py`.

## Context packs

Context packs live under `eval/context_packs/<agent>/`. They are loaded by name
with `--context-pack` and rendered into the agent prompt. This allows controlled
comparisons such as:

```text
current prompt + baseline context
current prompt + AC checklist context
candidate prompt + best context
```

## Optional Opik logging

Pass `--opik` to attempt Opik trace and feedback-score logging. The runner is
safe when Opik is unavailable, and local JSON reports remain the source of truth.

## CI examples

Fast harness validation:

```bash
python3 eval/agent_runner.py \
  --agent task-generator \
  --dataset eval/agent_datasets/task-generator/smoke.jsonl \
  --dry-run \
  --fail-under 0.99 \
  --require-pass-rate 1.0
```

Candidate validation:

```bash
python3 eval/agent_runner.py \
  --agent task-generator \
  --dataset eval/agent_datasets/task-generator/regression.jsonl \
  --runner openai-compat \
  --model minimax-m2.7:cloud \
  --context-pack ac-checklist-v1 \
  --compare-to eval/agent_reports/task-generator/baseline.json \
  --fail-under 0.80
```
