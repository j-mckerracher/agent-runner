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
| `metadata` | `Mapping[str, Any]` | Free-form. Must be a `Mapping`; canonicalized into an **immutable** graph of frozen nodes (tuples canonicalized to lists at every depth) and exposed as a **read-only, deeply immutable** `Mapping` view. Caller input (including non-string keys) is never retained, and callers cannot mutate the ref through `ref.metadata` (top-level, nested, or via internal attributes). Full JSON-ability — string keys, finite floats, acyclic, supported leaves only — is checked lazily at serialization. |
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
  enum -> `.value`. `metadata` is included only when non-empty; it is strictly
  re-serialized into a **fresh** nested structure (never aliasing the ref's own
  metadata), rejecting non-string keys, non-finite floats (`NaN`/`±Infinity`),
  reference cycles, and unsupported leaves with
  `ArtifactMetadataSerializationError` before any partial output is produced.
- `to_json()` is deterministic and strict
  (`json.dumps(..., sort_keys=True, allow_nan=False)`); any residual encoder
  `ValueError` is surfaced as `ArtifactMetadataSerializationError`.
- `from_dict()` ignores unknown top-level keys (forward-compat) and passes raw
  known values to the constructor, so `__post_init__` is the single place that
  validates and normalizes them. That is what makes `path=""` a contract error
  (not `Path('.')`), and `validation_status="bad"` / `consumer_stages="qa"`
  surface the aggregate error rather than a bare `ValueError` or a silent
  character split. A **missing** required `artifact_type` is likewise surfaced
  as an aggregated `ArtifactRefValidationError` (alongside any other field
  problems), never a bare constructor `TypeError`.
- `validate_payload(data, where)` (staticmethod) performs structural
  validation without constructing — at minimum rejecting a non-`Mapping`
  payload (including `None`).

## Metadata semantics

- **Immutable canonical graph.** At construction, `metadata` is canonicalized
  into a **genuinely immutable** graph of frozen, slotted nodes: a mapping
  becomes an immutable `_MappingNode` (an ordered tuple of key/value entries), a
  sequence becomes an immutable `_SequenceNode` (an ordered tuple of items), and
  JSON scalars pass through. The graph is memoized by object identity so shared
  inputs are tolerated. Caller-owned containers are never retained, so later
  mutation of the input (at any depth) cannot alter a constructed `ArtifactRef`.
- **Nothing reachable is mutable.** The `metadata` field is a
  `collections.abc.Mapping` view whose only stored state is the immutable graph
  root (`_root`). The view exposes no mutation API (item assignment/deletion on
  `ref.metadata` raises `TypeError`), `_root` itself cannot be reassigned
  (`ref.metadata._root = ...` raises `AttributeError`), and even reached
  directly the root yields only immutable nodes and tuples — there is no
  `dict`/`list` graph to edit. Each `ref.metadata[key]` access **materializes a
  fresh plain copy**, so mutating a value read out of the view (at any depth)
  never changes the ref. The view is not a `dict` but compares by value
  (`ref.metadata == {...}`) and is unhashable, exactly as a plain metadata
  `dict` would be.
- **Caller keys are never retained.** Mapping keys are canonicalized: string
  keys are detached into an exact built-in `str` (subclasses are never held);
  any non-string key is replaced by an immutable
  `_UnsupportedKey` marker recording only the original type name. The caller's
  key object is never held or exposed (iteration yields the exact `str` or the
  marker, not the caller object), and serialization still rejects the
  non-string key lazily.
- **Scalar subclasses are detached.** Accepted JSON scalars are canonicalized
  into exact built-ins — `str`/`int`/`float` subclasses become plain
  `str`/`int`/`float`, `bool` stays `bool` (checked before `int`), and `None`
  stays `None`. The underlying built-in payload is extracted without calling
  overridable conversion hooks (`__int__`/`__float__`/`__str__`/`__getitem__`),
  so a subclass cannot change the stored value or wire output (and `-0.0` is
  preserved). A mutable scalar subclass is therefore never retained: mutating
  its attributes after construction cannot change `ArtifactRef` equality, and
  `to_dict()` returns exact built-in leaves rather than aliasing the caller
  object.
- **Tuple canonicalization.** Tuples are normalized to lists at every depth, so
  `(1, 2)` and `[1, 2]` are equivalent inputs. This is what makes tuple-bearing
  metadata survive a dict or JSON round trip to `ArtifactRef` equality
  (`from_dict(ref.to_dict()) == ref`, `from_dict(json.loads(ref.to_json())) == ref`).
- **Finite floats only.** `NaN`, `Infinity`, and `-Infinity` are rejected at
  serialization with `ArtifactMetadataSerializationError` naming the offending
  location; ordinary finite floats serialize unchanged.
- **Acyclic canonical tree.** A reference cycle (direct or via mixed dict/list
  nesting) is replaced during canonicalization by an immutable cycle marker, so
  the stored graph is always a **finite tree** — equality and hashing can never
  leak a `RecursionError`. The cycle is still rejected **lazily** at
  serialization with `ArtifactMetadataSerializationError`. Shared **non-cyclic**
  substructures (the same child referenced from two keys) remain valid and
  serialize under each path.
- **Fresh output.** `to_dict()` serializes the immutable canonical graph into a
  freshly materialized nested structure; mutating one result never affects the
  ref or any other `to_dict()` result.
- **Invalid metadata does not silently compare equal.** Unsupported leaf values
  and non-string keys are stored as immutable markers with **identity**
  equality, so two `ArtifactRef`s holding *different* unsupported values (even of
  the same type, e.g. two distinct `bytearray`s) compare **unequal** rather than
  collapsing to equal. The caller's mutable object is never held; both reading
  such a value through the view and serializing it raise
  `ArtifactMetadataSerializationError` naming the original leaf type, and
  non-string keys are likewise rejected with an actionable location, never
  silently stringified.
- Construction stays lazy and side-effect-free: canonicalization validates
  nothing and touches no filesystem, subprocess, network, env, or logging; all
  strict checks happen at serialization time.

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
