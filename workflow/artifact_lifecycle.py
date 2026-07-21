"""Stage-artifact lifecycle event boundary (Prompt 22).

The single telemetry-emitting seam for stage-artifact validation. It resolves a
stage declaration, runs the **pure** ``validate_stage_artifacts`` from
``workflow.stage_artifacts``, and emits the four artifact lifecycle events
(``artifact.created`` / ``artifact.validated`` / ``artifact.invalid`` /
``artifact.missing``) through the canonical Prompt-4 ``telemetry`` contract via a
caller-supplied :class:`~telemetry.EventSink`.

This mirrors ``workflow.stages.CallableStage``: the domain result is computed
*before* any emission, the sink is caller-owned (never constructed, flushed, or
closed here), and a sink failure is swallowed locally so it can never change the
returned :class:`~workflow.stage_artifacts.StageArtifactValidationResult`.

Import weight lives here, not in ``stage_artifacts``: this module imports
``telemetry`` while the declaration/validation module stays telemetry-free and
usable without a sink. Nothing here is wired into production orchestration
(deferred to Prompt 23).

Event mapping — one terminal event per supplied reference, one per missing slot:

* Each **output** reference: ``artifact.created`` (status ``ok``, no
  ``validation_status`` in metadata) followed by exactly one terminal
  ``artifact.validated`` / ``artifact.invalid``.
* Each **input** reference: exactly one terminal ``artifact.validated`` /
  ``artifact.invalid`` (no ``created`` — the stage consumes, it did not make it).
* Each unfilled **required** slot (input or output): one ``artifact.missing``.
  A missing output never gets an ``artifact.created``.
* Each **unexpected** reference (type matches no declared slot): if supplied as
  an **output**, ``artifact.created`` followed by ``artifact.invalid`` (it was
  still produced by this stage); if supplied as an **input**, just
  ``artifact.invalid`` (it was not produced here).
* Optional-absent slot: no event.

Payloads carry ``run_id`` + ``stage`` and metadata (``artifact_type``,
``direction``, ``artifact_ref_schema_version``, known ``artifact_schema`` /
``artifact_schema_version``, ``issue_codes`` when present, ``validation_status``
only on terminal events). ``artifact_path`` is set only for local-path refs;
URI-only refs carry the URI in metadata and leave ``artifact_path`` unset.
Missing events name the type but carry no path/URI. Artifact contents, prompts,
responses, and secrets are never emitted.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from artifacts import ArtifactRef, ArtifactValidationStatus
from telemetry import EventSink, EventStatus, EventType, make_event

from .stage_artifacts import (
    ArtifactDirection,
    ReferenceOutcome,
    SlotOutcome,
    StageArtifactDeclaration,
    StageArtifactValidationResult,
    validate_stage_artifacts,
)


def _emit(sink: EventSink | None, event_type: EventType, run_id: str, **fields: Any) -> None:
    """Emit one lifecycle event. Never raises.

    A ``None`` sink makes emission a no-op. A sink ``.emit`` exception is
    swallowed here so a sink failure can never mask or alter the domain
    validation result (mirrors ``CallableStage._emit``).
    """
    if sink is None:
        return
    try:
        sink.emit(make_event(event_type, run_id, **fields))
    except Exception:  # noqa: BLE001 - deliberate: sink failure must not change
        # the returned validation result; the canonical sink records failures at
        # its own layer.
        pass


def _artifact_path(ref: ArtifactRef) -> str | None:
    """Local filesystem path, or ``None`` for a URI-only reference."""
    return str(ref.path) if ref.path is not None else None


def _ref_metadata(
    direction: ArtifactDirection,
    artifact_type: str,
    ref: ArtifactRef,
    *,
    validation_status: str | None = None,
    issue_codes: Iterable[str] = (),
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "artifact_type": artifact_type,
        "direction": direction.value,
        "artifact_ref_schema_version": ref.artifact_ref_schema_version,
    }
    if ref.artifact_schema is not None:
        metadata["artifact_schema"] = ref.artifact_schema
    if ref.artifact_schema_version is not None:
        metadata["artifact_schema_version"] = ref.artifact_schema_version
    # URI-only refs surface the locator in metadata; a local path goes to the
    # dedicated artifact_path field instead (never relabel a URI as a path).
    if ref.path is None and ref.uri is not None:
        metadata["artifact_uri"] = ref.uri
    if validation_status is not None:
        metadata["validation_status"] = validation_status
    codes = list(issue_codes)
    if codes:
        metadata["issue_codes"] = codes
    return metadata


def _emit_reference(
    sink: EventSink | None,
    run_id: str,
    stage: str,
    slot: SlotOutcome,
    ref_outcome: ReferenceOutcome,
) -> None:
    """Emit the lifecycle for one matched reference in a slot."""
    ref = ref_outcome.ref
    direction = ref_outcome.direction
    artifact_type = ref_outcome.artifact_type

    # An output reference is created by this stage before it is validated.
    if direction is ArtifactDirection.OUTPUT:
        _emit(
            sink,
            EventType.ARTIFACT_CREATED,
            run_id,
            stage=stage,
            status=EventStatus.OK,
            artifact_path=_artifact_path(ref),
            metadata=_ref_metadata(direction, artifact_type, ref),
        )

    # A slot-level error (e.g. cardinality) taints every reference in the slot;
    # otherwise the reference's own status decides the terminal event. Exactly
    # one terminal per reference, never contradictory.
    terminal_invalid = (
        slot.status is ArtifactValidationStatus.INVALID
        or ref_outcome.status is ArtifactValidationStatus.INVALID
    )
    issue_codes = [i.code for i in slot.issues] + [i.code for i in ref_outcome.issues]

    if terminal_invalid:
        _emit(
            sink,
            EventType.ARTIFACT_INVALID,
            run_id,
            stage=stage,
            status=EventStatus.ERROR,
            artifact_path=_artifact_path(ref),
            metadata=_ref_metadata(
                direction,
                artifact_type,
                ref,
                validation_status=ArtifactValidationStatus.INVALID.value,
                issue_codes=issue_codes,
            ),
        )
    else:
        _emit(
            sink,
            EventType.ARTIFACT_VALIDATED,
            run_id,
            stage=stage,
            status=EventStatus.OK,
            artifact_path=_artifact_path(ref),
            metadata=_ref_metadata(
                direction,
                artifact_type,
                ref,
                validation_status=ArtifactValidationStatus.VALID.value,
                # Warning-only refs stay valid but still report their codes.
                issue_codes=[i.code for i in ref_outcome.issues],
            ),
        )


def _emit_missing_slot(
    sink: EventSink | None,
    run_id: str,
    stage: str,
    slot: SlotOutcome,
) -> None:
    """Emit one ``artifact.missing`` for a required slot with no references."""
    _emit(
        sink,
        EventType.ARTIFACT_MISSING,
        run_id,
        stage=stage,
        status=EventStatus.ERROR,
        metadata={
            "artifact_type": slot.spec.artifact_type,
            "direction": slot.direction.value,
            "validation_status": ArtifactValidationStatus.MISSING.value,
            "issue_codes": [i.code for i in slot.issues],
        },
    )


def _emit_unexpected(
    sink: EventSink | None,
    run_id: str,
    stage: str,
    ref_outcome: ReferenceOutcome,
) -> None:
    """Emit the lifecycle for a reference matching no declared slot.

    An unexpected reference supplied as an **output** is still a reference this
    stage produced, so it gets its ``artifact.created`` like any other output
    before the terminal ``artifact.invalid``. An unexpected **input** reference
    was not produced here, so it gets only the terminal ``artifact.invalid``
    (mirrors ``_emit_reference``'s direction split).
    """
    ref = ref_outcome.ref
    direction = ref_outcome.direction
    artifact_type = ref_outcome.artifact_type

    if direction is ArtifactDirection.OUTPUT:
        _emit(
            sink,
            EventType.ARTIFACT_CREATED,
            run_id,
            stage=stage,
            status=EventStatus.OK,
            artifact_path=_artifact_path(ref),
            metadata=_ref_metadata(direction, artifact_type, ref),
        )

    _emit(
        sink,
        EventType.ARTIFACT_INVALID,
        run_id,
        stage=stage,
        status=EventStatus.ERROR,
        artifact_path=_artifact_path(ref),
        metadata=_ref_metadata(
            direction,
            artifact_type,
            ref,
            validation_status=ArtifactValidationStatus.INVALID.value,
            issue_codes=[i.code for i in ref_outcome.issues],
        ),
    )


def emit_stage_artifact_lifecycle(
    *,
    run_id: str,
    stage: str | StageArtifactDeclaration | None = None,
    sink: EventSink | None,
    inputs: Iterable[ArtifactRef] = (),
    outputs: Iterable[ArtifactRef] = (),
    declaration: StageArtifactDeclaration | None = None,
) -> StageArtifactValidationResult:
    """Validate stage artifacts and emit their lifecycle events.

    Resolves the declaration (explicit ``declaration`` wins over ``stage``),
    computes the validation result *first*, then emits events through the
    caller-supplied ``sink``. Returns the domain result unchanged regardless of
    any sink failure. A ``None`` sink runs validation with emission as a no-op.
    """
    target: str | StageArtifactDeclaration
    if declaration is not None:
        target = declaration
    elif stage is not None:
        target = stage
    else:
        raise ValueError("emit_stage_artifact_lifecycle requires stage or declaration")

    result = validate_stage_artifacts(target, inputs=inputs, outputs=outputs)
    stage_name = result.stage_name

    for slot in result.slots:
        if slot.status is ArtifactValidationStatus.MISSING:
            _emit_missing_slot(sink, run_id, stage_name, slot)
            continue
        for ref_outcome in slot.references:
            _emit_reference(sink, run_id, stage_name, slot, ref_outcome)

    for ref_outcome in result.unexpected:
        _emit_unexpected(sink, run_id, stage_name, ref_outcome)

    return result
