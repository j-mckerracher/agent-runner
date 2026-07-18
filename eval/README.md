# Workflow Evals

This eval harness answers one question:

> After a workflow change, can Agent Workbench still complete representative work items?

Each generated benchmark is a pair of files in `<data-dir>/eval/benchmarks/<difficulty>/`:

| File | Visibility |
|---|---|
| `story.json` | Passed to the normal workflow as input |
| `hidden_tests.py` | Withheld until the workflow finishes, then run with pytest against the modified sandbox |

Three built-in difficulty levels exist: **easy**, **medium**, and **hard**.

For comparing two versioned v0.2 evaluation reports (`eval/report_schema.py`)
programmatically — AC/benchmark deltas, config drift, and a documented
`improved`/`regressed`/`mixed`/`unchanged`/`inconclusive` classification —
see [`docs/evaluation-comparison.md`](../docs/evaluation-comparison.md).
That layer is a standalone library today; it is not yet wired into this
runner's live execution or CLI.

---

## Quick start

### 1. Configure `.env`

The runner needs a target repo and a gold-master commit SHA. The easiest way
is to add them to `.env` at the workspace root (git-ignored):

```bash
EVAL_TARGET_REPO=/absolute/path/to/target/repo   # local path or remote URL
EVAL_TARGET_SHA=<gold-master-commit-sha>
EVAL_RUNNER=claude                                # or: codex, copilot, gemini, openai-compat
```

These can also be passed directly as flags (see [All flags](#all-flags)).

### 2. Run evaluations

**Minimal invocation (hidden tests only):**
```bash
python3 eval/runner.py --runner copilot
```

This is the simplest form. It runs all benchmarks using only the hidden
tests that ship with each benchmark. No additional test command is needed.

**With a project test command (optional):**
```bash
python3 eval/runner.py --runner copilot \
  --project-test-command "python3 -m pytest -q"
```

`--project-test-command` runs an arbitrary shell command inside the sandbox
after the workflow finishes. It is meant to catch regressions in the workflow's
own output — for example, running the generated code's test suite. See
[Project test command](#project-test-command) for details.

**Filter by difficulty:**
```bash
python3 eval/runner.py --difficulty easy
python3 eval/runner.py --difficulty easy medium
```

**A specific named benchmark:**
```bash
python3 eval/runner.py --benchmark easy
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
Gold-master failure verification is enabled during benchmark generation by
default. Pass `--no-verify-eval-gold-fails` only when debugging generator output
without the target repo's local test runtime installed.

### Without bootstrap

```bash
python3 eval/seed_benchmarks.py --force
```

By default, the generator runs each generated `hidden_tests.py` against the
gold-master commit and rejects tests that pass or fail for collection/runtime
setup reasons instead of the intended behavioral reason. Use
`--no-verify-gold-fails` only for local prompt debugging.

---

## How the runner works

For each benchmark:

1. A fresh temporary sandbox is created.
2. The target repo is cloned and the gold-master SHA is checked out.
3. The hidden tests are first verified against gold master; they must fail
   cleanly for the intended behavioral reason.
4. `run.py --headless` is run against the sandbox using `story.json`.
5. If `--project-test-command` is supplied, that command runs in the sandbox
   to check for regressions.
6. `hidden_tests.py` is copied into the sandbox and run with pytest plus JUnit
   XML output. Skipped hidden tests fail by default.
7. The sandbox is cleaned up (regardless of outcome).

Results are printed to the terminal and (by default) written to
`<data-dir>/eval/reports/latest.json` with AC-level quality, reliability, efficiency, and
optional baseline trend fields.

The local Evaluate UI is benchmark-report first: it launches `/evaluate/benchmark-runs`
through this runner, reads reports from `<data-dir>/eval/reports`, and shows warnings when a
baseline is missing, only one trial was run, hidden tests skipped, AC mapping is
incomplete, or the selected baseline differs by runner/model, target SHA, or
benchmark set.

---

## Project test command

`--project-test-command` is an **optional** shell command that runs inside the
sandbox after the workflow completes (step 5 of the runner flow). Its purpose is
to catch regressions: if the workflow modified the repo and the repo has its own
test suite, this command verifies those tests still pass.

### What it actually does

The command executes with `shell=True` in the **cloned target repo root** (the
sandbox workspace), not in the agent-workbench repo. The working directory is
the checkout of `EVAL_TARGET_REPO` at `EVAL_TARGET_SHA`, after the workflow has
modified it.

If the command exits non-zero, the benchmark fails with `"project tests failed"`.

### When to use it

Use `--project-test-command` when the **target repo** has a test suite the
workflow could break. Examples:

| Target repo type | Example command |
|---|---|
| Python project | `python3 -m pytest -q` |
| Python project (specific dir) | `python3 -m pytest -q tests/` |
| Node/TypeScript project | `npm test` |
| .NET project | `dotnet test` |

### When to omit it

**Omit `--project-test-command` when the target repo has no test suite** that
applies to the workflow's changes. The hidden tests alone provide the acceptance
criteria signal — the project test command is a regression safety net, not the
primary quality gate.

### Common pitfalls

**"no tests ran in 0.45s" → benchmark fails.** Pytest found zero test files in
the workspace root. This happens when:

- The target repo is not a Python project (e.g. an Angular monorepo has no
  `test_*.py` files).
- The workflow didn't generate pytest-discoverable test files.
- Test files exist but aren't named `test_*.py` or live in a subdirectory pytest
  doesn't scan by default.

Fix: either point pytest at the right subdirectory
(`--project-test-command "python3 -m pytest -q path/to/tests/"`), use the
target repo's native test runner (`npm test`, `dotnet test`), or omit the flag
entirely.

**Exit code 5 means "no tests collected."** Pytest uses exit code 5 when it
discovers zero tests. The eval runner treats any non-zero exit as failure.

**The command runs after the workflow, not before.** It tests the modified
sandbox, not the gold-master checkout. Gold-master verification is handled
separately by the hidden tests in step 3.

---

## All flags

| Flag | Default | Description |
|---|---|---|
| `--repo PATH` | `EVAL_TARGET_REPO` env / `.env` | Target Git repo path or URL to clone and test against. Required; falls back to the `EVAL_TARGET_REPO` environment variable or `.env` file. |
| `--sha SHA` | `EVAL_TARGET_SHA` env / `.env` | Gold-master commit SHA to check out before running the workflow. Required; falls back to `EVAL_TARGET_SHA`. |
| `--runner NAME` | `EVAL_RUNNER` env / `claude` | Agent runner backend: `claude`, `codex`, `copilot`, `gemini`, or `openai-compat`. Falls back to the `EVAL_RUNNER` environment variable or `.env`, then `claude`. |
| `--model NAME` | runner default | Override the model for the selected runner. Falls back to `EVAL_MODEL` in `.env` when compatible with the runner's model choices. For built-in `openai-compat`, omitting the model lets omp use its configured default; when set, any model name is passed through to `omp --model`. For `codex` and `openai-compat` aliases, any model name is accepted; `claude`/`copilot`/`gemini` require known models. |
| `--difficulty LEVEL [LEVEL …]` | all benchmarks | One or more difficulty levels to run: `easy`, `medium`, `hard`. When omitted, all benchmarks in `--benchmarks-dir` are run. |
| `--benchmark NAME` | all benchmarks | Exact benchmark folder name(s) to run (e.g. `easy`). Repeat the flag for multiple names. Takes precedence over `--difficulty` when both are given. |
| `--benchmarks-dir PATH` | `<data-dir>/eval/benchmarks` | Root directory that contains benchmark sub-folders. Override to point at a custom benchmark tree. |
| `--project-test-command CMD` | none | Shell command executed inside the sandbox after the workflow finishes, used to detect regressions (e.g. `python3 -m pytest -q`). Falls back to `EVAL_PROJECT_TEST_COMMAND` in `.env`. |
| `--workflow-timeout SECS` | `10800` (3 h) | Maximum wall-clock seconds allowed for the workflow stage (`run.py`) per benchmark before it is killed. |
| `--test-timeout SECS` | `300` (5 min) | Maximum seconds allowed for each test stage (project tests and hidden tests) per benchmark. |
| `--include-lessons` | off | Deprecated no-op. The lessons optimizer is disabled and is never included in workflow eval runs. |
| `--keep-sandbox` | off | Preserve the temporary sandbox directory after the run completes. Useful for post-mortem debugging. |
| `--reports-dir PATH` | `<data-dir>/eval/reports` | Directory for benchmark reports. Override to keep reports elsewhere. |
| `--write-report / --no-write-report` | on | Write a JSON report to `<data-dir>/eval/reports/`. Pass `--no-write-report` to skip writing. |
| `--log-level LEVEL` | `warning` | Logging verbosity passed through to `run.py`: `debug`, `info`, `warning`, `error`, or `critical`. |
| `--runs N` | `1` | Number of trials to run per benchmark. |
| `--allow-hidden-skips` | off | Allow skipped hidden tests. By default, hidden-test skips fail the benchmark. |
| `--no-verify-gold-fails` | off | Skip gold-master hidden-test failure verification. Intended only for local debugging. |
| `--compare-to PATH` | none | Baseline report JSON to compare against for trend classification. |
| `--update-baseline` | off | Write the current report as the baseline report. |

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
   Generated hidden tests must define `AC_TEST_MAP`, use AC-prefixed test names
   such as `test_ac1_handles_edge_case`, avoid skip/xfail behavior, and cover
   each acceptance criterion through behavior rather than brittle source-text
   checks whenever possible.

4. Run `python3 eval/runner.py --benchmark easy` to verify it works end-to-end.

---

## Reading the results

### Terminal output

The runner prints workflow progress in real time:

```
== easy (trial 1) ==
Preparing sandbox
Verifying hidden tests fail on gold master
Running workflow
Workflow progress: 16% — intake (stage 2/6)
Workflow progress: 33% — task-generation (stage 3/6)
...
Workflow progress: 83% — execution (3/4 UoWs complete)
Workflow progress: 100% — workflow complete
Running project tests
Running hidden tests
PASS
Finished easy in 420.1s
```

A final summary table follows:

```
Evaluation report
-----------------
easy                     trial 01 PASS
medium                   trial 01 FAIL (hidden tests failed)
hard                     trial 01 FAIL (timeout after 10800s)
```

### Report JSON structure

Each run writes two files to `<data-dir>/eval/reports/`:

- `<YYYY-MM-DD-HHMMss>-<difficulty>.json` — timestamped copy, e.g. `2026-05-20-143022-easy.json`
- `latest.json` — always overwritten with the most recent run

```json
{
  "created_at": "2026-05-20T12:00:00Z",
  "repo": "/path/to/repo",
  "sha": "abc123",
  "runner": "copilot",
  "model": null,
  "runs": 1,
  "summary": { ... },
  "results": [ ... ]
}
```

#### `results[]` — per-benchmark detail

Each result object captures one trial of one benchmark:

| Field | Description |
|---|---|
| `name` | Benchmark folder name (`easy`, `medium`, `hard`) |
| `run_id` | Unique run identifier (`easy-t1-20260520-120000000000`) |
| `status` | `"PASS"` or `"FAIL"` |
| `error` | Failure reason when status is `"FAIL"`; empty on pass |
| `score_weighted` | Quality weighted score (0.0–1.0), same as `quality.weighted_score` |
| `trial_index` | 1-based trial number when `--runs > 1` |
| `seconds` | Wall-clock seconds for the full benchmark trial |
| `quality` | Acceptance-criterion quality model (see below) |
| `metrics` | Wall time, token counts, per-agent breakdown |
| `hidden_tests` | JUnit summary of hidden pytest results plus `ac_results` map |
| `project_tests` | JUnit summary of project test command (when configured) |
| `story` | Snapshot of the story.json used for the run |

##### `quality` — AC-level quality model

```json
{
  "weighted_score": 0.7500,
  "ac_passed": 3,
  "ac_total": 4,
  "ac_failed_ids": ["AC4"],
  "critical_ac_failed": 0,
  "project_tests_passed": true,
  "hidden_tests_passed": true,
  "hidden_tests_skipped": 0
}
```

| Field | Meaning |
|---|---|
| `weighted_score` | `ac_passed / ac_total`. A single number between 0.0 and 1.0. 1.0 means every acceptance criterion was satisfied. |
| `ac_passed` | How many acceptance criteria had all their mapped hidden tests pass. |
| `ac_total` | Total number of acceptance criteria defined in story.json. |
| `ac_failed_ids` | Which ACs failed (at least one mapped test did not pass). |
| `critical_ac_failed` | How many of the failed ACs are marked `critical_acceptance_criteria` in the story metadata. |
| `project_tests_passed` | Whether `--project-test-command` exited 0 (only meaningful when that flag is set). |
| `hidden_tests_passed` | Whether all hidden tests passed with zero skips. |
| `hidden_tests_skipped` | Count of skipped hidden tests. Non-zero only fails the benchmark if `--allow-hidden-skips` is off. |

##### `metrics` — efficiency data

```json
{
  "wall_seconds": 420.1,
  "llm_session_seconds": 185.3,
  "tokens_in": 45000,
  "tokens_out": 12000,
  "tokens_total": 57000,
  "cost_usd": 0.0,
  "agent_sessions": 12,
  "by_agent": {
    "software-engineer": { "calls": 4, "duration_ms": 45000, "tokens_in": 18000, "tokens_out": 5000 },
    "task-generator":    { "calls": 1, "duration_ms": 12000, "tokens_in": 8000,  "tokens_out": 2000 }
  }
}
```

`cost_usd` is always 0.0 unless a cost model is configured. `by_agent` breaks down LLM usage per agent role.

##### `hidden_tests.ac_results` — per-AC test mapping

```json
{
  "AC1": {
    "tests": ["test_ac1_truncate_export_symbol_exists", "test_ac1_truncate_long_string_default_ellipsis"],
    "cases": [
      { "name": "test_ac1_truncate_export_symbol_exists", "status": "passed", "time": 0.5 },
      { "name": "test_ac1_truncate_long_string_default_ellipsis", "status": "passed", "time": 0.3 }
    ],
    "passed": true,
    "missing_cases": false,
    "failed": false,
    "skipped": false
  }
}
```

This is the direct trace from acceptance criterion to test outcome. `missing_cases: true` means the `AC_TEST_MAP` referenced a test name that never executed (likely a naming mismatch or collection error).

#### `summary` — aggregate over all benchmarks and trials

```json
{
  "quality": {
    "weighted_score": 0.7500,
    "ac_passed": 9,
    "ac_total": 12,
    "critical_ac_failed": 0,
    "hidden_tests_skipped": 0
  },
  "reliability": {
    "runs": 3,
    "passed": 2,
    "pass_rate": 0.6667,
    "weighted_score_mean": 0.7500,
    "failure_categories": { "hidden tests failed": 1 }
  },
  "efficiency": {
    "wall_seconds_mean": 350.2,
    "tokens_total_mean": 52000.0,
    "cost_usd_mean": 0.0
  },
  "warnings": ["Only one run; reliability unknown."],
  "trend": "insufficient data"
}
```

| Section | What it tells you |
|---|---|
| `quality` | Aggregate AC satisfaction across all benchmarks. High `weighted_score` with zero `critical_ac_failed` is the target. |
| `reliability` | How consistently the workflow passes. `pass_rate` below 1.0 means flaky or broken behavior. `failure_categories` groups failures by root cause so you can spot patterns. |
| `efficiency` | Average wall time and token consumption. Watch for regressions here when changing prompts or agent logic. |
| `warnings` | Actionable flags: single-run (unreliable), baseline mismatch, hidden skips, incomplete AC mapping. |
| `trend` | Only present when `--compare-to` is set. One of `"increased"`, `"decreased"`, `"same"`, or `"insufficient data"`. |

### Interpreting trends

Pass `--compare-to <data-dir>/eval/reports/baseline.json` to classify the current run against a known-good baseline:

| Trend | Meaning |
|---|---|
| `increased` | Quality improved by ≥ `--regression-quality-pp` (default 5pp), OR quality held steady while both wall time and tokens dropped by ≥ `--regression-efficiency-pct` (default 15%). |
| `decreased` | Quality dropped by ≥ the quality threshold, OR quality held steady while time or tokens grew by ≥ the efficiency threshold. |
| `same` | No statistically meaningful change in either direction. |
| `insufficient data` | No baseline was provided or the baseline is empty. |

Create a baseline after a known-good run:

```bash
python3 eval/runner.py --difficulty easy medium hard --runs 3 --update-baseline
```

This writes `<data-dir>/eval/reports/baseline.json`. Subsequent runs can compare against it:

```bash
python3 eval/runner.py --compare-to "$AGENT_RUNNER_DATA_DIR/eval/reports/baseline.json"
```

### Common failure modes

| Error message | What it means | What to check |
|---|---|---|
| `hidden tests unexpectedly passed on gold master` | Tests don't fail against the unmodified repo. The benchmark is misaligned with the gold-master SHA. | Regenerate the benchmark against the correct SHA, or verify the repo was checked out correctly. |
| `hidden tests errored on gold master` | Tests crashed during gold-master verification (pytest exit code 2–5). Runtime or dependency missing. | Check the target repo's dependencies are installed. Tests must not require a runtime that isn't available locally. |
| `hidden tests skipped on gold master` | Tests contained a skip condition that fired. | Regenerate or repair the benchmark. Hidden tests must not skip. |
| `workflow failed` | `run.py` exited non-zero. | Check the workflow logs in `<data-dir>/logs/<run_id>/`. Look for agent errors, timeouts, or model refusals. |
| `project tests failed` | The `--project-test-command` exited non-zero after the workflow modified the sandbox. | Check the stdout/stderr printed above the error. Common causes: pytest found no tests (`no tests ran`, exit code 5), the command is wrong for the target repo type, or the workflow introduced a regression. See [Project test command](#project-test-command). |
| `hidden tests failed` | One or more hidden tests did not pass after the workflow. | Look at `hidden_tests.ac_results` to see which ACs failed. Check individual test case messages. |
| `hidden tests skipped` | A hidden test skipped at runtime. | Regenerate the benchmark. Skips are banned unless `--allow-hidden-skips` is set. |
| `AC_TEST_MAP entries did not execute` | A test name in `AC_TEST_MAP` has no matching JUnit case. Either the test was never collected or the name doesn't match. | Check for typos, parametrized test name mismatches, or collection errors. |
| `timeout after Ns` | The workflow, project tests, or hidden tests exceeded their timeout. | Increase `--workflow-timeout` or `--test-timeout`, or investigate what hung. |

### How many runs?

A single run (`--runs 1`) tells you whether the workflow _can_ pass. It does not tell you whether it _reliably_ passes.

- **1 run**: Quick smoke test during development.
- **3 runs**: Minimum for a reliability signal. The report will warn about single runs.
- **5+ runs**: Meaningful `pass_rate` and failure category distribution.

For baseline creation, use at least 3 runs so the trend classifier has stable means to compare against.

### Using the GUI

The Evaluate panel in the local UI (http://127.0.0.1:8742) is benchmark-report first:

1. Select a benchmark difficulty and runner/model.
2. Click **Run Benchmarks** to launch `eval/runner.py` as a server job.
3. The results panel loads the latest `<data-dir>/eval/reports/latest.json` and displays:
   - Per-benchmark pass/fail status with error messages
   - Quality scores (weighted and AC-level)
   - Efficiency metrics (wall time, tokens)
   - Aggregate summary with warnings
   - Trend comparison when a baseline exists

Warnings are shown prominently when: no baseline is selected, only one trial was run, hidden tests skipped, AC mapping is incomplete, or the selected baseline differs in runner/model, target SHA, or benchmark set.

---

## Reports

Each run writes two files to `<data-dir>/eval/reports/`:

- `<YYYY-MM-DD-HHMMss>-<difficulty>.json` — timestamped copy, e.g. `2026-05-20-143022-easy.json`
- `latest.json` — always overwritten with the most recent run

---

## Single-Agent Artifact Evals

The workflow benchmark runner remains the end-to-end acceptance gate. The
single-agent eval runner is a cheaper optimization loop for prompt and context
changes before you spend a full workflow run.

The first supported agent is `task-generator`. It writes and scores
`planning/tasks.yaml` from prepared `intake/story.yaml` and
`intake/constraints.md` artifacts.

### Dry-run smoke check

```bash
python3 eval/agent_runner.py \
  --agent task-generator \
  --dataset "$AGENT_RUNNER_DATA_DIR/eval/agent_datasets/task-generator/smoke.jsonl" \
  --dry-run
```

Dry-run mode writes a deterministic valid task plan. Use it to validate the
harness, datasets, reports, and CI gates without calling an LLM runner.

### Run a real task-generator eval

```bash
python3 eval/agent_runner.py \
  --agent task-generator \
  --dataset "$AGENT_RUNNER_DATA_DIR/eval/agent_datasets/task-generator/smoke.jsonl" \
  --runner openai-compat \
  --model minimax-m2.7 \
  --context-pack schema-examples-v1 \
  --runs 3
```

Reports are written to `<data-dir>/eval/agent_reports/task-generator/` and mirrored to
`<data-dir>/eval/agent_reports/task-generator/latest.json`.

### Compare prompt and context variants

```bash
python3 eval/agent_runner.py \
  --agent task-generator \
  --dataset "$AGENT_RUNNER_DATA_DIR/eval/agent_datasets/task-generator/regression.jsonl" \
  --runner openai-compat \
  --model minimax-m2.7 \
  --context-pack ac-checklist-v1 \
  --prompt-path agent-definition-source/task-generator/v3-candidate/prompt.md \
  --compare-to "$AGENT_RUNNER_DATA_DIR/eval/agent_reports/task-generator/baseline.json" \
  --runs 3
```

Promotion criteria should include the single-agent report and the existing
end-to-end workflow benchmark report. A candidate prompt or context pack should
not be promoted if easy/medium workflow benchmarks regress.

### Opik logging

Add `--opik` to attempt Opik trace and feedback-score logging. The local JSON
report remains the source of truth, and the run degrades safely when Opik is not
configured.

```bash
python3 eval/agent_runner.py \
  --agent task-generator \
  --dataset "$AGENT_RUNNER_DATA_DIR/eval/agent_datasets/task-generator/smoke.jsonl" \
  --runner openai-compat \
  --model minimax-m2.7 \
  --context-pack baseline \
  --opik
```
