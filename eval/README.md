# eval

Evaluation and testing utilities for agent-runner.

---

## Unit & Integration Tests

```bash
# Run all tests
python -m pytest tests/ -v

# Run only the local API + GUI smoke tests
python -m pytest tests/test_server_routes.py tests/test_server_events.py -v

# Run only synthetic workflow tests
python -m pytest tests/test_steps_and_run.py::FullSyntheticWorkflowIntegrationTests -v

# Validate a fixture file
python -c "
from workflow_inputs import load_story_fixture
fixture = load_story_fixture('path/to/story.json')
print('✅ Valid')
"
```

The server smoke tests cover the FastAPI surface used by the local GUI:

- `/health`
- `/runs`
- `/settings`
- `/agents`
- `/corpus`
- `/evaluate/summary`
- `/evaluate/runs`
- JSONL event emission and cassette recording

---

## Evaluation Runner (`eval/run_eval.py`)

Runs the full agent pipeline against a test story, scores the result, and optionally logs to Opik.

### Basic usage

```bash
python eval/run_eval.py \
    --change-id EVAL-001 \
    --mono-root /path/to/mcs-products-mono-ui
```

### Key flags

| Flag | Default | Description |
|------|---------|-------------|
| `--change-id` | required | Story ID — must match a file in `eval/stories/<id>.json` |
| `--mono-root` | required | Path to the `mcs-products-mono-ui` repo |
| `--runner` | `claude` | Agent runner: `claude`, `copilot`, or `gemini` |
| `--gemini-model` | `gemini-2.5-flash` | Model when `--runner gemini`. Options: `gemini-2.5-pro`, `gemini-2.5-flash`, `gemini-3-pro-preview`, `gemini-3-flash-preview` |
| `--runs` | `1` | Total evaluation runs to execute |
| `--max-concurrent` | `1` | Max runs executing simultaneously |
| `--skip-pipeline` | off | Score the current repo state without re-running the pipeline |
| `--skip-materialize` | off | Skip agent materialization in `run.py` |
| `--skip-opik` | off | Print scores locally without logging to Opik |
| `--experiment-name` | auto | Opik experiment name (defaults to `eval-{id}-{timestamp}`) |
| `--testing-branch` | `agents/frozen-for-testing` | Frozen branch in `mcs-products-mono-ui` used as the test baseline |

### Parallel runs

`--runs` > 1 creates isolated git worktrees so runs don't interfere with each other. `--max-concurrent` controls how many execute simultaneously (backed by `ThreadPoolExecutor`).

```bash
# 10 runs, 3 at a time
python eval/run_eval.py \
    --change-id EVAL-001 \
    --mono-root /path/to/mcs-products-mono-ui \
    --runs 10 \
    --max-concurrent 3
```

> Keep `--max-concurrent` at or below your CPU core count.

### Common recipes

```bash
# Skip Opik (no credentials)
python eval/run_eval.py --change-id EVAL-001 --mono-root /path/to/mono --skip-opik

# Re-score without re-running the pipeline
python eval/run_eval.py --change-id EVAL-001 --mono-root /path/to/mono --skip-pipeline

# Use Gemini runner
python eval/run_eval.py --change-id EVAL-001 --mono-root /path/to/mono \
    --runner gemini --gemini-model gemini-2.5-pro
```

### Inspect results in the local GUI

The local API + GUI is useful when you want a manual view over run history, event streams, corpus summaries, or regressions while iterating on eval stories:

```bash
python server_main.py
# open http://127.0.0.1:8742
```

- **Runs** shows live stage/job events from `agent-context/<change-id>/events.jsonl`
- **Corpus** summarizes `eval/stories/*.json` with history-derived pass rates
- **Evaluate** reads the local job history and highlights regressions
- **Settings** edits `~/.agent-runner/config.json`

---

## Eval Stories

Story fixtures live in `eval/stories/<change-id>.json`. They follow the same [synthetic fixture format](../README.md#synthetic-fixture-format) as workflow fixtures.

| Story | Purpose |
|-------|---------|
| `EVAL-001` | Primary eval scenario |
| `EVAL-002` | Additional scenario |
| `EVAL-003` | Additional scenario |
