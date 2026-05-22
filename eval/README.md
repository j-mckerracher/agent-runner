# Workflow Evals

This eval harness answers one question:

> After a workflow change, can Agent Workbench still complete representative work items?

Each benchmark is a pair of files in `eval/benchmarks/<difficulty>/`:

| File | Visibility |
|---|---|
| `story.json` | Passed to the normal workflow as input |
| `hidden_tests.py` | Withheld until the workflow finishes, then run with pytest against the modified sandbox |

Three built-in difficulty levels exist: **easy**, **medium**, and **hard**.

---

## Quick start

### 1. Configure `.env`

The runner needs a target repo and a gold-master commit SHA. The easiest way
is to add them to `.env` at the workspace root (git-ignored):

```bash
EVAL_TARGET_REPO=/absolute/path/to/target/repo   # local path or remote URL
EVAL_TARGET_SHA=<gold-master-commit-sha>
EVAL_RUNNER=claude                                # or: copilot, gemini
```

These can also be passed directly as flags (see [All flags](#all-flags)).

### 2. Run evaluations

**All difficulties (default):**
```bash
python3 eval/runner.py \
  --runner copilot \
  --project-test-command "python3 -m pytest -q"
```

**One difficulty level:**
```bash
python3 eval/runner.py --difficulty easy \
  --runner copilot \
  --project-test-command "python3 -m pytest -q"
```

**Multiple difficulty levels:**
```bash
python3 eval/runner.py --difficulty easy medium \
  --runner copilot \
  --project-test-command "python3 -m pytest -q"
```

**A specific named benchmark:**
```bash
python3 eval/runner.py --benchmark easy \
  --runner copilot
```

---

## Generating benchmarks

### During bootstrap

Bootstrap can seed all three benchmark folders from your target repo using an
LLM:

```bash
./bootstrap.sh \
  --eval-target-repo /absolute/path/to/target/repo \
  --eval-target-sha <gold-master-commit-sha> \
  --generate-eval-benchmarks \
  --eval-runner claude \
  --no-opik
```

Bootstrap also writes the `.env` keys above automatically.

### Without bootstrap

```bash
python3 eval/seed_benchmarks.py --force
```

Add `--verify-gold-fails` when the target repo's test dependencies are already
installed and you want the generator to reject hidden tests that pass against
the gold-master commit (ensures tests are actually testing new behaviour).

---

## How the runner works

For each benchmark:

1. A fresh temporary sandbox is created.
2. The target repo is cloned and the gold-master SHA is checked out.
3. `run.py --headless` is run against the sandbox using `story.json`.
4. If `--project-test-command` is supplied, that command runs in the sandbox
   to check for regressions.
5. `hidden_tests.py` is copied into the sandbox and run with pytest.
6. The sandbox is cleaned up (regardless of outcome).

Results are printed to the terminal and (by default) written to
`eval/reports/latest.json`.

---

## All flags

| Flag | Default | Description |
|---|---|---|
| `--repo PATH` | `EVAL_TARGET_REPO` env / `.env` | Target Git repo path or URL to clone and test against. Required; falls back to the `EVAL_TARGET_REPO` environment variable or `.env` file. |
| `--sha SHA` | `EVAL_TARGET_SHA` env / `.env` | Gold-master commit SHA to check out before running the workflow. Required; falls back to `EVAL_TARGET_SHA`. |
| `--runner NAME` | `EVAL_RUNNER` env / `claude` | Agent runner backend: `claude`, `copilot`, or `gemini`. Falls back to the `EVAL_RUNNER` environment variable or `.env`, then `claude`. |
| `--model NAME` | runner default | Override the model for the selected runner. Falls back to `EVAL_MODEL` in `.env` when the override is compatible with the runner's allowed model list; otherwise uses the runner default. |
| `--difficulty LEVEL [LEVEL …]` | all benchmarks | One or more difficulty levels to run: `easy`, `medium`, `hard`. When omitted, all benchmarks in `--benchmarks-dir` are run. |
| `--benchmark NAME` | all benchmarks | Exact benchmark folder name(s) to run (e.g. `easy`). Repeat the flag for multiple names. Takes precedence over `--difficulty` when both are given. |
| `--benchmarks-dir PATH` | `eval/benchmarks` | Root directory that contains benchmark sub-folders. Override to point at a custom benchmark tree. |
| `--project-test-command CMD` | none | Shell command executed inside the sandbox after the workflow finishes, used to detect regressions (e.g. `python3 -m pytest -q`). Falls back to `EVAL_PROJECT_TEST_COMMAND` in `.env`. |
| `--workflow-timeout SECS` | `10800` (3 h) | Maximum wall-clock seconds allowed for the workflow stage (`run.py`) per benchmark before it is killed. |
| `--test-timeout SECS` | `300` (5 min) | Maximum seconds allowed for each test stage (project tests and hidden tests) per benchmark. |
| `--include-lessons` | off | Include the lessons-optimizer stage in the workflow. Disabled by default to keep eval runs faster. |
| `--keep-sandbox` | off | Preserve the temporary sandbox directory after the run completes. Useful for post-mortem debugging. |
| `--write-report / --no-write-report` | on | Write a JSON report to `eval/reports/`. Pass `--no-write-report` to skip writing. |
| `--log-level LEVEL` | `warning` | Logging verbosity passed through to `run.py`: `debug`, `info`, `warning`, `error`, or `critical`. |

---

## Adding a new benchmark

1. Create a directory inside the relevant difficulty folder:
   ```
   eval/benchmarks/easy/story.json
   eval/benchmarks/easy/hidden_tests.py
   ```

2. `story.json` must be a JSON object with these required fields:

   | Field | Type | Notes |
   |---|---|---|
   | `change_id` | string | Unique identifier, e.g. `EVAL-MY-001` |
   | `title` | string | Short work-item title |
   | `description` | string | Full description of the change |
   | `acceptance_criteria` | array of strings | Non-empty; each string is one criterion |

3. `hidden_tests.py` is a standard pytest file. It runs with the sandbox as
   the working directory and can import from the cloned repo via `PYTHONPATH`.

4. Run `python3 eval/runner.py --benchmark easy` to verify it works end-to-end.

---

## Reports

Each run writes two files to `eval/reports/`:

- `<YYYY-MM-DD-HHMMss>-<difficulty>.json` — timestamped copy, e.g. `2026-05-20-143022-easy.json`
- `latest.json` — always overwritten with the most recent run

```json
{
  "created_at": "2026-05-20T12:00:00Z",
  "repo": "/path/to/repo",
  "sha": "abc123",
  "runner": "copilot",
  "model": null,
  "results": [
    { "name": "easy",   "status": "PASS", "error": "",                    "seconds": 420.1 },
    { "name": "medium", "status": "FAIL", "error": "hidden tests failed", "seconds": 310.5 }
  ]
}
```
