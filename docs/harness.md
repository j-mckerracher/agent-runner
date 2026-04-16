# Evaluation Harness

The harness (`packages/harness`) owns task corpus loading, run scheduling,
container lifecycle, gateway configuration, grading, baseline management,
lineage recording, and regression reports. It invokes the orchestrator
(`packages/runner`) as an opaque subprocess.

For the architectural rationale, see [01-broad-architecture.md](01-broad-architecture.md)
§9.3 and §11 (control flow). For calibrating a new task, see
[docs/calibration.md](calibration.md).

---

## 1. Subcommands

The harness CLI is `agent-runner-harness`. In a dev environment with `PYTHONPATH`
set:

```bash
PYTHONPATH=packages/shared:packages/runner:packages/registry:packages/harness \
  python -m agent_runner_harness.cli.main <subcommand> [flags]
```

### 1.1 `evaluate`

Run an evaluation cycle against one or all corpus tasks.

```
evaluate [--task ID] [--k INT] [--dev-mode] [--judge-stub] [--corpus DIR]
         [--sources DIR] [--runs-root DIR] [--judge-model MODEL]
         [--cassette-mode live|record|replay] [--image TAG]
```

- `--task ID` — evaluate a single task; omit to evaluate the full corpus.
- `--k INT` — number of runs per task (default 1).
- `--dev-mode` — skip container; run in-process/subprocess (default: true).
- `--no-dev-mode` — authoritative mode: requires `--image`.
- `--judge-stub` — replace the LLM judge with a deterministic stub (always passes
  rubric criteria); useful for smoke-testing structure without real judge calls.
- `--cassette-mode` — `live` (default), `record`, or `replay`.

### 1.2 `calibrate`

Run K iterations of a single task and save the resulting baseline band.

```
calibrate --task ID [--k INT] [--baselines-dir DIR] [--judge-stub] [--dev-mode]
          [--corpus DIR] [--sources DIR] [--runs-root DIR] [--judge-model MODEL]
```

- `--baselines-dir DIR` — where to write `<task_id>.json` (default: `baselines/`).
- `--k INT` — number of runs (default 1; use 5–10 for real calibration).

Writes `<baselines-dir>/<task_id>.json` on completion. See [docs/calibration.md](calibration.md).

### 1.3 `baseline`

Read or inspect a saved baseline band.

```
baseline show --task ID [--baselines-dir DIR]
```

Prints the stored `BaselineBand` as JSON.

### 1.4 `record`

Alias for `evaluate --cassette-mode record`. Records LLM/HTTP traffic to cassettes.

### 1.5 `replay`

Alias for `evaluate --cassette-mode replay`. Replays from cassettes; fails closed
on cassette miss.

### 1.6 `report`

Render a human-readable report from an existing run directory.

```
report <RUN_DIR>
```

Reads `*/grading.json` files under `RUN_DIR` and prints a cycle summary.

### 1.7 `materialize`

Materialize agent bundles from the registry into a target directory.

```
materialize --target DIR [--sources DIR] [--agents REF ...]
```

Delegates to `packages/registry`. See [docs/registry.md](registry.md) §5.

---

## 2. Worked example: dev-mode evaluation cycle

```bash
# 1. Activate the venv
source .venv/bin/activate

# 2. Smoke-test a single task with a stub judge (no LLM calls)
PYTHONPATH=packages/shared:packages/runner:packages/registry:packages/harness \
  python -m agent_runner_harness.cli.main evaluate \
    --task simple-readme-update \
    --k 1 \
    --dev-mode \
    --judge-stub

# 3. Inspect the run directory
ls runs/cycle-*/run-*/
# → artifacts/  event.log.jsonl  grading.json  lineage.json  run.log.json

# 4. Read the grading record
cat runs/cycle-*/run-*/grading.json

# 5. Generate a report
PYTHONPATH=packages/shared:packages/runner:packages/registry:packages/harness \
  python -m agent_runner_harness.cli.main report runs/cycle-<id>
```

---

## 3. Run lifecycle

For each run (one `(task, k_index)` pair), the harness executes:

```
1. Materialize agents
   └─ registry.load_bundles(sources_dir)
   └─ registry.materialize(bundles, run_dir / "artifacts/.claude/agents")

2. Resolve substrate
   └─ substrates.load_manifest(substrates.yaml)
   └─ substrate.commit → used in lineage header

3. Start LLM/HTTP gateway (if cassette_mode != "live")
   └─ gateway.start_gateway(mode, cassette_dir)

4. Invoke runner subprocess
   └─ python -m agent_runner.cli.headless --workflow standard --dry-run ...
   └─ stdout/stderr captured; ##EVENT## lines written to event.log.jsonl

5. Build and persist lineage header
   └─ builds RunLineage from all resolved inputs
   └─ writes runs/<cycle_id>/<run_id>/lineage.json

6. Grade
   └─ grading.grade(task, artifact_dir, event_log, judge_model)
   └─ deterministic checks first (file_exists, script, event_assertion, schema_valid)
   └─ rubric judge second (skipped if deterministic layer fails and task is rubric-optional)
   └─ writes runs/<cycle_id>/<run_id>/grading.json

7. Emit run.end event (##EVENT## line)
```

In dev mode, step 1 (materialization into a run-scoped directory) happens but no
container is started. The runner subprocess is invoked in `--dry-run` mode, which
synthesizes outputs without real LLM calls.

---

## 4. On-disk layout under `runs/`

```
runs/
  <cycle_id>/           e.g. cycle-a1b2c3d4
    <run_id>/           e.g. run-e5f6a7b8
      artifacts/        — output artifacts from the runner
        story.yaml
        impl_report.yaml
        ...
        .claude/
          agents/       — materialized agent files for this run
      event.log.jsonl   — ##EVENT## structured events (one JSON object per line)
      grading.json      — GradingRecord: deterministic + rubric results + overall_pass
      lineage.json      — RunLineage: full provenance of this run
      run.log.json      — per-run structured log from the runner
    cycle_report.html   — optional rendered HTML report
```

Each file is written atomically after the step that produces it. If a run fails
mid-way, earlier files may be present and later ones absent.

---

## 5. Baseline management

Baselines are stored in `baselines/<task_id>.json` as `BaselineBand` objects:

```json
{
  "task_id": "simple-readme-update",
  "task_version": 1,
  "low": 0.35,
  "high": 0.65,
  "mean": 0.50,
  "sample_size": 10,
  "established_at": "2026-04-16T18:00:00Z",
  "judge_model": "gpt-5.4-high",
  "reason": "initial_calibration"
}
```

The band is `mean ± 0.15`, clamped to `[0, 1]`. When `sample_size < 2`,
`low == high == mean`. A regression is detected when the measured pass rate in
a new cycle falls below `low`. An improvement is detected when it exceeds `high`.

---

## 6. Dev mode vs. authoritative mode

| Aspect | Dev mode (`--dev-mode`) | Authoritative (`--no-dev-mode`) |
|---|---|---|
| Container | None | Ephemeral Docker container per run |
| Runner invocation | Subprocess in current env | Container entrypoint |
| Substrate extraction | Skipped (uses current repo) | Pinned commit extracted fresh |
| Baseline updates | Not counted | Counted |
| Lineage `mode` field | `"dev"` | `"authoritative"` |
| Speed | Fast | Slower (container spin-up) |

Dev mode runs are flagged in their lineage and excluded from baseline updates.
They are suitable for inner-loop iteration and smoke-testing task structure.

---

## 7. Python API

```python
from pathlib import Path
from agent_runner_harness.corpus import load_task, load_all
from agent_runner_harness.scheduler import RunOpts, run_cycle
from agent_runner_harness.baseline.manager import compute_band, save_band

task = load_task(Path("task-corpus/simple-readme-update"))
opts = RunOpts(k_runs=5, dev_mode=True, judge_stub=True)
result = run_cycle([task], opts)

band = compute_band(
    [r["overall_pass"] for r in result.run_results],
    judge_model="stub",
    task_id=task.id,
    task_version=task.version,
)
save_band(band, Path("baselines"))
```
