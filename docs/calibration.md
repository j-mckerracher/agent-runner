# Calibration Workflow for Migrated Tasks

This document describes the step-by-step procedure for calibrating a newly
migrated task. Calibration establishes whether a task's acceptance criteria
discriminate between passing and failing runs at the desired difficulty band,
and records a baseline pass-rate band for regression detection.

For background on the dual-format acceptance criteria philosophy and the
meaning of pass rates, see [docs/task-corpus.md](task-corpus.md) §6.
For the harness subcommands referenced below, see [docs/harness.md](harness.md).

---

## 1. Author initial `task.yaml` via `tools/ac_migrator`

If starting from an ADO work item, use the `ac_migrator` tool to extract and
convert acceptance criteria from the ADO payload into the dual-format `task.yaml`:

```bash
# Run interactively from the repo root
python tools/ac_migrator/main.py \
  --payload <path-to-ado-payload.json> \
  --task-id <new-task-id>
```

The tool:
1. Parses the ADO work item's description (which may contain embedded HTML or
   natural-language acceptance criteria).
2. Proposes a split into deterministic and rubric criteria.
3. Scaffolds `task-corpus/<task-id>/task.yaml` with the proposed split.
4. Creates placeholder criterion scripts in `criteria/deterministic/`.

Review the generated `task.yaml` carefully:
- Ensure every criterion has a unique `id`.
- Make sure deterministic criteria are actually deterministic (no LLM required).
- Verify rubric prompts are self-contained (see [task-corpus.md](task-corpus.md) §5.3).
- Set an initial `calibration.target_pass_rate` (start with `0.5`).

---

## 2. Dry-run validation

Before spending compute on real runs, verify the task structure with a single
stub run:

```bash
PYTHONPATH=packages/shared:packages/runner:packages/registry:packages/harness \
  python -m agent_runner_harness.cli.main evaluate \
    --task <task-id> \
    --dev-mode \
    --judge-stub \
    --k 1
```

Flags:
- `--judge-stub`: uses a deterministic pass-all stub for rubric criteria (no LLM calls).
- `--dev-mode`: skips container spin-up (fast, non-authoritative).
- `--k 1`: single run for smoke-testing.

**Check for:**
- No `FileNotFoundError` in the output (criterion scripts exist and are reachable).
- No `jsonschema.ValidationError` (task.yaml is schema-valid).
- At least one grading record written to `runs/<cycle_id>/<run_id>/grading.json`.
- The grading record shows which criteria were evaluated and why each passed/failed.

If the run errors before grading, fix the structural issue before proceeding.

---

## 3. Trial calibration (5 runs)

Run 5 iterations to get an initial read on the pass rate:

```bash
PYTHONPATH=packages/shared:packages/runner:packages/registry:packages/harness \
  python -m agent_runner_harness.cli.main calibrate \
    --task <task-id> \
    --k 5 \
    --baselines-dir baselines
```

The command prints a summary:

```
Band saved to baselines/<task-id>.json: low=0.350 mean=0.500 high=0.650
```

Read the band and interpret:

| Observed mean pass rate | Interpretation |
|---|---|
| < 0.2 | Task is too hard; most runs fail |
| 0.2–0.3 | Borderline hard; consider relaxing |
| **0.3–0.7** | **Target band — good discrimination** |
| 0.7–0.8 | Borderline easy; consider tightening |
| > 0.8 | Task is too easy; add harder ACs |

With only 5 runs the estimate is noisy (±~22% at 95% CI for a true 50% rate).
Use this step for directional feedback only.

---

## 4. Adjust if out of band

### Task too easy (mean > 0.8)

- Add additional deterministic criteria that check for more specific structural
  properties.
- Raise rubric thresholds (e.g. from `threshold: 2` to `threshold: 3` on a 0-3 scale).
- Add a rubric criterion for a quality dimension that was previously unscored.
- Make the input harder (e.g. a more ambiguous ADO description).

After making changes, **bump `version`** in `task.yaml`:

```yaml
version: 2   # was 1
```

Bumping version ensures that future baseline comparisons are against the correct
AC set and alerts the system that old baselines are invalidated.

### Task too hard (mean < 0.2)

- Remove or relax the most-failing deterministic criteria (check grading records
  to identify which criterion is failing most often).
- Lower rubric thresholds (e.g. from `threshold: 3` to `threshold: 2`).
- Simplify inputs (e.g. less ambiguous description, fewer required outputs).

Bump `version` after any AC change.

Repeat steps 2–4 until the trial band is in range.

---

## 5. Full calibration (10 runs)

Once the trial band is acceptable, run a full calibration for a stable baseline:

```bash
PYTHONPATH=packages/shared:packages/runner:packages/registry:packages/harness \
  python -m agent_runner_harness.cli.main calibrate \
    --task <task-id> \
    --k 10 \
    --baselines-dir baselines
```

This overwrites `baselines/<task-id>.json` with a band computed from 10 runs.
A 10-run band has approximately ±15% confidence interval, which is the system's
built-in band width (`mean ± 0.15`).

For tasks with high rubric variance (large spread in judge scores across runs),
consider 15 or 20 runs to stabilize the mean estimate before committing the band.

---

## 6. Commit together

Commit the task definition and its baseline band atomically so the corpus and
the baselines directory are always in sync:

```bash
git add task-corpus/<task-id>/
git add baselines/<task-id>.json
git commit -m "feat(corpus): add <task-id> task with calibrated baseline"
```

Do **not** commit a baseline for a task that has not completed full calibration.
A missing baseline is preferable to a misleading one.

---

## 7. Troubleshooting

### 7.1 Bimodal pass rates

**Symptom:** 5-run trial shows either 0/5 or 5/5 passes; no middle ground.

**Cause:** The task tests a binary capability. The model either performs the
whole task or fails completely. This is valid but limits regression detection
(you'll see a phase transition, not a gradual drift).

**Action:**
- Add a rubric criterion that grades quality conditional on the task being
  attempted at all. This allows partial-credit detection.
- Accept the bimodal distribution and track the flip point across versions.
- Split the task into an easier and a harder variant.

### 7.2 High rubric judge variance

**Symptom:** Re-running calibration on the same code produces very different
mean pass rates (e.g. 0.3 one time, 0.7 another time).

**Causes and remedies:**

| Cause | Remedy |
|---|---|
| `judge_prompt` is ambiguous or underspecified | Add concrete anchor examples to all score levels |
| Judge scale is too fine-grained | Switch from 0-4 to 0-3 |
| Threshold is at the midpoint of a coarse scale | Move threshold up or down by 1 |
| Judge model is non-deterministic at temperature > 0 | Pin judge to `gpt-5.4-high` which uses `temperature=0` |
| Criterion is measuring something the artifacts don't contain | Remove or rewrite the criterion |

If variance persists after refining the prompt, consider converting the rubric
criterion to a deterministic one (even if the check is less expressive).

### 7.3 Judge-only regressions

**Symptom:** Pass rate drops in a new evaluation cycle but the worker model
and prompts are unchanged. Grading records show deterministic criteria still
passing; only rubric criteria are regressing.

**Cause:** The judge model has been updated by the provider (silent model drift),
or the judge's behavior has changed due to a system prompt update.

**Action:**
1. Compare the `judge_model` field in `lineage.json` across old and new runs to
   confirm the version is the same string.
2. Re-grade the archived runs from the previous cycle with the current judge to
   check if the judge scores have changed.
3. If the judge has drifted, treat this as a **rebaseline event**: re-grade all
   archived runs, update all baseline bands, and record the judge change as the
   reason.
4. If the judge string is unchanged but behavior has changed, file a bug and
   freeze the judge at a pinned API version if the provider supports it.

### 7.4 Criterion script fails on first run only

**Symptom:** First run of calibration fails; subsequent runs pass. Or vice versa.

**Cause:** The criterion script is reading from a location that varies between
runs (e.g. a relative path that resolves differently depending on `cwd`).

**Action:** Ensure criterion scripts use `sys.argv[1]` as the artifact directory
and construct all paths relative to it. See [task-corpus.md](task-corpus.md) §7.3.

### 7.5 Calibration produces a band of [0.0, 0.0]

**Symptom:** All K runs fail; band is `low=0.0 mean=0.0 high=0.0`.

**Cause:** Usually a dry-run/dev-mode issue where the runner doesn't produce
real artifacts, and the deterministic `file_exists` criteria all fail.

**Action:**
1. Inspect `runs/<cycle_id>/<run_id>/artifacts/` to see what was actually produced.
2. If empty, the runner's `--dry-run` synthesis is not producing the expected files.
3. Either fix the dry-run synthesis for this workflow, or run a real (non-dry-run)
   dev-mode evaluation with an actual Claude Code invocation.
