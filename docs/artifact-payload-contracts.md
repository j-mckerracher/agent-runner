# Planning-artifact and report-artifact payload contracts (v0.4)

Prompt 19 adds typed, immutable, read-only **payload** contracts for the four
planning-stage artifacts, layered on top of the Prompt 18
[`ArtifactRef`](./artifact-ref.md) *reference* contract. Prompt 20 extends the
same module with two more: `ImplementationReportPayload` and `QAReportPayload`,
covering the legacy `impl_report.yaml` and `qa_report.yaml` workflow artifacts.

Where `ArtifactRef` answers *"what/where is this artifact"* (type, path, producer,
schema identity), these contracts answer *"what is inside it"* — a validated,
typed view of the payload, plus the metadata needed to mint a matching
`ArtifactRef`.

## Public imports

```python
from artifacts import (
    StoryArtifact, TaskPlanArtifact, AssignmentArtifact, UowSpecArtifact,
    PLANNING_ARTIFACTS,            # ordered tuple of the four planning-stage contract classes
    ImplementationReportPayload, QAReportPayload,
    DefinitionOfDoneItem, QAAcValidation,
    load_implementation_report, load_qa_report,
    ValidationResult, ValidationIssue, ValidationSeverity,
    ArtifactValidationError,      # raised on structural (ERROR) problems
    ArtifactLoadError,            # raised on transport problems (I/O, parse, missing PyYAML)
)
```

## Contract schema version vs payload schema version

Independent version axes coexist:

- `ArtifactRef.artifact_ref_schema_version` — shape of the *reference* record (P18).
- `<Artifact>.ARTIFACT_SCHEMA_VERSION` — shape of the *payload* modeled here (all
  currently `"1"`).
- `<Artifact>.ARTIFACT_SCHEMA` — the payload's stable, namespaced identity
  (e.g. `agent-workbench.story`).
- `ImplementationReportPayload.schema_version` / `QAReportPayload.schema_version`
  (P20 only) — a **document-embedded** version read from the source report
  itself, distinct from the three axes above. See
  [Report schema-version handling](#report-schema-version-handling).

For the four planning-stage contracts, the catalog **assigns** each payload its
schema identity; it does not claim that the legacy on-disk files embed that
identity, and cross-version payload *migration* remains deferred (see below).

## The four planning-stage contracts

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
responsibility. This applies equally to the two report contracts below.

### Core-field projections (not lossless mirrors)

These four models capture the fields the workflow depends on. Unmodeled top-level
keys (`critical_path`, `estimated_total_batches`, `parallelization_opportunities`,
`ac_coverage_matrix`, `notes`, `metacognitive_context`, …) are ignored, not
retained. Consequently `to_dict()` is a round trip of the **typed model**, not of
the source document:

```python
obj = StoryArtifact.from_mapping(data)
StoryArtifact.from_mapping(obj.to_dict()) == obj   # True (typed projection)
obj.to_dict() == data                              # not guaranteed
```

The two P20 report contracts below deliberately do **not** follow this
projection model — see
[Report contracts preserve extension data](#report-contracts-preserve-extension-data).

## The two report contracts

| artifact type | schema / version | producer | consumers | path scope | canonical relative path | params | format |
|---|---|---|---|---|---|---|---|
| `impl_report` | `agent-workbench.impl-report` / `1` | `execution` | *(none — P20 does not wire consumers)* | `agent_context` | `{change_id}/execution/{uow_id}/impl_report.yaml` | `change_id`, `uow_id` | yaml |
| `qa_report` | `agent-workbench.qa-report` / `1` | `qa` | *(none — P20 does not wire consumers)* | `agent_context` | `{change_id}/qa/qa_report.yaml` | `change_id` | yaml |

Required fields:

- `ImplementationReportPayload`: `uow_id`, `status` ∈ `{complete, partial, blocked}`,
  `implementation_summary`, `definition_of_done_status` (non-empty list of
  `DefinitionOfDoneItem { item: non-empty str, met: bool, evidence: str | None }`).
  A legacy top-level `definition_of_done` list is accepted as an alias when
  `definition_of_done_status` is absent, with a `legacy_definition_of_done`
  warning; the canonical key is always what is serialized.
- `QAReportPayload`: `story_id`, `qa_status` ∈ `{pass, fail, blocked}`,
  `acceptance_criteria_validation` (non-empty mapping of AC id →
  `QAAcValidation`, each with `status` ∈ `{pass, fail, partial}`),
  `final_recommendation` ∈ `{approve, reject, approve_with_conditions}`.
  `conditions` (non-empty list of strings) is required when
  `final_recommendation == "approve_with_conditions"`, and is an
  `invalid_value`/`missing_field` error otherwise omitted.

`met` is validated with an explicit `isinstance(raw, bool)` check, so integer
truthiness (`0`/`1`) and string truthiness (`"true"`/`"false"`) are both
rejected as `wrong_type`, never silently coerced.

### Report contracts preserve extension data

Unlike the four planning-stage projections, `ImplementationReportPayload` and
`QAReportPayload` preserve every unrecognized top-level key as read-only
`extension` data (a frozen, recursively-immutable mapping — `MappingProxyType`
for nested mappings, `tuple` for nested lists), because generated agent reports
carry many evolving sections (`engineering_scope_classification`,
`files_modified`, `commands_executed`, `worktree_management`,
`regression_risk_assessment`, `issues_found`, `metacognitive_context`, …) that
must survive a load/serialize round trip untouched:

```python
report = ImplementationReportPayload.from_mapping(data)
report.extension["files_modified"]           # preserved, read-only
ImplementationReportPayload.from_mapping(report.to_dict()) == report   # True
```

This uses a purpose-built recursive freeze/thaw (`_freeze_extension_value` /
`_thaw_extension_value`), not the cycle-safe canonicalization graph in
`artifacts.models` — parsed YAML/JSON data is already restricted to safe
built-in types and has no cycles, so the lighter mechanism is sufficient.

### Report schema-version handling

`ImplementationReportPayload.schema_version` / `QAReportPayload.schema_version`
is read from the source document itself, independent of `ARTIFACT_SCHEMA_VERSION`:

- Missing `schema_version` → defaults to the current supported version (`"1"`).
- A boolean (or any non-`str`) value → `wrong_type` (bool is checked before any
  numeric handling, so it is never mistaken for `0`/`1`).
- A string outside the supported set (malformed or an unsupported future
  version) → `invalid_value` naming the supported set.
- The canonical version is always what `to_dict()`/`to_json()` emit.

No migration machinery exists; a second version would be added as a new entry
in `_SUPPORTED_REPORT_SCHEMA_VERSIONS`, not by rewriting old documents.

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

The two P20 report contracts add their own enum constraints (`status`,
`qa_status`, per-AC `status`, `final_recommendation` — see
[The two report contracts](#the-two-report-contracts)), validated through the
same `invalid_value`/`wrong_type` machinery, plus the conditional
`conditions`-required-iff-`approve_with_conditions` rule.

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
| `legacy_definition_of_done` | impl_report top-level `definition_of_done` → `definition_of_done_status` |

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

# The two P20 report contracts use the same loaders, plus module-level
# convenience wrappers:
impl_report = load_implementation_report("…/C1/execution/U1/impl_report.yaml")
qa_report = load_qa_report("…/C1/qa/qa_report.yaml")
qa_report2, result2 = QAReportPayload.load_with_validation("…/C1/qa/qa_report.yaml")
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

`ImplementationReportPayload.to_artifact_ref()` and `QAReportPayload.to_artifact_ref()`
override the base factory to auto-populate stable identity `metadata`
(`uow_id`/`status`/`change_id`/`story_id` for the former;
`story_id`/`qa_status`/`final_recommendation` for the latter). Caller-supplied
`metadata` keys take precedence over these defaults via `dict.update()`:

```python
report = ImplementationReportPayload.from_mapping(impl_data)
ref = report.to_artifact_ref(path="C1/execution/U1/impl_report.yaml")
ref.metadata["uow_id"], ref.metadata["status"]   # auto-populated

qa = QAReportPayload.from_mapping(qa_data)
ref = qa.to_artifact_ref(path="C1/qa/qa_report.yaml", metadata={"run": "abc"})
ref.metadata["qa_status"], ref.metadata["run"]   # both present
```

As with the planning-stage contracts, these two producer-stage conversions
(`execution`, `qa`) exist without any consumer-stage wiring or live workflow
adoption — see [Legacy compatibility & non-migration boundary](#legacy-compatibility--non-migration-boundary).

## Import-isolation guarantee

`artifacts` remains a stdlib-only leaf. Importing `artifacts`,
`artifacts.payloads`, or `artifacts.validation` pulls in **none** of `core`,
`workflow`, `runners`, `eval`, `server`, `telemetry`, `opik`, the Anthropic /
OpenAI / Google SDKs, or **PyYAML**. Construction, validation, serialization, and
`to_artifact_ref` perform no filesystem, subprocess, network, or environment
access. (`load*()` reads a file by design and is excluded from that no-side-effect
guarantee.) This guarantee, and the no-side-effect checks, cover the two P20
report contracts identically to the four planning-stage contracts. Enforced by
`tests/test_artifact_payloads_isolation.py`.

## Legacy compatibility & non-migration boundary

- Current on-disk filenames and directory layout are unchanged, for both the
  planning-stage artifacts and `impl_report.yaml` / `qa_report.yaml`.
- No production producer or consumer adopts any of these six contracts in this
  prompt or the previous one — **live workflow integration is explicitly
  deferred**. `to_artifact_ref()` exists as a conversion capability only.
- `core/artifact_utils.py`, `core/run_cmds.py`, and
  `scripts/validate-artifact-schema.py` are untouched; existing writers,
  readers, path conventions, and `core`/`eval`/`telemetry` behavior are
  unchanged.
- The models are additive: a *view* over payloads, not a rewrite of them.

## Deferred follow-up work

- `eval_report`, `trace`, `final_diff` contracts and centralized evidence paths
  (Prompt 21).
- Live production adoption of any of the six payload contracts by a real
  producer or consumer stage.
- Cross-version payload migration (beyond the single currently-supported
  version each contract accepts) and multi-version schema-version negotiation.
- Centralized runtime-path resolution for the logical scopes.
- Artifact lifecycle events (`artifact.created` / `.validated` / `.invalid`).
- Stage input/output (produce/consume) declarations and production adoption.
- Consumer-stage wiring for `impl_report` / `qa_report` (P20 sets producer
  stages only, per design).
