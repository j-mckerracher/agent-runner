# Evidence Artifact Contracts

> Prompt 21 — additive layer on top of the planning/report payload contracts
> from Prompts 18–20. See [artifact-payload-contracts.md](artifact-payload-contracts.md)
> for the base contracts.

This document describes the three **evidence artifact adapters** and the
**centralized evidence-path contract** introduced in Prompt 21.

---

## Overview

Evidence artifacts are produced at the end of a benchmark trial (by
`eval/runner.py`) and consumed by reporting, comparison, and analysis tools.
Before Prompt 21 these files had no typed loader or `ArtifactRef` adapter;
path construction was scattered across `eval/runner.py` and
`eval/live_report.py`.

Prompt 21 adds:

| Artifact | Class | Module |
|---|---|---|
| `final.diff` text | `FinalDiffArtifact` | `artifacts/evidence.py` |
| v0.2 eval report JSON | `EvalReportArtifact` | `eval/report_artifact.py` |
| `trace.jsonl` event log | `TraceArtifact` | `telemetry/trace_artifact.py` |

Plus:

- `eval/evidence_paths.py` — centralized path-construction helpers
- `eval/evidence_artifacts.py` — registry + aggregation re-export

---

## Import Boundary Rules

The `artifacts` package is a **stdlib-only leaf**: it imports nothing from
`core`, `workflow`, `runners`, `eval`, `server`, `telemetry`, `opik`, PyYAML,
or any vendor SDK.

| Module | May import | Must NOT import |
|---|---|---|
| `artifacts/evidence.py` | `artifacts.models`, `artifacts.validation` | `eval`, `telemetry`, any vendor |
| `eval/report_artifact.py` | `artifacts.*`, `eval.report_schema` | `telemetry`, any vendor |
| `telemetry/trace_artifact.py` | `artifacts.*`, `telemetry.events` | `eval`, any vendor |
| `eval/evidence_artifacts.py` | all three adapters | (no new constraints) |

Verified by `tests/test_artifact_payloads_isolation.py`.

---

## FinalDiffArtifact

**Module**: `artifacts/evidence.py`
**Public import**: `from artifacts import FinalDiffArtifact`

```python
from artifacts import FinalDiffArtifact

art = FinalDiffArtifact.load("evidence/final.diff")
print(art.text)          # unified-diff text, line endings preserved
print(art.char_length)   # Unicode code points
print(art.byte_length)   # UTF-8 bytes
print(art.is_empty)      # True for empty diff

ref = art.to_artifact_ref(path="evidence/final.diff")
```

### Notes

- Empty file is **valid** and distinct from a missing file.
- Bytes are decoded explicitly (`raw.decode("utf-8")`) — no line-ending
  normalization.
- Missing file or non-UTF-8 bytes → `ArtifactLoadError`.
- `to_artifact_ref` never computes a checksum; pass it via `checksum_sha256=`
  if available.

---

## EvalReportArtifact

**Module**: `eval/report_artifact.py`
**Public import**: `from eval.report_artifact import EvalReportArtifact`

```python
from eval.report_artifact import EvalReportArtifact

art = EvalReportArtifact.load_with_validation("reports/latest.json")
print(art.report.eval_run_id)
print(art.report.benchmark_suite_id)

ref = art.to_artifact_ref(path="reports/latest.json")
```

### Validation

1. File must exist and contain valid JSON → `ArtifactLoadError` otherwise.
2. `report_schema_version` must equal `"0.2"` → `ArtifactValidationError` if not.
3. Structural validation via `eval.report_schema.validate_report_payload` →
   `ArtifactValidationError` if it fails.

**No duplicate schema**: validation delegates to `eval.report_schema`; this
adapter adds only the schema-version gate.

---

## TraceArtifact

**Module**: `telemetry/trace_artifact.py`
**Public import**: `from telemetry.trace_artifact import TraceArtifact`

```python
from telemetry.trace_artifact import TraceArtifact

art = TraceArtifact.load_with_validation("evidence/trace.jsonl")
print(art.event_count)
print(art.run_id)

# Round-trip to JSONL (byte-identical to JsonlEventSink output)
print(art.to_jsonl())

ref = art.to_artifact_ref(path="evidence/trace.jsonl")
```

### Validation

- Missing file → `ArtifactLoadError`.
- Malformed JSON on any line → `ArtifactValidationError` naming the 1-based line.
- Invalid `TraceEvent` on any line → `ArtifactValidationError` naming the line.
- Mixed `run_id` values → `ArtifactValidationError`.
- Empty file → valid empty trace (`event_count=0`, `run_id=None`).

### Round-trip guarantee

`to_jsonl()` produces output byte-identical to what `JsonlEventSink` writes:
one sorted-keys JSON line per event followed by `\n`. Empty trace → `""`.

---

## Centralized Evidence Paths (`eval/evidence_paths.py`)

Single source of truth for:

- `_sanitize_path_segment(raw)` — safe filesystem segment with 8-char SHA-256
  collision suffix (byte-identical to the original runner.py implementation).
- Filename constants: `STORY_JSON`, `WORKFLOW_STDOUT_LOG`, `WORKFLOW_STDERR_LOG`,
  `WORKFLOW_RESULT_JSON`, `HIDDEN_TESTS_XML`, `FINAL_DIFF`, `TRACE_JSONL`.
- `TrialEvidencePaths` — pure computed layout for one trial.
- Report-path helpers: `report_stamp`, `timestamped_report_path`,
  `latest_report_path`.

```python
from eval.evidence_paths import TrialEvidencePaths

paths = TrialEvidencePaths.for_trial(reports_dir, eval_run_id, benchmark_id, trial_id)
paths.ensure_dir()  # explicit mkdir; for_trial() itself is pure

print(paths.story_json)          # reports_dir/evidence/san(eval)/san(bench)/san(trial)/story.json
print(paths.workflow_stdout_log) # .../workflow.stdout.log
print(paths.final_diff)          # .../final.diff
print(paths.trace_jsonl)         # .../trace.jsonl
```

### Adoption in runner / live_report

- `eval/runner.py`: `_evidence_dir()` delegates to
  `TrialEvidencePaths.for_trial(...).ensure_dir()`.  All seven literal filename
  strings replaced with constants from `evidence_paths`.
- `eval/live_report.py`: `write_eval_report()` uses `report_stamp`,
  `timestamped_report_path`, `latest_report_path`.

**Zero externally observable behavior change** — same dir layout, same
filenames, same JSON, same CLI, same warnings.

---

## Registry (`eval/evidence_artifacts.py`)

```python
from eval.evidence_artifacts import EVIDENCE_ARTIFACTS
# (EvalReportArtifact, TraceArtifact, FinalDiffArtifact)

# Also re-exports individual names:
from eval.evidence_artifacts import EvalReportArtifact, TraceArtifact, FinalDiffArtifact
```

`EVIDENCE_ARTIFACTS` does not overlap with `PLANNING_ARTIFACTS` or
`REPORT_ARTIFACTS` (from `artifacts.payloads`).

---

## Out of Scope (deferred)

- Workflow-stage adoption of payload classes.
- Stage `consume`/`produce` declarations.
- Artifact lifecycle events.
- `artifact.missing` trace enum value.
- `EvalReport` / `TraceEvent` / `JsonlEventSink` redesign.
- Git patch parsing.
- Checksum computation inside adapters.
- Runner rewrite.
