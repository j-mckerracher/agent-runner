# Versioned `ArtifactRef` Contract (v0.4)

## Why this exists

Agent Workbench carries artifact-shaped data in legacy/generic fields today:
`AgentResult.artifacts_touched` (`tuple[str, ...] | None`),
`RunContext`/`WorkflowResult.artifact_references` (`tuple[Any, ...]`),
`eval.report_schema.Reference`, and `TraceEvent.artifact_path` (`str`). None
of these is a first-class, versioned, machine-readable artifact *identity*.

`artifacts/models.py::ArtifactRef` is that identity: a small, deterministic,
stdlib-only data contract that honestly identifies a local (`path`) or
URI-addressed (`uri`) artifact and carries producer/consumer/schema/
validation/checksum/metadata information about it. It is the reusable leaf
contract that later prompts (concrete artifact schemas, loaders, validators,
centralized paths, stage produce/consume wiring) build on.

**Public import path:**

```python
from artifacts import ArtifactRef, ArtifactValidationStatus
```

Also exported: `ArtifactRefValidationError`,
`ArtifactMetadataSerializationError`, `ARTIFACT_REF_SCHEMA_VERSION`,
`SUPPORTED_ARTIFACT_REF_SCHEMA_VERSIONS`.

## Fields

`ArtifactRef` is a frozen, keyword-only dataclass.

| Field | Type | Rules |
|---|---|---|
| `artifact_type` | `str` | **Required.** Nonempty (non-whitespace). Stored verbatim, not trimmed. |
| `path` | `Path \| None` | Optional. `str` is coerced to `Path` without filesystem access; empty/whitespace strings are rejected (never become `Path('.')`). At least one of `path`/`uri` required. |
| `uri` | `str \| None` | Optional. Nonempty when supplied. At least one of `path`/`uri` required. |
| `producer_stage` | `str \| None` | Optional. Nonempty when supplied. |
| `consumer_stages` | `tuple[str, ...] \| None` | Optional, tri-state (see below). `list`/`tuple` only — never `str`/`bytes`. Items nonempty; duplicates rejected; order preserved. |
| `artifact_schema` | `str \| None` | Optional. Names the artifact's payload schema. Must pair with `artifact_schema_version`. |
| `artifact_schema_version` | `str \| None` | Optional. Must pair with `artifact_schema`. |
| `validation_status` | `ArtifactValidationStatus \| None` | Optional. Enum or resolvable string; garbage rejected; absence stays `None`. |
| `checksum_sha256` | `str \| None` | Optional. Exactly 64 lowercase hex chars; **never computed** by this contract. |
| `metadata` | `Mapping[str, Any]` | Free-form. Must be a `Mapping`; defensively copied. JSON-ability checked at serialization. |
| `artifact_ref_schema_version` | `str` | Contract version. Defaults to `"1"`; must be in `SUPPORTED_ARTIFACT_REF_SCHEMA_VERSIONS`. |

### `ArtifactValidationStatus`

```
NOT_VALIDATED = "not_validated"
VALID         = "valid"
INVALID       = "invalid"
MISSING       = "missing"
```

## Contract version vs artifact-schema version

Two independent version axes, deliberately not conflated:

- **`artifact_ref_schema_version`** — the version of *this `ArtifactRef`
  contract's shape*. Checked against
  `SUPPORTED_ARTIFACT_REF_SCHEMA_VERSIONS`; an unsupported value is rejected
  rather than parsed as if it matched the current contract.
- **`artifact_schema_version`** — the version of the *referenced artifact's
  own payload schema* (e.g. the plan JSON's schema). This contract does not
  interpret it; it only pairs it with `artifact_schema`.

## Path vs URI

`path` and `uri` are independent addressing forms. **At least one** must be
present. **Both** are allowed simultaneously and there is **no equivalence
check** between them — this contract does not assert that a `path` and a `uri`
point at the same bytes, and it never resolves, reads, or stats either.

## Validation-status and checksum semantics

- `validation_status` absent (`None`) means "unknown / not recorded" and is
  distinct from the explicit `NOT_VALIDATED` state. A garbage string is
  rejected, never silently coerced to `NOT_VALIDATED`.
- `checksum_sha256` is **never computed** by this contract. It only *validates*
  that a supplied value is exactly 64 lowercase hex characters (matching
  `hashlib.sha256().hexdigest()`); `fullmatch` rejects a 64-char prefix of a
  longer string, uppercase hex, and non-hex characters.

## Serialization & forward-compat

- `to_dict()` always includes `artifact_type` and
  `artifact_ref_schema_version`. `None` optionals are omitted. Observed empty
  collections are preserved (`consumer_stages == ()` -> `[]`). `Path` -> `str`,
  enum -> `.value`. `metadata` is included only when non-empty and its
  JSON-ability is verified first, so a serialization failure raises
  `ArtifactMetadataSerializationError` before any partial output is produced.
- `to_json()` is deterministic (`json.dumps(..., sort_keys=True)`).
- `from_dict()` ignores unknown top-level keys (forward-compat) and passes raw
  known values to the constructor, so `__post_init__` is the single place that
  validates and normalizes them. That is what makes `path=""` a contract error
  (not `Path('.')`), and `validation_status="bad"` / `consumer_stages="qa"`
  surface the aggregate error rather than a bare `ValueError` or a silent
  character split.
- `validate_payload(data, where)` (staticmethod) performs structural
  validation without constructing — at minimum rejecting a non-`Mapping`
  payload (including `None`).

## Import isolation & no filesystem I/O

`artifacts` is a stdlib-only leaf package. Importing `artifacts` /
`artifacts.models` loads nothing from `core`, `workflow`, `runners`, `eval`,
`server`, `telemetry`, `opik`, or any vendor SDK. Construction and
serialization perform **no** filesystem access, subprocess, network, env
mutation, or logging. Both guarantees are enforced by
`tests/test_artifact_ref_isolation.py`.

## Non-migration boundary (Prompt 18)

Prompt 18 introduces the reusable contract **only**. No existing field is
migrated and no production workflow changes: `artifacts_touched`,
`artifact_references`, `eval.report_schema.Reference`,
`TraceEvent.artifact_path`, writers, path conventions, formats, validators,
and workflow order are untouched, and no producer/consumer adopts
`ArtifactRef` yet. Later prompts define concrete artifact schemas, loaders,
validators, centralized paths, and stage produce/consume wiring on top of this
leaf contract.

## Examples

### Path-based

```python
from artifacts import ArtifactRef, ArtifactValidationStatus

ref = ArtifactRef(
    artifact_type="plan",
    path="planning/plan.json",
    producer_stage="planning",
    consumer_stages=["execution", "qa"],
    artifact_schema="plan.schema",
    artifact_schema_version="2",
    validation_status=ArtifactValidationStatus.VALID,
    checksum_sha256="a" * 64,
)
ref.to_dict()
```

```json
{
  "artifact_type": "plan",
  "artifact_ref_schema_version": "1",
  "path": "planning/plan.json",
  "producer_stage": "planning",
  "consumer_stages": ["execution", "qa"],
  "artifact_schema": "plan.schema",
  "artifact_schema_version": "2",
  "validation_status": "valid",
  "checksum_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}
```

### URI-based

```python
from artifacts import ArtifactRef

ref = ArtifactRef(artifact_type="plan", uri="s3://bucket/plan.json")
ref.to_dict()
```

```json
{
  "artifact_type": "plan",
  "artifact_ref_schema_version": "1",
  "uri": "s3://bucket/plan.json"
}
```
