# Planning-artifact payload contracts (v0.4)

Prompt 19 adds typed, immutable, read-only **payload** contracts for the four
planning-stage artifacts, layered on top of the Prompt 18
[`ArtifactRef`](./artifact-ref.md) *reference* contract.

Where `ArtifactRef` answers *"what/where is this artifact"* (type, path, producer,
schema identity), these contracts answer *"what is inside it"* — a validated,
typed view of the payload, plus the metadata needed to mint a matching
`ArtifactRef`.

## Public imports

```python
from artifacts import (
    StoryArtifact, TaskPlanArtifact, AssignmentArtifact, UowSpecArtifact,
    PLANNING_ARTIFACTS,            # ordered tuple of the four contract classes
    ValidationResult, ValidationIssue, ValidationSeverity,
    ArtifactValidationError,      # raised on structural (ERROR) problems
    ArtifactLoadError,            # raised on transport problems (I/O, parse, missing PyYAML)
)
```

## Contract schema version vs payload schema version

Three independent version axes coexist:

- `ArtifactRef.artifact_ref_schema_version` — shape of the *reference* record (P18).
- `<Artifact>.ARTIFACT_SCHEMA_VERSION` — shape of the *payload* modeled here (all
  currently `"1"`).
- `<Artifact>.ARTIFACT_SCHEMA` — the payload's stable, namespaced identity
  (e.g. `agent-workbench.story`).

The catalog **assigns** each payload its schema identity; it does not claim that
the legacy on-disk files embed that identity. Payload migration and cross-version
validation are deferred (see below).

## The four contracts

| artifact type | schema / version | producer | consumers | path scope | canonical relative path | params | format |
|---|---|---|---|---|---|---|---|
| `story` | `agent-workbench.story` / `1` | `intake` | `task-generation`, `task-assignment`, `qa`, `pr-review` | `agent_context` | `{change_id}/intake/story.yaml` | `change_id` | yaml |
| `task_plan` | `agent-workbench.task-plan` / `1` | `task-generation` | `task-assignment`, `qa`, `pr-review` | `agent_context` | `{change_id}/planning/tasks.yaml` | `change_id` | yaml |
| `assignment` | `agent-workbench.assignment` / `1` | `task-assignment` | `execution`, `qa`, `pr-review` | `agent_context` | `{change_id}/planning/assignments.json` | `change_id` | json |
| `uow_spec` | `agent-workbench.uow-spec` / `1` | `task-assignment` | `execution` | `agent_context` | `{change_id}/execution/{uow_id}/uow_spec.yaml` | `change_id`, `uow_id` | yaml |

Metadata is exposed as `ClassVar` class attributes: `ARTIFACT_TYPE`,
`ARTIFACT_SCHEMA`, `ARTIFACT_SCHEMA_VERSION`, `PRODUCER_STAGE`,
`CONSUMER_STAGES`, `PATH_SCOPE`, `RELATIVE_PATH_TEMPLATE`, `PATH_PARAMETERS`,
`CONTENT_FORMAT`.

### Path scopes are logical and unresolved

`PATH_SCOPE` (`agent_context`) and `RELATIVE_PATH_TEMPLATE` are declarative. The
contracts never resolve a template to a machine path, never read `core.runtime_paths`,
and never touch the filesystem during construction. Resolution stays the caller's
responsibility.

### Core-field projections (not lossless mirrors)

These models capture the fields the workflow depends on. Unmodeled top-level
keys (`critical_path`, `estimated_total_batches`, `parallelization_opportunities`,
`ac_coverage_matrix`, `notes`, `metacognitive_context`, …) are ignored, not
retained. Consequently `to_dict()` is a round trip of the **typed model**, not of
the source document:

```python
obj = StoryArtifact.from_mapping(data)
StoryArtifact.from_mapping(obj.to_dict()) == obj   # True (typed projection)
obj.to_dict() == data                              # not guaranteed
```

## Validation model

`validate_payload(data)` never raises; it returns a `ValidationResult`:

- `ValidationIssue(code, message, severity, location)` — one structured problem.
- `ValidationResult.errors` / `.warnings` / `.ok` — `ok` is true when there are no
  ERROR issues (warning-only payloads are `ok`).
- Every `code` is a stable, machine-readable constant from
  `artifacts.validation` (`ERROR_CODES` / `WARNING_CODES`).

Error codes: `missing_field`, `wrong_type`, `empty_collection`, `not_a_mapping`, `invalid_value`
(e.g. a `TaskEntry.priority`/`.complexity` value outside the enums below).

`ValidationResult.issues` defensively coerces any iterable given at construction into a concrete
tuple, so a caller-owned `list` passed in cannot be mutated afterward to change a stored result.

Task-level enum constraints (mirrors `agent-script-source/validate-artifact-schema.py`):
`TaskEntry.priority` ∈ `{high, medium, low}`; `TaskEntry.complexity` ∈ `{simple, moderate,
complex}`. Values outside these sets raise `invalid_value`. Story acceptance-criteria keys and
values must already be non-empty strings — they are validated, never coerced via `str(...)`.

`ArtifactValidationError` is raised only for ERROR issues; it carries the full
`result` and its `.errors` tuple, and renders its message as `code: message; …`
so callers see every problem at once.

## Compatibility warnings

Accepted legacy shapes are normalized **in memory** (on a defensive deep copy)
and reported as WARNING issues. Loading still succeeds. Warning codes:

| code | accepted legacy shape → normalized to |
|---|---|
| `legacy_ac_list` | story `acceptance_criteria` as a list → auto-numbered `AC1..N` |
| `legacy_execution_schedule` | assignment `execution_schedule` → `batches` |
| `legacy_batch_key` | batch `batch` → `batch_id` |
| `legacy_task_id` | task `task_id` → `id` |
| `legacy_acceptance_criteria_mapped` | task `acceptance_criteria_mapped` → `ac_mapping` |
| `legacy_estimated_complexity` | task `estimated_complexity` → `complexity` |
| `legacy_partial_uow_spec` | uow_spec missing canonical fields (only `uow_id` required) |

Warnings are observable through the `*_with_validation` API (see below); the
convenience methods discard them. Structured `ValidationResult` warnings are the
only warning channel — the contracts never emit Python `warnings` or log messages.

## Read-only loading

Two API pairs — a warning-preserving form and a convenience form that raises on
errors and discards warnings:

```python
story, result = StoryArtifact.load_with_validation("…/C1/intake/story.yaml")
for w in result.warnings:
    print(w.code, w.message)          # e.g. legacy_ac_list

assignment = AssignmentArtifact.load("…/C1/planning/assignments.json")  # convenience

# Validate an already-parsed mapping without touching the filesystem:
story, result = StoryArtifact.from_mapping_with_validation(parsed_dict)
```

Loading reads the file with `Path.read_text(...)` and never opens it for writing.
JSON is parsed with the standard-library `json` module. **YAML is imported
lazily**, at call time, inside the loader — so `import artifacts` never imports
PyYAML. A missing PyYAML, a missing file, or malformed YAML/JSON raises
`ArtifactLoadError` (message includes the source path); a readable-but-invalid
payload raises `ArtifactValidationError` instead.

## `ArtifactRef` factory

```python
story = StoryArtifact.from_mapping(parsed_dict)

ref = story.to_artifact_ref(path="C1/intake/story.yaml")
ref.artifact_type            # "story"
ref.producer_stage           # "intake"
ref.consumer_stages          # ("task-generation", "task-assignment", "qa", "pr-review")
ref.artifact_schema          # "agent-workbench.story"
ref.artifact_schema_version  # "1"

# Optional caller-supplied fields:
story.to_artifact_ref(
    uri="s3://bucket/story.yaml",
    validation_status=ArtifactValidationStatus.VALID,
    checksum_sha256="…64 hex…",
    metadata={"run": "abc"},
)
```

The factory does not resolve the path template, check that a file exists, compute
a checksum, or read the payload. The caller must supply `path` and/or `uri`
(`ArtifactRef` enforces this). `metadata` is defensively copied by `ArtifactRef`.

## Import-isolation guarantee

`artifacts` remains a stdlib-only leaf. Importing `artifacts`,
`artifacts.payloads`, or `artifacts.validation` pulls in **none** of `core`,
`workflow`, `runners`, `eval`, `server`, `telemetry`, `opik`, the Anthropic /
OpenAI / Google SDKs, or **PyYAML**. Construction, validation, serialization, and
`to_artifact_ref` perform no filesystem, subprocess, network, or environment
access. (`load*()` reads a file by design and is excluded from that no-side-effect
guarantee.) Enforced by `tests/test_artifact_payloads_isolation.py`.

## Legacy compatibility & non-migration boundary

- Current on-disk filenames and directory layout are unchanged.
- No production producer or consumer adopts these contracts in this prompt.
- Existing writers, readers, path conventions, and `core`/`eval`/`telemetry`
  behavior are untouched.
- The models are additive: a *view* over payloads, not a rewrite of them.

## Deferred follow-up work

- `ImplementationReportArtifact` and `QAReportArtifact` (Prompt 20).
- `eval_report`, `trace`, `final_diff` contracts and centralized evidence paths
  (Prompt 21).
- Cross-version payload migration and schema-version negotiation.
- Centralized runtime-path resolution for the logical scopes.
- Artifact lifecycle events (`artifact.created` / `.validated` / `.invalid`).
- Stage input/output (produce/consume) declarations and production adoption.
