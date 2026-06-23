---
name: agent-workbench-harness
description: |
  Familiarize an agent with the Agent Workbench workflow harness. Use when investigating, modifying, debugging, or explaining runs that start from run.py, including stage orchestration, runner dispatch, artifact contracts, event logs, GUI job execution, and evaluation loops.
---

# Agent Workbench Harness

Use this skill to quickly build an accurate mental model of the Agent Workbench harness before changing it.

## When to Use

- You are asked to understand, debug, extend, or document the harness that starts at `run.py`.
- A workflow run failed and you need to trace which stage, agent, artifact, runner, or event caused it.
- You need to add or modify a stage, agent invocation, evaluator loop, artifact contract, runner integration, GUI run behavior, or observability field.
- You need to onboard a new agent to this repository's harness architecture.

## First Files to Read

Start with these files in this order:

1. `run.py`: CLI entrypoint and workflow stage orchestrator.
2. `core/steps.py`: stage prompt builders, agent calls, deterministic intake/fallback artifact writers, PR creation.
3. `core/evaluator_optimizer_loops.py`: producer/evaluator retry loops for planning, assignment, implementation, and QA.
4. `core/run_cmds.py`: runner dispatch, CLI execution, OpenAI-compatible tool runtime, metrics/events, failover hooks.
5. `core/artifact_utils.py`: artifact normalization, assignment parsing, implementation report validation.
6. `core/opik_integration.py`: evaluator SDK/CLI routing and prompt/file-content injection.
7. `core/check_gates.py`, `core/run_gates.py`, `agent-script-source/validate-artifact-schema.py`: canonical schema and programmatic gate behavior.
8. `core/workflow_inputs.py`: input source resolution for ADO, synthetic story files, and manual stories.
9. `core/repo_prep.py`: feature branch naming and checkout behavior.
10. `core/runtime_paths.py`: durable runtime state roots.
11. `server/runner_proc.py`, `server/jobs.py`, `server/events.py`, `server/routes/runs.py`: GUI/API job submission, subprocess lifecycle, event streaming, and user responses.
12. `.claude/agents/*.agent.md`: agent-specific artifact contracts and output schemas.

## Mental Model

`run.py` is the harness conductor. It does not implement product changes itself. It:

1. Resolves the workflow input and target repository.
2. Cleans stale per-change artifacts unless a server-managed event log is active.
3. Resolves runner/model configuration and per-agent overrides.
4. Prepares a feature branch in the target repo.
5. Runs a fixed sequence of stages.
6. Requires key artifacts after stages that must produce them.
7. Emits structured events and optional Opik traces.
8. Writes `summary/workflow_status.yaml` and `summary/run_metrics.yaml` at the end.

The harness has two major surfaces:

- CLI surface: `python run.py ...` runs a workflow directly.
- GUI/API surface: FastAPI routes submit a job, `server/jobs.py` queues it, `server/runner_proc.py` launches `run.py` as a subprocess, and `server/events.py` tails `events.jsonl` for live UI updates.

## Runtime State

Runtime data lives outside the git checkout unless overridden:

- Default data directory is platform-specific via `core/runtime_paths.py`.
- `AGENT_RUNNER_DATA_DIR` overrides the data root.
- `agent_context_root()` resolves to `<data-dir>/agent-context`.
- `logs_root()` resolves to `<data-dir>/logs`.

Per-change artifacts live under:

```text
<agent-context-root>/<CHANGE-ID>/
```

Important subdirectories:

- `intake/`: normalized story, config, constraints.
- `planning/`: task plan, assignments, evaluator outputs.
- `execution/<UOW-ID>/`: UoW specs, implementation reports, evaluator feedback.
- `qa/`: QA report and evidence.
- `pr/`: PR metadata and PR review.
- `summary/`: workflow status, metrics, copied event log.
- `escalations/`: request/response files for user escalation when enabled.

## Workflow Inputs

`core/workflow_inputs.py` accepts exactly one story source:

- `--ado-url`: live Azure DevOps intake. If `--change-id` is missing, it is inferred from the URL.
- `--story-file`: synthetic local JSON fixture. If no source is supplied, this defaults to `workflow-fixtures/synthetic_story.json`.
- `--manual-story-file`: JSON from manual UI/API story input.

The resolved `WorkflowInput` contains:

- `repo`: absolute target repository path.
- `change_id`: authoritative run id.
- `intake_mode`: `ado`, `synthetic`, or `manual`.
- `intake_source`: URL or file path.
- `branch_description_source`: title-like text used to build the feature branch name.

## Branch Preparation

`core/repo_prep.py` creates or checks out:

```text
feature/<change-id-slug>-<description-slug>
```

It requires the target repo to have local `develop` or `origin/develop`, checks out `develop`, pulls with `--ff-only`, then checks out or creates the feature branch.

Be careful: this is real git state in the target repo. Do not change this behavior without considering dirty worktrees, branch collisions, and server-managed runs.

## Stage Sequence

`run.py` uses `_Stage` to emit `stage.start` and `stage.end` events and set `AGENT_RUNNER_CURRENT_STAGE`.

Executable stages are:

1. `materialize`
2. `intake`
3. `task-generation`
4. `task-assignment`
5. `execution`
6. `qa`
7. `pr-review`

The historical lessons optimizer is disabled unconditionally.

### Stage 0: Materialize

Default behavior is to skip materialization. Passing `--materialize` runs `core/materialize.py`.

Materialization copies canonical agent, skill, and script sources into runner-specific generated directories for Claude, Codex, Copilot, Gemini, and OpenAI-compatible runners. It is operator-controlled so prompt-file changes stay explicit.

### Stage 1: Intake

Implemented in `steps.step_intake()`.

Inputs:

- `intake_source`
- `repo`
- `change_id`
- `intake_mode`
- optional `extra_context`
- prepared `feature_branch`

Outputs:

- `intake/story.yaml`
- `intake/config.yaml`
- `intake/constraints.md`

Synthetic and manual intake are deterministic Python writers. ADO intake invokes the intake agent with a prompt built by `build_intake_prompt()`.

After intake, `run.py` requires `intake/story.yaml`, emits `story.normalized`, and records original/normalized acceptance criteria counts where available.

### Stage 2: Task Generation

Implemented through `run_eval_optimizer_loop()` with:

- producer: `steps.step_task_gen_producer`
- evaluator: `steps.step_task_gen_evaluator`

Producer output:

- `planning/tasks.yaml`

Evaluator reads:

- `planning/tasks.yaml`

The loop injects evaluator feedback into later producer iterations and stops early when evaluator output contains `PASS`.

`steps.py` normalizes legacy task fields, can expand some calibration single-task plans, and has a Copilot compatibility fallback that writes a conservative `tasks.yaml` if Copilot did not materialize one.

### Stage 3: Task Assignment

Implemented through `run_eval_optimizer_loop()` with:

- producer: `steps.step_task_assigner`
- evaluator: `steps.step_assignment_evaluator`

Producer output:

- `planning/assignments.json`
- `execution/<UOW-ID>/uow_spec.yaml` for each scheduled UoW

Assignment normalization accepts legacy YAML or `execution_schedule` shapes and rewrites to canonical JSON with a top-level `batches` list. `run.py` requires `planning/assignments.json` before execution.

### Stage 4: Execution

Implemented by reading `planning/assignments.json`, sorting batches by `batch_id`, and executing each UoW with `run_uow_eval_loop()`.

Batch behavior:

- Batches run in order.
- A batch with `parallel_execution: true` and more than one UoW uses `ThreadPoolExecutor`.
- Otherwise UoWs run sequentially.

For each UoW, the loop calls:

1. `steps.step_software_engineer`
2. `artifact_utils.normalize_impl_report_file`
3. `artifact_utils.snapshot_impl_report_attempt`
4. `artifact_utils.validate_impl_report_alignment`
5. `steps.step_software_engineer_evaluator`
6. `_persist_impl_evaluator_feedback`

Key artifacts:

- `execution/<UOW-ID>/uow_spec.yaml`
- `execution/<UOW-ID>/impl_report.yaml`
- `execution/<UOW-ID>/impl_report_validation.yaml`
- `execution/<UOW-ID>/attempts/attempt-NNN/impl_report.yaml`
- `execution/<UOW-ID>/eval_impl_N.json`

The loop stops early on evaluator output containing `PASS`.

### Stage 5: QA

Implemented through `run_eval_optimizer_loop()` with:

- producer: `steps.step_qa_engineer`
- evaluator: `steps.step_qa_evaluator`

The harness pre-creates:

- `qa/evidence/test_output/`
- `qa/evidence/logs/`
- `qa/evidence/screenshots/`

Producer output:

- `qa/qa_report.yaml`
- evidence files under `qa/evidence/`

Evaluator reads `qa_report.yaml` and `intake/story.yaml`, then stops the loop early on `PASS`.

### Stage 6: PR Review

Skipped when `AGENT_RUNNER_EVALUATION_RUN` is truthy.

Otherwise `steps.step_pr_review()`:

1. Reads `run_metadata.feature_branch` from `intake/config.yaml`.
2. Requires the target repo to currently be on that feature branch.
3. Commits dirty worktree changes with message `Implement <change_id>`.
4. Pushes the branch.
5. Creates an Azure DevOps PR targeting `develop`.
6. Writes `pr/pr.json`.
7. Runs the `pr-reviewer` agent, which must write `pr/pr_review.md`.

If the reviewer does not write `pr_review.md`, a fallback markdown review artifact is written with the raw reviewer response.

## Producer/Evaluator Loops

`core/evaluator_optimizer_loops.py` defines two loops.

`run_eval_optimizer_loop()` is used for planning, assignment, and QA:

- Runs producer once.
- Runs evaluator.
- If evaluator output contains `PASS`, stops.
- Otherwise injects evaluator output as `## Evaluator Issues to Fix` into the next producer prompt.
- Emits `loop.start`, `loop.iteration.start`, `loop.iteration.end`, and `loop.end` events when event logging is active.
- If the loop exhausts all iterations without `PASS`, it records exhaustion but does not fail the workflow by itself. Only raised exceptions or required-artifact checks stop `run.py`.

`run_uow_eval_loop()` is used for implementation:

- Runs the software engineer for one UoW.
- Normalizes, snapshots, and validates `impl_report.yaml`.
- Runs the implementation evaluator.
- Persists evaluator output to `eval_impl_N.json`.
- Feeds evaluator output back to the next engineer attempt if needed.

Default loop count is 3. `--calibration-fast-mode` reduces loops to 1.

Evaluator functions usually call `core/opik_integration.py::call_evaluator_sdk()`, which loads the evaluator agent prompt, injects referenced artifact file contents, and routes by runner through the available SDK/CLI path. Producer functions usually call `core/run_cmds.py::run_agent_cmd()`.

## Runner and Model Resolution

`run.py` resolves a workflow-level runner/model and per-agent overrides.

Supported base runners are defined in `core/runner_models.py`:

- `claude`
- `codex`
- `copilot`
- `gemini`
- `openai-compat`

Custom aliases may be configured under `runner_aliases` in the server config. Per-agent overrides are accepted through:

- CLI: repeated `--agent-runner AGENT=RUNNER`
- CLI: repeated `--agent-model AGENT=MODEL`
- API: `agent_llm_overrides`

Valid agent names are listed in `run.py` as `AGENT_NAMES`. Unknown agent override keys fail input validation.

## Runner Dispatch

All stage agent invocations flow through `core/run_cmds.py::run_agent_cmd()`.

Dispatch paths:

- Claude: `run_claude_cmd()` invokes `claude -p ... --agent <agent> --model <model> --output-format json`.
- Copilot: `run_copilot_cmd()` invokes `copilot` or a Copilot alias binary and can fall back to embedded agent instructions if native custom agent use refuses.
- Gemini: `run_gemini_cmd()` embeds agent instructions and required skills into the prompt because Gemini CLI lacks the same custom-agent surface.
- Codex: `run_codex_cmd()` invokes `codex exec`, adds the per-change artifact dir as writable, and captures the final message through a temp output file.
- OpenAI-compatible: `run_openai_compat_cmd()` calls a local OpenAI-compatible chat endpoint with a bounded tool loop.

`run_agent_cmd()` also handles:

- disabled-agent checks
- `AGENT_RUNNER_FORCE_ESCALATION_TEST`
- usage-exhaustion failover via `RunnerFailoverPolicy`
- `runner.failover` and `runner.failover_unavailable` events

## OpenAI-Compatible Tool Runtime

The OpenAI-compatible path is important because it does not shell out to a full IDE agent. `_OpenaiCompatToolRuntime` exposes bounded tools:

- `list_dir`
- `read_file`
- `write_file`
- `write_task_plan`
- `write_assignments`
- `write_impl_report`
- `write_qa_report`
- `run_shell`
- `request_user_input`

Protected artifacts must use typed writers:

- `planning/tasks.yaml` -> `write_task_plan`
- `planning/assignments.json` -> `write_assignments`
- `execution/<uow_id>/impl_report.yaml` -> `write_impl_report`
- `qa/qa_report.yaml` -> `write_qa_report`

The runtime constrains reads to the runner root, agent-context root, current change dir, and target repo. Writes are constrained to the current change dir and target repo, with protections for generated directories, VCS directories, lock files, env/secret-like files, and prompt/agent source files.

## Artifact Contracts

The harness relies on artifacts more than return text. A stage may print plausible text and still fail if the required artifact is missing.

Critical artifacts:

- Intake: `intake/story.yaml`, `intake/config.yaml`, `intake/constraints.md`
- Task generation: `planning/tasks.yaml`
- Assignment: `planning/assignments.json`, `execution/<UOW-ID>/uow_spec.yaml`
- Implementation: `execution/<UOW-ID>/impl_report.yaml`
- Implementation evaluator: `execution/<UOW-ID>/eval_impl_N.json`
- QA: `qa/qa_report.yaml`, `qa/evidence/**`
- PR: `pr/pr.json`, `pr/pr_review.md`
- Summary: `summary/workflow_status.yaml`, `summary/run_metrics.yaml`, `summary/events.jsonl`

Agent prompt files under `.claude/agents/` contain the detailed schema expectations. Read the specific agent prompt before changing that stage's contract.

Schema and gate layers:

- Canonical validator: `agent-script-source/validate-artifact-schema.py`, materialized into runner script directories. It supports `tasks`, `assignments`, `impl_report`, and `qa_report`.
- Gate helpers: `core/check_gates.py` and `core/run_gates.py`, plus companion scripts such as `check-ac-coverage.py`, `check-dependency-cycles.py`, and `check-test-harnesses.py`.
- Harness normalization/alignment: `core/artifact_utils.py`.
- Evaluator prompts: `.claude/agents/*-evaluator.agent.md`; evaluators are instructed to run programmatic gates before rubric judgment.

Do not treat `scripts/validate-artifact-schema.py` as authoritative without checking it against `agent-script-source/validate-artifact-schema.py`; the repo copy has been observed to lag the canonical validator.

## Artifact Normalization and Validation

`core/artifact_utils.py` performs defensive cleanup because LLM-produced files may be malformed.

Important behavior:

- `normalize_impl_report_file()` repairs common YAML scalar issues and rewrites canonical YAML.
- `snapshot_impl_report_attempt()` copies current `impl_report.yaml` into `attempts/attempt-NNN/`.
- `validate_impl_report_alignment()` checks that `impl_report.yaml` matches the current `change_id`, `uow_id`, and domain terms from `uow_spec.yaml`.
- `parse_assignments_text()` accepts strict JSON, repaired JSON, JSON in fences, or legacy YAML.
- `normalize_assignments_file()` rewrites assignments to canonical JSON.

If a UoW fails before evaluator invocation, check `impl_report_validation.yaml` and the exception in `summary/workflow_status.yaml`.

## Events, Metrics, and Observability

Structured event logging is optional for direct CLI runs and enabled for server jobs through `AGENT_RUNNER_EVENT_LOG`.

Important event types:

- `job.start`, `job.end`
- `stage.start`, `stage.end`
- `story.source`, `story.normalized`
- `workflow.plan`
- `uow.start`, `uow.end`
- `loop.start`, `loop.iteration.start`, `loop.iteration.end`, `loop.end`
- `cli.invoke`, `cli.exit`
- `llm.call`
- `metrics`
- `runner.failover`
- `log`
- `user.prompt`, `user.response`, `user.prompt.timeout`

`run.py::_write_run_metrics()` reads events and writes `summary/run_metrics.yaml` with:

- stage durations
- UoW durations
- loop iteration counts
- CLI calls by agent
- LLM calls by agent and model
- token and cost totals
- latency summaries
- error categories
- prompt repetition and cost anomalies
- an answerability matrix describing which performance questions the telemetry can answer

Opik tracing is optional. `core/opik_tracing.py` disables tracing when settings are missing or endpoint startup fails, but runtime trace/span errors may still surface if tracing was successfully enabled.

## GUI/API Run Path

The FastAPI app is built in `server/app.py`.

Run submission flow:

1. `server/routes/runs.py::submit_run()` validates runner, model, story source, and per-agent overrides.
2. `server/jobs.py::JobManager.submit()` writes a job row, chooses event/cassette paths, and queues the job.
3. Worker creates `server/runner_proc.py::JobProcess`.
4. `JobProcess.start()` cleans stale artifacts, truncates the event log, starts a `FileTailer`, and spawns `run.py`.
5. `FileTailer` publishes events to `EventBus` and persists current stage, awaiting-input status, and metrics to the job database.
6. `JobProcess.wait()` determines final status from `job.end` and process exit code, formats failure summaries, and updates the job row.

Cancellation sends SIGTERM to the subprocess process group. `run.py` has a SIGTERM handler that writes a cancelled `workflow_status.yaml`, emits `job.end`, and exits 143.

## User Escalation

Interactive escalation is routed through `core/user_escalation.py` and exposed to agents as:

- MCP tool for Claude/Gemini/Codex paths.
- `request_user_input` typed tool for OpenAI-compatible path.
- script entrypoints in `agent-script-source/`.

Server-managed runs set:

- `AGENT_RUNNER_USER_ESCALATION=gui`
- `AGENT_RUNNER_ESCALATION_ROOT=<agent-context>/<CHANGE-ID>/escalations`

Headless mode sets:

- `AGENT_RUNNER_HEADLESS=1`
- default `AGENT_RUNNER_USER_ESCALATION=auto`

Agents should escalate only for blocking product decisions, approval decisions, or human-only clarifications that cannot be resolved from artifacts and repository evidence.

## Important Environment Variables

- `AGENT_RUNNER_DATA_DIR`: overrides the runtime data root.
- `AGENT_RUNNER_CHANGE_ID`: current change id.
- `AGENT_RUNNER_REPO`: target repo path.
- `AGENT_RUNNER_CURRENT_STAGE`: current stage for event/log attribution.
- `AGENT_RUNNER_EVENT_LOG`: enables structured JSONL event logging.
- `AGENT_RUNNER_JOB_ID`: server job id.
- `AGENT_CONTEXT_ROOT`: exported by server subprocess environment for job runs.
- `CHANGE_ID`: exported by server subprocess environment for job runs.
- `AGENT_RUNNER_USER_ESCALATION`: escalation mode, commonly `gui` or `auto`.
- `AGENT_RUNNER_ESCALATION_ROOT`: escalation file root.
- `AGENT_RUNNER_CASSETTE`: optional cassette capture path for hermetic runs.
- `AGENT_RUNNER_EVALUATION_RUN`: skips PR creation/review when truthy.
- `AGENT_RUNNER_FORCE_ESCALATION_TEST`: forces a synthetic escalation before agent runs.
- `OPENAI_COMPAT_HOST`: overrides the OpenAI-compatible API base host.
- `OPIK_BASE_URL`, `OPIK_URL_OVERRIDE`, `OPIK_API_KEY`: Opik configuration helpers.

## Common Debugging Paths

For a failed run:

1. Open `<agent-context-root>/<CHANGE-ID>/summary/workflow_status.yaml`.
2. Check `failed_stage`, `last_completed_stage`, `failure_summary`, and traceback.
3. Open `summary/run_metrics.yaml` and `summary/events.jsonl` if present.
4. Inspect the required artifact for the failed stage.
5. If the failure is in execution, inspect `execution/<UOW-ID>/impl_report_validation.yaml`, `eval_impl_N.json`, and `attempts/`.
6. If the run was server-managed, inspect the run events through `/runs/{job_id}/events` or the event log path stored on the job row.
7. If a runner failed, search for `llm.call`, `cli.exit`, `runner.failover`, and `log` events for the agent.

For missing artifacts:

- Verify the stage prompt in `core/steps.py`.
- Verify the corresponding `.claude/agents/*.agent.md` output contract.
- For OpenAI-compatible runs, confirm the agent used typed artifact writer tools instead of `write_file`.
- For Copilot runs, check whether compatibility fallback logic applies only to task generation and assignment.

For stale or mixed artifacts:

- Direct CLI runs call `clean_workspace()` unless `AGENT_RUNNER_EVENT_LOG` is set.
- Server runs pre-clean artifacts in `JobProcess.start()` and `run.py` skips cleanup when server event logging is active.
- `clean_change_workspace()` also removes sibling `-RUN-N` directories for non-isolated base change IDs.

For model/runner surprises:

- Check `runner`, `model`, and `agent_llm_overrides` in the job row or CLI args.
- Check `core/runner_models.py` for defaults and alias handling.
- Check `llm.call` events for the actual runner/model used per call.
- Check `runner.failover` events for quota-based fallback.

## Change Safety Rules

- Do not change artifact paths casually. Downstream stages, tests, and GUI summaries are path-coupled.
- Do not remove `_require_file()` checks in `run.py`; they are the harness's main protection against successful-looking no-op agents.
- Do not add automatic prompt/materialization changes to normal runs. Materialization is explicitly operator-controlled.
- Do not weaken OpenAI-compatible write protections without reviewing prompt/secret/generated-file safety.
- Keep evaluator loops deterministic: pass/fail is currently based on the substring `PASS` in evaluator output.
- Treat PR creation as side-effectful: it commits, pushes, and creates an Azure DevOps PR.
- Preserve server event semantics when adding stages; the GUI depends on stage, job, user, metrics, and log events.

## Useful Tests

Use focused tests around the subsystem you touch:

- `tests/test_workflow_inputs.py`
- `tests/test_steps.py`
- `tests/test_evaluator_optimizer_loops.py`
- `tests/test_artifact_utils.py`
- `tests/test_run_cmds.py`
- `tests/test_log_layout.py`
- `tests/test_server_routes.py`
- `tests/test_server_events.py`
- `tests/test_runner_models.py`
- `tests/test_runner_failover.py`
- `tests/test_repo_prep.py`

When a change touches the main stage sequence, run at least the workflow input, steps, loops, artifacts, and log layout tests.

## Quick Command Examples

Direct synthetic run:

```bash
python run.py --repo /path/to/target/repo --story-file workflow-fixtures/synthetic_story.json --runner claude --log-level info
```

Manual story run:

```bash
python run.py --repo /path/to/target/repo --manual-story-file /path/to/manual_story.json --runner claude
```

One-iteration calibration-style run:

```bash
python run.py --repo /path/to/target/repo --story-file workflow-fixtures/synthetic_story.json --calibration-fast-mode --headless
```

Per-agent override example:

```bash
python run.py --repo /path/to/target/repo --story-file workflow-fixtures/synthetic_story.json --runner claude --agent-runner qa-engineer=gemini --agent-model qa-engineer=gemini-2.5-flash
```

## Completion Checklist

Before claiming you understand or have safely changed the harness:

- You can explain the stage sequence from `run.py`.
- You know which artifact each stage must produce.
- You know whether the run is direct CLI or server-managed.
- You know which runner path is being used for each relevant agent.
- You checked the agent prompt file for any artifact contract you changed.
- You checked event and metrics implications for any stage, runner, or loop change.
- You ran focused tests or documented why they were not run.
