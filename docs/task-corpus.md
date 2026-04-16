# Task Corpus — Dual-Format Convention

This document describes the task corpus format, the dual-format acceptance-criteria
philosophy, how to author and calibrate tasks, and the pitfalls to avoid.

For the broader "why" behind the dual-format approach, see
[01-broad-architecture.md](01-broad-architecture.md) §13 (testing philosophy) and
§11 (control flow). The short version: deterministic checks catch structural regressions
cheaply and reproducibly; the rubric judge catches semantic quality where exact equality
is too fragile. Neither alone is sufficient.

---

## 1. On-disk layout

The corpus lives at `task-corpus/` in the repo root. One subdirectory per task, named
after the task ID (lowercase-hyphenated, matching `task.yaml`'s `id` field):

```
task-corpus/
  <task-id>/
    task.yaml                       — required: task definition
    inputs/                         — optional: input files referenced by task
      <whatever>.json
    criteria/
      deterministic/                — optional: scripts for deterministic ACs
        check_<something>.py
```

The harness discovers tasks by scanning for subdirectories that contain a `task.yaml`.
No other top-level files in `task-corpus/` are treated as task entries.

---

## 2. The dual-format philosophy

Acceptance criteria are split into two orthogonal layers:

| Layer | Mechanism | Cost | Reproducible? | Good for |
|---|---|---|---|---|
| Deterministic | Python script / file-check / event-check | Near-zero | Yes | Structure, presence, schema, events |
| Rubric | LLM judge scored on a scale | One judge call per criterion per run | Stochastic within ± variance | Semantic quality, faithfulness, coherence |

**Deterministic criteria come first.** A run may be marked as an overall fail before
the judge is even called, saving cost. Conversely, a run that passes all structural
checks but fails semantic quality is distinguishable from one that fails at a structural
level.

**At least one of the two layers is required.** A task may have deterministic-only,
rubric-only, or both. Deterministic-only tasks are cheapest and most stable. Tasks
with only rubric criteria depend entirely on the judge, which should be a deliberate
choice.

---

## 3. Full annotated `task.yaml` example

The following is the `ado-normalize-embedded-ac` seed task, annotated inline:

```yaml
# Unique task identifier. Must match the directory name.
# Pattern: ^[a-z0-9][a-z0-9_-]*$
id: ado-normalize-embedded-ac

# Schema version. Increment when AC definitions change substantively.
# Bump version + re-calibrate together (see §6).
version: 1

# Human-readable display name.
title: "Extract and normalize acceptance criteria from embedded HTML ADO payload"

# Relative difficulty — used for corpus-level statistics only (not grading logic).
# One of: easy | medium | hard
difficulty: medium

# Free-form tags for filtering and grouping (optional).
tags:
  - ado
  - acceptance-criteria
  - normalization

# Which substrate (codebase snapshot) to run against.
# The ref must exist in substrates/substrates.yaml.
substrate:
  ref: baseline-2026-04-16

# Which workflow definition to use.
workflow:
  id: standard    # must match a file in packages/runner/agent_runner/workflows/
  version: 1

# Agent refs required. Each must exist in agent-sources/ at the given version.
# Pattern: name@version
agents:
  - intake@v1

# Key-value inputs injected into the run environment.
# The runner makes these available to the workflow / agents.
inputs:
  payload_file: "inputs/ado_payload.json"

# Model overrides. "worker" is used by the orchestrator; "judge" by the harness grader.
# Omit "judge" if there are no rubric criteria.
models:
  worker: claude-sonnet-4-5
  judge: gpt-5.4-high

acceptance_criteria:
  # ── Deterministic ──────────────────────────────────────────────────────────
  # These run first, without LLM calls. All must pass for the run to pass
  # the deterministic layer (individual failures are recorded and reported).
  deterministic:
    - id: story-yaml-exists          # Unique within this task.
      description: "story.yaml artifact must be present in the output"
      kind: file_exists              # Checks that a file was produced.
      path: story.yaml              # Relative to the run's artifact directory.

    - id: ac-count-check
      description: "At least 2 acceptance criteria must be extracted"
      kind: script                   # Runs an arbitrary Python script.
      script: criteria/deterministic/check_ac_count.py
      # Script path is relative to the task directory (task-corpus/<id>/).
      # The script receives the artifact directory as sys.argv[1] and must
      # exit 0 for pass, non-zero for fail.

    - id: eval-pass-event
      description: "Runner must emit an eval.pass event at the qa stage"
      kind: event_assertion          # Checks the event.log.jsonl for a matching event.
      event:
        kind: eval.pass              # Must match the "kind" field in the event record.
        stage: qa                    # Must match the "stage" field.

  # ── Rubric ─────────────────────────────────────────────────────────────────
  # These are scored by the judge LLM. Each criterion is independent.
  # A criterion passes when score >= threshold.
  rubric:
    - id: r-ac-faithfulness
      description: "Extracted ACs faithfully represent the original ADO description"
      kind: rubric                   # Must be "rubric" for schema validation.
      scale: "0-3"                   # Integer scale the judge scores on.
      threshold: 2                   # Minimum score to count as a pass.
      judge_prompt: |
        Evaluate whether the extracted acceptance criteria in story.yaml faithfully
        represent the intent and content of the original ADO work item description.

        Score 0-3:
        0 = Criteria are missing or completely wrong
        1 = Some criteria captured but major omissions or distortions
        2 = Most criteria captured with minor issues acceptable
        3 = All criteria accurately and completely captured

# ── Calibration record ───────────────────────────────────────────────────────
# Filled in after calibration (see docs/calibration.md). The harness writes
# the measured_pass_rate and band fields; you set model and target_pass_rate.
calibration:
  model: claude-sonnet-4-5
  runs: 5
  target_pass_rate: 0.8          # Aim for 0.3–0.7; see §6 for guidance.
```

---

## 4. Deterministic criterion kinds

### 4.1 `file_exists`

Checks that a named file is present in the run's artifact directory after the
workflow completes.

```yaml
- id: impl-report-present
  description: "impl_report.yaml produced by the software_engineer stage"
  kind: file_exists
  path: impl_report.yaml        # relative to artifact_dir
```

**Signal:** The relevant stage completed and wrote its primary output. Catching
this early is cheap insurance against crashes or skipped stages.

---

### 4.2 `script`

Runs a Python script that receives the artifact directory as `sys.argv[1]`. Exit
code 0 = pass, anything else = fail.

```yaml
- id: ac-count-check
  description: "At least 2 ACs present in story.yaml"
  kind: script
  script: criteria/deterministic/check_ac_count.py   # relative to task dir
```

Example script (`criteria/deterministic/check_ac_count.py`):

```python
import sys, yaml
from pathlib import Path

artifact_dir = Path(sys.argv[1])
story = yaml.safe_load((artifact_dir / "story.yaml").read_text())
acs = story.get("acceptance_criteria", [])
if len(acs) < 2:
    print(f"FAIL: only {len(acs)} AC(s) found, need at least 2")
    sys.exit(1)
print(f"PASS: {len(acs)} AC(s) found")
```

**Signal:** Structural invariants that are too specific to express as file-check or
event-check but don't require an LLM. Examples: field count, value ranges, format
compliance, referential integrity between artifacts.

---

### 4.3 `event_assertion`

Checks the run's `event.log.jsonl` for at least one event matching all specified
fields.

```yaml
- id: eval-pass-at-qa
  description: "Evaluator approved the QA stage"
  kind: event_assertion
  event:
    kind: eval.pass
    stage: qa
```

**Signal:** Workflow control-flow paths. Particularly useful for verifying that
evaluator loops converged (`eval.pass`) rather than being bypassed or timed out.

---

### 4.4 `schema_valid`

Validates a produced artifact against a JSON Schema file.

```yaml
- id: story-schema-valid
  description: "story.yaml conforms to the story schema"
  kind: schema_valid
  path: story.yaml               # artifact to validate
  schema: story.schema.json      # schema relative to shared/schemas/
```

**Signal:** Output structure correctness. Use when the schema is already
defined in `packages/shared/agent_runner_shared/schemas/`. Catches type errors
and missing required fields that scripts would have to replicate manually.

---

## 5. Rubric criterion authoring

### 5.1 Scale choice

| Scale | Use when |
|---|---|
| `0-3` | Four-point scale; sweet spot for most quality dimensions. 0=broken, 1=poor, 2=acceptable, 3=excellent. |
| `0-4` | Five-point scale; add one more graduation when the 0-3 middle is too coarse (e.g., a long document where "partially good" has two meaningful levels). |

Avoid `0-1` (too binary — just use a deterministic check) and `0-10` (judge
calibration variance is too high at fine granularity).

### 5.2 Threshold guidance

Set the threshold at the **lowest score you consider acceptable in production**.
For a `0-3` scale:
- `threshold: 2` — the common case ("acceptable or better")
- `threshold: 3` — only for criteria where anything less is a regression

For `0-4`:
- `threshold: 3` is typically the right default.

Do not set `threshold: 1` on a `0-3` scale — it makes the criterion near-
trivially passable.

### 5.3 Writing the `judge_prompt`

The judge prompt is rendered verbatim and sent to the pinned judge model. Every
`judge_prompt` must include:

1. **What to evaluate** — which artifact(s) to look at and what property to score.
2. **The scale definition** — every integer on the scale, with a one-line anchor.
3. **No ambiguous references** — do not reference "the previous message" or
   external context the judge won't have.

**Good anchor example (0-3 scale):**

```
Score 0-3:
0 = Output is absent or completely wrong (e.g., empty file, wrong format)
1 = Partial: key elements are present but major portions are wrong or missing
2 = Mostly correct: all key elements present; minor errors acceptable
3 = Fully correct: no material issues
```

**Poor anchor (too vague):**

```
Score from 0 to 3 based on overall quality.
```

### 5.4 When to use rubric criteria

Use rubric criteria when:
- The property involves subjective quality (faithfulness, clarity, completeness
  of reasoning).
- Deterministic checks would require replicating significant business logic
  in a script.
- The cost of a false negative (missing a regression) outweighs the judge cost.

Do not use rubric criteria for things that can be checked structurally. A
`file_exists` check is more reliable than asking a judge "is the file present?"

---

## 6. Calibration

Calibration measures the observed pass rate of a task under the target worker
model and determines whether the task is well-discriminating. See
[docs/calibration.md](calibration.md) for the full step-by-step procedure.

### 6.1 Target pass rate

The goal is approximately **0.5** — equal-hardness discrimination. In practice,
the acceptable band is **0.3–0.7**. A task outside this range is either too easy
(pass rate > 0.8, meaning it adds noise without discriminative power) or too hard
(pass rate < 0.2, meaning it will almost always fail and won't detect regressions).

| Observed pass rate | Interpretation | Action |
|---|---|---|
| < 0.2 | Too hard | Trim ACs, lower rubric thresholds, or simplify inputs |
| 0.2–0.3 | Borderline hard | Consider slight relaxation |
| 0.3–0.7 | Good band | Ship it |
| 0.7–0.8 | Borderline easy | Consider slight tightening |
| > 0.8 | Too easy | Add harder ACs or raise rubric thresholds |

### 6.2 Minimum runs for a valid band

Run at least **5 runs** before declaring a band. With fewer runs, the observed
pass rate has very high variance (e.g., 2/3 passes and 1/3 passes both look
"acceptable" but have different true rates).

For tasks that will be used in large-scale regression comparisons, use **10 runs**
for a stable baseline.

### 6.3 When to bump `version`

Increment `version` in `task.yaml` and re-calibrate whenever:
- An acceptance criterion is added, removed, or has its threshold changed.
- The `inputs` or `substrate.ref` changes.
- The worker model (`models.worker`) changes.

Do **not** increment version for changes to `description` or `title` that don't
affect grading.

---

## 7. Common pitfalls

### 7.1 Rubric threshold too lenient

Setting `threshold: 1` on a `0-3` scale means almost every run passes the
rubric, eliminating its discriminative value. The rubric becomes noise. Default
to `threshold: 2` unless there's a specific reason.

### 7.2 Deterministic scripts that read wall-clock time

Scripts in `criteria/deterministic/` must be hermetic. Do not use
`datetime.now()`, `time.time()`, or file modification timestamps. These make
the criterion non-reproducible under cassette replay. Read only from the
`sys.argv[1]` artifact directory.

### 7.3 Non-hermetic file paths

Do not hardcode absolute paths in criterion scripts. Everything should be
relative to `sys.argv[1]` (the artifact directory). Similarly, do not open
files outside the artifact directory — the harness does not guarantee any other
paths are present during grading.

### 7.4 Bimodal pass rates

If calibration shows a bimodal distribution (many all-pass and many all-fail
runs with nothing in between), the task is likely testing a binary capability
(the model either does X or it doesn't). This is valid, but the task will have
limited value for detecting _degree_ of regression. Consider adding a rubric
criterion that grades quality conditional on the task being attempted.

### 7.5 Judge-only regressions

A pass-rate drop can come from the judge alone (if the judge model is updated or
its prompts change) rather than the worker model. When a regression is detected,
check the grading records to see if deterministic criteria are failing or only
rubric criteria. If only rubric criteria regress, suspect a judge-side change and
consult the rebaseline procedure in [docs/calibration.md](calibration.md).
