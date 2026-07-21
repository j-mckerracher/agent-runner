# Stage Artifact Contracts and Lifecycle Events

Prompt 22 makes each canonical workflow stage's typed artifact **inputs and
outputs** explicit, and adds one reusable lifecycle boundary that validates
`ArtifactRef`s against those declarations and emits the four `artifact.*`
telemetry events.

This is a **reusable, fixture-proven contract only** — nothing is wired into
production orchestration (`run.py`, `core/steps.py`, runners, server routes,
eval execution). Live wiring is deferred to Prompt 23.

## Why

The pieces existed but were disconnected:

- Canonical stage strings live in `core/workflow_constants.py`
  (`WORKFLOW_STAGES`).
- Each typed artifact class carries `ARTIFACT_TYPE` / `ARTIFACT_SCHEMA` /
  `ARTIFACT_SCHEMA_VERSION` / `PRODUCER_STAGE` / `CONSUMER_STAGES`
  (`artifacts/payloads.py`), but nothing mapped **stage → artifacts it
  consumes/produces**, and nothing expressed **cardinality**.
- Telemetry had `artifact.created` / `artifact.validated` / `artifact.invalid`
  but no `artifact.missing`, and no reusable helper to emit the lifecycle.

## Two modules, split by dependency weight

| Module | Imports | Role |
|---|---|---|
| `workflow/stage_artifacts.py` | stdlib + `artifacts` (stdlib-only leaf) + `core.workflow_constants` | **Pure** declaration + registry + deterministic validation. **No telemetry.** |
| `workflow/artifact_lifecycle.py` | `telemetry` + `workflow.stage_artifacts` + `artifacts` | The only new module touching telemetry: validates then emits events. |

Keeping validation telemetry-free means it runs without a sink and the "a sink
failure must not mask the domain result" doctrine falls out naturally — the
result is computed before any emission (mirrors `workflow.stages.CallableStage`).

## Consume / produce matrix

| Stage | Consumes | Produces |
|---|---|---|
| `materialize` | — | — |
| `intake` | — | `story` ×1 |
| `task-generation` | `story` ×1 | `task_plan` ×1 |
| `task-assignment` | `story` ×1, `task_plan` ×1 | `assignment` ×1, `uow_spec` ×1+ |
| `execution` | `assignment` ×1, `uow_spec` ×1+ | `impl_report` ×1+ |
| `qa` | `story` ×1, `task_plan` ×1, `assignment` ×1, `impl_report` ×1+ | `qa_report` ×1 |
| `pr-review` | `story` ×1, `task_plan` ×1, `assignment` ×1, `impl_report` ×1+, `qa_report` ×1 | — |

`×1` = `EXACTLY_ONE`, `×1+` = `ONE_OR_MORE`. Schema/producer/consumer metadata
is derived from the artifact classes via `StageArtifactSpec.from_artifact` — no
hand-copied schema strings. The registry is validated at import time:

- a **produced** spec's stage must equal the artifact's canonical
  `PRODUCER_STAGE`;
- a **consumed** spec's stage must appear in the artifact's canonical
  `CONSUMER_STAGES`;
- duplicate stages, duplicate specs in a direction, and invalid cardinality all
  raise `StageArtifactRegistryError`.

## Public API (`workflow.stage_artifacts`)

- `ArtifactDirection` — `INPUT` / `OUTPUT`.
- `Cardinality(min_count, max_count)` with `ZERO` / `EXACTLY_ONE` /
  `OPTIONAL_ONE` / `ONE_OR_MORE`, `.label`, `.allows(count)`, `.to_dict()`.
- `StageArtifactSpec` — one typed slot; `from_artifact(cls, direction,
  cardinality)`; carries the canonical `producer_stage` / `consumer_stages`.
- `StageArtifactDeclaration(stage_name, inputs, outputs)`.
- Per-reference outcome model: `ReferenceOutcome` (one per supplied ref),
  `SlotOutcome` (one per declared spec; `MISSING` when a required slot is
  empty), `StageArtifactValidationResult(stage_name, slots, unexpected)` with
  `.ok` / `.errors` / `.warnings` / `.issues` / `.to_dict()`.
- `STAGE_ARTIFACT_REGISTRY`, `get_stage_declaration(name)` (raises
  `UnknownStageError`), `validate_stage_artifacts(stage, *, inputs=(),
  outputs=())`.

Issue codes: `artifact_missing`, `unexpected_artifact_type`,
`cardinality_too_many`, `artifact_schema_mismatch`,
`artifact_schema_version_mismatch`, `wrong_producer_stage`,
`incompatible_consumer_stage`; warning codes `unknown_artifact_schema`,
`unknown_producer_stage`, `unknown_consumer_stages`.

## Validation semantics

- Match `artifact_type` exactly; group refs by type per direction.
- Required slot with **zero** refs → `SlotOutcome` `MISSING` + `artifact_missing`
  (ERROR). Applies to **inputs and outputs alike**.
- Optional slot with zero refs → `VALID`, no issue, no event.
- Count over max → slot `INVALID` + `cardinality_too_many`.
- Ref whose type matches no spec for that direction → `INVALID` in `unexpected`
  (`unexpected_artifact_type`) — wrong type is *invalid*, not *missing*.
- **Direction-specific compatibility:**
  - **Input** ref: `producer_stage` (when known) must equal the artifact's
    canonical `PRODUCER_STAGE`; the current stage must appear in the ref's known
    `consumer_stages`.
  - **Output** ref: `producer_stage` (when known) must equal the **current
    stage**; the ref's known `consumer_stages` must match the artifact's
    canonical future consumers. The producing stage is **not** required to be
    among its own consumers.
  - Both: `artifact_schema` / `artifact_schema_version` mismatch → ERROR.
- **Absent-metadata policy:** a ref that leaves `artifact_schema` /
  `producer_stage` / `consumer_stages` as `None` yields a **WARNING**, never an
  ERROR, and is never silently upgraded to a passing compatibility claim.
  `.ok` stays `True` on warnings-only.
- Metadata-only and deterministic: no filesystem, subprocess, network, or
  checksum computation; path-vs-URI is preserved (a URI is never relabeled as a
  path).

## Lifecycle events (`workflow.artifact_lifecycle`)

`emit_stage_artifact_lifecycle(*, run_id, stage=None, sink, inputs=(),
outputs=(), declaration=None) -> StageArtifactValidationResult`

Resolves the declaration (`declaration` wins over `stage`), validates, then
emits — one terminal event per supplied ref, one per missing slot:

| Case | Events |
|---|---|
| Each **output** ref | `artifact.created` (ok) → `artifact.validated` / `artifact.invalid` |
| Each **input** ref | `artifact.validated` / `artifact.invalid` (no `created`) |
| Each unfilled **required** slot (input or output) | `artifact.missing` (no `created`) |
| Each unexpected **output** ref | `artifact.created` (ok) → `artifact.invalid` (still produced by this stage) |
| Each unexpected **input** ref | `artifact.invalid` (not produced here — no `created`) |
| Optional-absent slot | (no event) |

Payload rules:

- Every event carries `run_id` + `stage`.
- `artifact_path` is set **only** for a local-path ref; a URI-only ref puts the
  URI in `metadata.artifact_uri` and leaves `artifact_path` unset.
- `metadata` carries `artifact_type`, `direction`,
  `artifact_ref_schema_version`, known `artifact_schema` /
  `artifact_schema_version`, and `issue_codes` when present.
- **`validation_status` appears only on terminal events**
  (`validated` / `invalid` / `missing`). `artifact.created` reports creation via
  `status=ok` and its metadata contains **no** `validation_status`.
- `artifact.missing` names the missing type but carries no path/URI (no ref
  exists).
- Artifact contents, prompts, responses, and secrets are **never** emitted.

Sink ownership: caller-supplied and caller-owned — the boundary never flushes or
closes it. A `None` sink runs validation with emission as a no-op. A sink
`.emit` exception is swallowed locally so it cannot change the returned result.

### Sample payloads

Valid output (`intake` produces `story`, local path):

```json
{"event_type": "artifact.created", "run_id": "run-1", "stage": "intake",
 "status": "ok", "artifact_path": "/tmp/story.yaml",
 "metadata": {"artifact_type": "story", "direction": "output",
              "artifact_ref_schema_version": "1",
              "artifact_schema": "story", "artifact_schema_version": "1"}}
{"event_type": "artifact.validated", "run_id": "run-1", "stage": "intake",
 "status": "ok", "artifact_path": "/tmp/story.yaml",
 "metadata": {"artifact_type": "story", "direction": "output",
              "artifact_ref_schema_version": "1",
              "artifact_schema": "story", "artifact_schema_version": "1",
              "validation_status": "valid"}}
```

Missing required input (`task-generation` given no `story`):

```json
{"event_type": "artifact.missing", "run_id": "run-1", "stage": "task-generation",
 "status": "error",
 "metadata": {"artifact_type": "story", "direction": "input",
              "validation_status": "missing", "issue_codes": ["artifact_missing"]}}
```

## Tests

- `tests/test_stage_artifact_contracts.py` — registry/matrix, build guards,
  cardinality, and the full validation-semantics surface.
- `tests/test_artifact_lifecycle.py` — event mapping, payload rules, missing /
  unexpected, `None`-sink no-op, sink-failure precedence.
- `tests/test_stage_artifacts_isolation.py` — import-purity probes.
- `tests/test_trace_events.py` — `artifact.missing` create + round trip and the
  17-family count guard.
