# Copilot instructions for `agent-workbench`

## Commands

```bash
# Preferred full local setup (starts local Opik + API + GUI)
./bootstrap.sh

# Manual local server startup
python3 -m pip install -r requirements.txt
python3 server/main.py
python3 server/main.py --reload

# Re-materialize runner assets after editing agent-definition-source/*,
# agent-skill-source/*, or agent-script-source/*
python3 core/materialize.py
python3 core/materialize.py --check

# Run the workflow runner against a target repo
python3 run.py --repo /absolute/path/to/target/repo
python3 run.py --repo /absolute/path/to/target/repo --story-file /path/to/story.json
python3 run.py --repo /absolute/path/to/target/repo --ado-url 'https://dev.azure.com/<org>/<project>/_workitems/edit/123456'

# Tests
python3 -m pytest -q tests/
python3 -m pytest -q tests/test_server_routes.py
python3 -m pytest -q tests/test_workflow_inputs.py::ResolveWorkflowInputErrorPathTests::test_medium__bundled_fixture_with_mismatched_change_id_raises_value_error
python3 -m pytest -q tests/test_steps_and_run.py::FullSyntheticWorkflowIntegrationTests

# Evaluation runner
python3 eval/run_eval.py --change-id EVAL-001 --mono-root /path/to/target/repo --skip-opik
```

## High-level architecture

- `run.py` is the workflow orchestrator. It resolves either a synthetic fixture or an Azure DevOps work item, materializes agents, then runs the six pipeline stages: `materialize -> intake -> task-generation -> task-assignment -> execution -> qa -> lessons-optimizer`. Task generation, assignment, and QA each use evaluator/optimizer loops; execution runs units of work batch-by-batch and can parallelize within a batch.
- `steps.py` contains the stage entrypoints. Stage outputs are written under `agent-context/<change_id>/` and form the contract between stages: intake writes `intake/story.yaml`, `intake/config.yaml`, and `intake/constraints.md`; planning writes `planning/tasks.yaml` and `planning/assignments.json`; execution writes `execution/*/impl_report.yaml`; QA writes `qa/qa_report.yaml`.
- `agent-definition-source/<agent>/vN/` is the source of truth for agent definitions, `agent-skill-source/<skill>/vN/` is the source of truth for skills, and `agent-script-source/` is the source of truth for helper scripts. `core/materialize.py` copies those assets into runner-specific `.claude/`, `.github/`, and `.gemini/` directories and records hashes in each runner root's `.materialization.json`. Runtime prompt loading goes through `agent_prompts.py`, which reads the materialized files.
- `run_cmds.py` is the runner abstraction over the external CLIs (`claude`, `copilot`, `gemini`). It also owns event emission and hermetic cassette recording hooks. Gemini is special: because its CLI has no top-level `--agent` flag, the materialized agent prompt is embedded directly into the prompt payload.
- `server/main.py` and `server/app.py` wrap the runner in a local FastAPI service plus browser UI (`gui/`). `server/jobs.py` queues jobs, `server/runner_proc.py` spawns `run.py` as a subprocess, `server/events.py` tails `agent-context/<change_id>/events.jsonl` into SSE streams, and `server/db.py` stores run metadata in `~/.agent-runner/jobs.db`.
- `eval/` is a separate evaluation harness. `eval/run_eval.py` runs fixtures from `eval/stories/`, can fan out isolated multi-run evaluations, and logs/scans results for the GUI's Corpus and Evaluate views.

## Key conventions

- Edit agent prompts in `agent-definition-source/*/v*/prompt.md`, skills in `agent-skill-source/*/v*/SKILL.md`, and helper scripts in `agent-script-source/*`. The `.claude/`, `.github/`, and `.gemini/` files are generated artifacts; re-run `python3 core/materialize.py` after changing sources.
- `change_id` is the stable join key across the whole system: artifact directories, event logs, SSE streams, Opik thread IDs, evaluation fixtures, and hermetic cassette filenames all key off it. Preserve it exactly.
- Synthetic mode is the default path. If `run.py` gets neither `--ado-url` nor `--story-file`, it falls back to `agent-context/test-fixtures/synthetic_story.json` (`TEST-AC-001`). `workflow_inputs.py` validates fixture shape and rejects mismatched `change_id` values early.
- Synthetic fixture `acceptance_criteria` may start as either a list or a keyed object, but intake normalizes downstream artifacts to `AC1`, `AC2`, ... in `intake/story.yaml`.
- Do not assume `planning/assignments.json` is strict JSON. The code explicitly tolerates YAML in that file, including duplicate `batch:` keys under `execution_schedule`; use `run.load_assignments()` rather than direct JSON parsing.
- Regular runs and evaluation runs share the same SQLite store and API surface, but they are separated by `run_kind`. `/runs` defaults to regular jobs; `/evaluate/runs` queues evaluation jobs. Keep that split intact when changing server routes or summaries.
- Direct CLI runs are intentionally side-effect free unless the server sets environment flags. Event emission only activates when `AGENT_RUNNER_EVENT_LOG` is set, and hermetic cassette recording only activates when `AGENT_RUNNER_CASSETTE` is set.
- Server-side mutable state lives under `~/.agent-runner/` by default (`config.json`, `jobs.db`, `cassettes/`, `memory/`). Tests redirect this with `AGENT_RUNNER_DATA_DIR`; follow that pattern when adding tests that touch server state.
- If you change runner integration code, dispatch LLM calls through `core/agent_cmd.py`, which delegates to the CLI subprocess runners in `core/run_cmds.py`.
- Tests are written with `unittest` style but run under `pytest`. Many test files use explicit node names and "difficulty rubric" comments at the top; follow the existing naming pattern when extending them.
