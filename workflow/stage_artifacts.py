"""Stage-artifact declarations, registry, and deterministic validation.

Prompt 22 makes each canonical workflow stage's typed artifact inputs and
outputs explicit, and validates supplied :class:`~artifacts.models.ArtifactRef`
objects against those declarations. This module is deliberately **pure**: it
imports only the standard library, the stdlib-only ``artifacts`` leaf, and the
canonical stage strings in ``core.workflow_constants``. It does NOT import
``telemetry`` — event emission lives in ``workflow.artifact_lifecycle`` so that
declaration/validation stays a side-effect-free, deterministic contract usable
without a sink (and so a sink failure can never influence a validation result).

Compatibility metadata (``artifact_schema``, ``artifact_schema_version``,
``producer_stage``, ``consumer_stages``) is derived from the accepted artifact
classes via :meth:`StageArtifactSpec.from_artifact`; nothing here re-declares a
second copy of those constants by hand.

Nothing in this module is wired into production orchestration (deferred to
Prompt 23). It is a reusable contract plus deterministic proof only.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar

from artifacts import (
    AssignmentArtifact,
    ArtifactRef,
    ArtifactValidationStatus,
    ImplementationReportArtifact,
    QAReportArtifact,
    StoryArtifact,
    TaskPlanArtifact,
    UowSpecArtifact,
    ValidationIssue,
    ValidationSeverity,
)
from core.workflow_constants import (
    STAGE_EXECUTION,
    STAGE_INTAKE,
    STAGE_MATERIALIZE,
    STAGE_PR_REVIEW,
    STAGE_QA,
    STAGE_TASK_ASSIGNMENT,
    STAGE_TASK_GENERATION,
    WORKFLOW_STAGES,
)

# --------------------------------------------------------------------------- #
# Stable, machine-readable issue codes                                        #
# --------------------------------------------------------------------------- #

#: A required declared artifact for which no matching reference was supplied.
ISSUE_MISSING_REQUIRED = "artifact_missing"
#: A supplied reference whose type matches no declared spec for the direction.
ISSUE_UNEXPECTED_TYPE = "unexpected_artifact_type"
#: More references supplied for a slot than its cardinality permits.
ISSUE_CARDINALITY_TOO_MANY = "cardinality_too_many"
#: A reference whose ``artifact_schema`` disagrees with the declaration.
ISSUE_SCHEMA_MISMATCH = "artifact_schema_mismatch"
#: A reference whose ``artifact_schema_version`` disagrees with the declaration.
ISSUE_SCHEMA_VERSION_MISMATCH = "artifact_schema_version_mismatch"
#: A reference whose ``producer_stage`` disagrees with the expected producer.
ISSUE_WRONG_PRODUCER = "wrong_producer_stage"
#: A reference whose ``consumer_stages`` are incompatible with the declaration.
ISSUE_INCOMPATIBLE_CONSUMER = "incompatible_consumer_stage"

#: Compatibility warning: reference left ``artifact_schema`` unknown (``None``).
WARNING_UNKNOWN_SCHEMA = "unknown_artifact_schema"
#: Compatibility warning: reference left ``producer_stage`` unknown (``None``).
WARNING_UNKNOWN_PRODUCER = "unknown_producer_stage"
#: Compatibility warning: reference left ``consumer_stages`` unknown (``None``).
WARNING_UNKNOWN_CONSUMER = "unknown_consumer_stages"


class StageArtifactRegistryError(ValueError):
    """Raised when a stage-artifact declaration set is internally inconsistent.

    Covers duplicate stage declarations, duplicate/contradictory specs for one
    stage and direction, invalid cardinality, and producer/consumer
    relationships that contradict the accepted artifact-class metadata.
    """


class UnknownStageError(KeyError):
    """Raised by :func:`get_stage_declaration` for an unregistered stage name.

    Subclasses ``KeyError`` for familiarity but carries an actionable message
    listing the known stages.
    """

    def __init__(self, stage_name: str, known: tuple[str, ...]) -> None:
        self.stage_name = stage_name
        self.known = known
        super().__init__(
            f"no stage-artifact declaration for stage {stage_name!r}; "
            f"known stages: {', '.join(known)}"
        )


class ArtifactDirection(str, Enum):
    """Whether a spec describes an input a stage consumes or an output it produces."""

    INPUT = "input"
    OUTPUT = "output"


@dataclass(frozen=True)
class Cardinality:
    """Machine-readable count constraint for one artifact slot.

    ``min_count`` and ``max_count`` (``None`` == unbounded) make the four
    required shapes explicit and distinguishable: zero, exactly one, optional
    one, and one-or-more. ``allows`` answers the range check used by validation.
    """

    min_count: int
    max_count: int | None

    # Named shapes are attached as class attributes below the definition.
    # ClassVar keeps the dataclass from treating them as instance fields.
    ZERO: ClassVar["Cardinality"]
    EXACTLY_ONE: ClassVar["Cardinality"]
    OPTIONAL_ONE: ClassVar["Cardinality"]
    ONE_OR_MORE: ClassVar["Cardinality"]

    def __post_init__(self) -> None:
        if self.min_count < 0:
            raise StageArtifactRegistryError(
                f"cardinality min_count must be >= 0, got {self.min_count}"
            )
        if self.max_count is not None and self.max_count < self.min_count:
            raise StageArtifactRegistryError(
                f"cardinality max_count ({self.max_count}) must be >= "
                f"min_count ({self.min_count})"
            )

    @property
    def required(self) -> bool:
        """True when at least one reference must be supplied."""
        return self.min_count >= 1

    @property
    def label(self) -> str:
        """A stable human/machine label for the four canonical shapes."""
        shape = (self.min_count, self.max_count)
        return {
            (0, 0): "zero",
            (1, 1): "exactly_one",
            (0, 1): "optional_one",
            (1, None): "one_or_more",
            (0, None): "zero_or_more",
        }.get(shape, f"{self.min_count}..{self.max_count}")

    def allows(self, count: int) -> bool:
        """Whether ``count`` supplied references satisfies this cardinality."""
        if count < self.min_count:
            return False
        if self.max_count is not None and count > self.max_count:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {"min": self.min_count, "max": self.max_count, "label": self.label}


Cardinality.ZERO = Cardinality(0, 0)
Cardinality.EXACTLY_ONE = Cardinality(1, 1)
Cardinality.OPTIONAL_ONE = Cardinality(0, 1)
Cardinality.ONE_OR_MORE = Cardinality(1, None)


@dataclass(frozen=True)
class StageArtifactSpec:
    """One typed artifact a stage consumes or produces, with cardinality.

    Compatibility metadata (schema, schema version, canonical producer and
    consumer stages) is exposed publicly here rather than hidden in registry
    logic, so a declaration is fully inspectable. Build with
    :meth:`from_artifact` to derive these from an accepted artifact class.
    """

    direction: ArtifactDirection
    artifact_type: str
    artifact_schema: str
    artifact_schema_version: str
    producer_stage: str
    consumer_stages: tuple[str, ...]
    cardinality: Cardinality

    @classmethod
    def from_artifact(
        cls,
        artifact_cls: Any,
        direction: ArtifactDirection,
        cardinality: Cardinality,
    ) -> "StageArtifactSpec":
        """Derive a spec from an artifact class's declared metadata."""
        return cls(
            direction=direction,
            artifact_type=artifact_cls.ARTIFACT_TYPE,
            artifact_schema=artifact_cls.ARTIFACT_SCHEMA,
            artifact_schema_version=artifact_cls.ARTIFACT_SCHEMA_VERSION,
            producer_stage=artifact_cls.PRODUCER_STAGE,
            consumer_stages=tuple(artifact_cls.CONSUMER_STAGES),
            cardinality=cardinality,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction.value,
            "artifact_type": self.artifact_type,
            "artifact_schema": self.artifact_schema,
            "artifact_schema_version": self.artifact_schema_version,
            "producer_stage": self.producer_stage,
            "consumer_stages": list(self.consumer_stages),
            "cardinality": self.cardinality.to_dict(),
        }


@dataclass(frozen=True)
class StageArtifactDeclaration:
    """The immutable, ordered input/output artifact contract for one stage."""

    stage_name: str
    inputs: tuple[StageArtifactSpec, ...] = ()
    outputs: tuple[StageArtifactSpec, ...] = ()

    def inputs_for_type(self, artifact_type: str) -> StageArtifactSpec | None:
        return next((s for s in self.inputs if s.artifact_type == artifact_type), None)

    def outputs_for_type(self, artifact_type: str) -> StageArtifactSpec | None:
        return next((s for s in self.outputs if s.artifact_type == artifact_type), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage_name,
            "inputs": [s.to_dict() for s in self.inputs],
            "outputs": [s.to_dict() for s in self.outputs],
        }


@dataclass(frozen=True)
class ReferenceOutcome:
    """The validation outcome for a single supplied reference.

    ``status`` is ``VALID`` or ``INVALID`` (a supplied ref always exists, so it
    is never ``MISSING``). Issues carry the stable codes and locations.
    """

    direction: ArtifactDirection
    artifact_type: str
    ref: ArtifactRef
    status: ArtifactValidationStatus
    issues: tuple[ValidationIssue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction.value,
            "artifact_type": self.artifact_type,
            "status": self.status.value,
            "ref": self.ref.to_dict(),
            "issues": [i.to_dict() for i in self.issues],
        }


@dataclass(frozen=True)
class SlotOutcome:
    """The validation outcome for one declared spec (a "slot").

    A slot can hold zero or more supplied references. ``status`` is ``MISSING``
    when a required slot got no references, ``INVALID`` on a cardinality
    violation or any invalid child reference, else ``VALID``. Per-reference
    detail lives in ``references``; cardinality/missing issues live in
    ``issues`` at this level.
    """

    direction: ArtifactDirection
    spec: StageArtifactSpec
    status: ArtifactValidationStatus
    references: tuple[ReferenceOutcome, ...] = ()
    issues: tuple[ValidationIssue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction.value,
            "artifact_type": self.spec.artifact_type,
            "status": self.status.value,
            "cardinality": self.spec.cardinality.to_dict(),
            "references": [r.to_dict() for r in self.references],
            "issues": [i.to_dict() for i in self.issues],
        }


@dataclass(frozen=True)
class StageArtifactValidationResult:
    """Public, Prompt-23-ready result of validating refs against a stage.

    Preserves every slot and unexpected-reference outcome, distinguishes
    ``errors`` from compatibility ``warnings``, and never upgrades unknown
    metadata into a passing compatibility claim.
    """

    stage_name: str
    slots: tuple[SlotOutcome, ...] = ()
    unexpected: tuple[ReferenceOutcome, ...] = ()

    @property
    def issues(self) -> tuple[ValidationIssue, ...]:
        """All issues, in deterministic order: per slot (slot-level then each
        reference), followed by unexpected references."""
        collected: list[ValidationIssue] = []
        for slot in self.slots:
            collected.extend(slot.issues)
            for ref_outcome in slot.references:
                collected.extend(ref_outcome.issues)
        for ref_outcome in self.unexpected:
            collected.extend(ref_outcome.issues)
        return tuple(collected)

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(i for i in self.issues if i.severity is ValidationSeverity.ERROR)

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        return tuple(i for i in self.issues if i.severity is ValidationSeverity.WARNING)

    @property
    def ok(self) -> bool:
        """True when there are no ERROR issues (warning-only results are ``ok``)."""
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage_name,
            "ok": self.ok,
            "slots": [s.to_dict() for s in self.slots],
            "unexpected": [r.to_dict() for r in self.unexpected],
        }


# --------------------------------------------------------------------------- #
# Registry                                                                    #
# --------------------------------------------------------------------------- #

# Declared consume/produce matrix. Every canonical stage appears exactly once;
# stages with no formalized artifact contract declare empty tuples rather than
# inventing an artifact. Specs are derived from the accepted artifact classes.
_C = Cardinality
_STAGE_MATRIX: dict[str, tuple[list[tuple[Any, ArtifactDirection, Cardinality]],
                               list[tuple[Any, ArtifactDirection, Cardinality]]]] = {
    STAGE_MATERIALIZE: ([], []),
    STAGE_INTAKE: (
        [],
        [(StoryArtifact, ArtifactDirection.OUTPUT, _C.EXACTLY_ONE)],
    ),
    STAGE_TASK_GENERATION: (
        [(StoryArtifact, ArtifactDirection.INPUT, _C.EXACTLY_ONE)],
        [(TaskPlanArtifact, ArtifactDirection.OUTPUT, _C.EXACTLY_ONE)],
    ),
    STAGE_TASK_ASSIGNMENT: (
        [
            (StoryArtifact, ArtifactDirection.INPUT, _C.EXACTLY_ONE),
            (TaskPlanArtifact, ArtifactDirection.INPUT, _C.EXACTLY_ONE),
        ],
        [
            (AssignmentArtifact, ArtifactDirection.OUTPUT, _C.EXACTLY_ONE),
            (UowSpecArtifact, ArtifactDirection.OUTPUT, _C.ONE_OR_MORE),
        ],
    ),
    STAGE_EXECUTION: (
        [
            (AssignmentArtifact, ArtifactDirection.INPUT, _C.EXACTLY_ONE),
            (UowSpecArtifact, ArtifactDirection.INPUT, _C.ONE_OR_MORE),
        ],
        [(ImplementationReportArtifact, ArtifactDirection.OUTPUT, _C.ONE_OR_MORE)],
    ),
    STAGE_QA: (
        [
            (StoryArtifact, ArtifactDirection.INPUT, _C.EXACTLY_ONE),
            (TaskPlanArtifact, ArtifactDirection.INPUT, _C.EXACTLY_ONE),
            (AssignmentArtifact, ArtifactDirection.INPUT, _C.EXACTLY_ONE),
            (ImplementationReportArtifact, ArtifactDirection.INPUT, _C.ONE_OR_MORE),
        ],
        [(QAReportArtifact, ArtifactDirection.OUTPUT, _C.EXACTLY_ONE)],
    ),
    STAGE_PR_REVIEW: (
        [
            (StoryArtifact, ArtifactDirection.INPUT, _C.EXACTLY_ONE),
            (TaskPlanArtifact, ArtifactDirection.INPUT, _C.EXACTLY_ONE),
            (AssignmentArtifact, ArtifactDirection.INPUT, _C.EXACTLY_ONE),
            (ImplementationReportArtifact, ArtifactDirection.INPUT, _C.ONE_OR_MORE),
            (QAReportArtifact, ArtifactDirection.INPUT, _C.EXACTLY_ONE),
        ],
        [],
    ),
}


def _build_declaration(
    stage_name: str,
    input_specs: list[tuple[Any, ArtifactDirection, Cardinality]],
    output_specs: list[tuple[Any, ArtifactDirection, Cardinality]],
) -> StageArtifactDeclaration:
    inputs: list[StageArtifactSpec] = []
    outputs: list[StageArtifactSpec] = []
    seen_input: set[str] = set()
    seen_output: set[str] = set()

    for artifact_cls, direction, cardinality in input_specs:
        spec = StageArtifactSpec.from_artifact(artifact_cls, direction, cardinality)
        if spec.artifact_type in seen_input:
            raise StageArtifactRegistryError(
                f"duplicate input spec {spec.artifact_type!r} for stage {stage_name!r}"
            )
        # Parity: a consumed artifact must list this stage among its consumers.
        if stage_name not in spec.consumer_stages:
            raise StageArtifactRegistryError(
                f"stage {stage_name!r} consumes {spec.artifact_type!r} but is not "
                f"in its canonical consumer_stages {spec.consumer_stages}"
            )
        seen_input.add(spec.artifact_type)
        inputs.append(spec)

    for artifact_cls, direction, cardinality in output_specs:
        spec = StageArtifactSpec.from_artifact(artifact_cls, direction, cardinality)
        if spec.artifact_type in seen_output:
            raise StageArtifactRegistryError(
                f"duplicate output spec {spec.artifact_type!r} for stage {stage_name!r}"
            )
        # Parity: a produced artifact's canonical producer must be this stage.
        if spec.producer_stage != stage_name:
            raise StageArtifactRegistryError(
                f"stage {stage_name!r} produces {spec.artifact_type!r} but its "
                f"canonical producer_stage is {spec.producer_stage!r}"
            )
        seen_output.add(spec.artifact_type)
        outputs.append(spec)

    return StageArtifactDeclaration(
        stage_name=stage_name, inputs=tuple(inputs), outputs=tuple(outputs)
    )


def _build_registry() -> dict[str, StageArtifactDeclaration]:
    registry: dict[str, StageArtifactDeclaration] = {}
    for stage_name in WORKFLOW_STAGES:
        if stage_name in registry:
            raise StageArtifactRegistryError(
                f"duplicate stage declaration {stage_name!r}"
            )
        input_specs, output_specs = _STAGE_MATRIX[stage_name]
        registry[stage_name] = _build_declaration(stage_name, input_specs, output_specs)
    # Every canonical stage must be covered exactly once, and no extras.
    if set(registry) != set(WORKFLOW_STAGES):
        raise StageArtifactRegistryError(
            "stage-artifact registry does not cover the canonical stages exactly"
        )
    return registry


#: The canonical stage-artifact registry, validated at import time.
STAGE_ARTIFACT_REGISTRY: Mapping[str, StageArtifactDeclaration] = _build_registry()


def get_stage_declaration(stage_name: str) -> StageArtifactDeclaration:
    """Return the declaration for ``stage_name`` or raise :class:`UnknownStageError`."""
    try:
        return STAGE_ARTIFACT_REGISTRY[stage_name]
    except KeyError:
        raise UnknownStageError(stage_name, tuple(STAGE_ARTIFACT_REGISTRY)) from None


# --------------------------------------------------------------------------- #
# Validation                                                                  #
# --------------------------------------------------------------------------- #


def _issue(code: str, message: str, severity: ValidationSeverity, location: str) -> ValidationIssue:
    return ValidationIssue(code=code, message=message, severity=severity, location=location)


def _check_ref(
    stage_name: str,
    spec: StageArtifactSpec,
    ref: ArtifactRef,
) -> list[ValidationIssue]:
    """Metadata-only checks for one type-matched reference. No I/O."""
    issues: list[ValidationIssue] = []
    loc = f"{spec.direction.value}.{spec.artifact_type}"

    # Schema / schema version (both-or-neither on ArtifactRef).
    if ref.artifact_schema is None:
        issues.append(
            _issue(
                WARNING_UNKNOWN_SCHEMA,
                f"reference for {spec.artifact_type!r} declares no artifact_schema",
                ValidationSeverity.WARNING,
                loc,
            )
        )
    else:
        if ref.artifact_schema != spec.artifact_schema:
            issues.append(
                _issue(
                    ISSUE_SCHEMA_MISMATCH,
                    f"artifact_schema {ref.artifact_schema!r} != expected "
                    f"{spec.artifact_schema!r}",
                    ValidationSeverity.ERROR,
                    loc,
                )
            )
        if (
            ref.artifact_schema_version is not None
            and ref.artifact_schema_version != spec.artifact_schema_version
        ):
            issues.append(
                _issue(
                    ISSUE_SCHEMA_VERSION_MISMATCH,
                    f"artifact_schema_version {ref.artifact_schema_version!r} != "
                    f"expected {spec.artifact_schema_version!r}",
                    ValidationSeverity.ERROR,
                    loc,
                )
            )

    # Producer / consumer compatibility is direction-specific.
    if spec.direction is ArtifactDirection.INPUT:
        expected_producer = spec.producer_stage
        if ref.producer_stage is None:
            issues.append(
                _issue(
                    WARNING_UNKNOWN_PRODUCER,
                    f"reference for {spec.artifact_type!r} declares no producer_stage",
                    ValidationSeverity.WARNING,
                    loc,
                )
            )
        elif ref.producer_stage != expected_producer:
            issues.append(
                _issue(
                    ISSUE_WRONG_PRODUCER,
                    f"producer_stage {ref.producer_stage!r} != expected "
                    f"{expected_producer!r}",
                    ValidationSeverity.ERROR,
                    loc,
                )
            )
        if ref.consumer_stages is None:
            issues.append(
                _issue(
                    WARNING_UNKNOWN_CONSUMER,
                    f"reference for {spec.artifact_type!r} declares no consumer_stages",
                    ValidationSeverity.WARNING,
                    loc,
                )
            )
        elif stage_name not in ref.consumer_stages:
            issues.append(
                _issue(
                    ISSUE_INCOMPATIBLE_CONSUMER,
                    f"consuming stage {stage_name!r} not in reference "
                    f"consumer_stages {tuple(ref.consumer_stages)}",
                    ValidationSeverity.ERROR,
                    loc,
                )
            )
    else:  # OUTPUT
        if ref.producer_stage is None:
            issues.append(
                _issue(
                    WARNING_UNKNOWN_PRODUCER,
                    f"reference for {spec.artifact_type!r} declares no producer_stage",
                    ValidationSeverity.WARNING,
                    loc,
                )
            )
        elif ref.producer_stage != stage_name:
            issues.append(
                _issue(
                    ISSUE_WRONG_PRODUCER,
                    f"producer_stage {ref.producer_stage!r} != producing stage "
                    f"{stage_name!r}",
                    ValidationSeverity.ERROR,
                    loc,
                )
            )
        if ref.consumer_stages is None:
            issues.append(
                _issue(
                    WARNING_UNKNOWN_CONSUMER,
                    f"reference for {spec.artifact_type!r} declares no consumer_stages",
                    ValidationSeverity.WARNING,
                    loc,
                )
            )
        elif tuple(ref.consumer_stages) != tuple(spec.consumer_stages):
            issues.append(
                _issue(
                    ISSUE_INCOMPATIBLE_CONSUMER,
                    f"consumer_stages {tuple(ref.consumer_stages)} != canonical "
                    f"{tuple(spec.consumer_stages)}",
                    ValidationSeverity.ERROR,
                    loc,
                )
            )

    return issues


def _status_for(issues: Iterable[ValidationIssue]) -> ArtifactValidationStatus:
    has_error = any(i.severity is ValidationSeverity.ERROR for i in issues)
    return ArtifactValidationStatus.INVALID if has_error else ArtifactValidationStatus.VALID


def _validate_direction(
    stage_name: str,
    direction: ArtifactDirection,
    specs: tuple[StageArtifactSpec, ...],
    refs: tuple[ArtifactRef, ...],
) -> tuple[tuple[SlotOutcome, ...], tuple[ReferenceOutcome, ...]]:
    specs_by_type = {s.artifact_type: s for s in specs}
    refs_by_type: dict[str, list[ArtifactRef]] = {}
    for ref in refs:
        refs_by_type.setdefault(ref.artifact_type, []).append(ref)

    slots: list[SlotOutcome] = []
    for spec in specs:
        loc = f"{spec.direction.value}.{spec.artifact_type}"
        matched = refs_by_type.get(spec.artifact_type, [])
        ref_outcomes = tuple(
            ReferenceOutcome(
                direction=spec.direction,
                artifact_type=spec.artifact_type,
                ref=ref,
                status=_status_for(issues := _check_ref(stage_name, spec, ref)),
                issues=tuple(issues),
            )
            for ref in matched
        )
        count = len(matched)
        slot_issues: list[ValidationIssue] = []
        if count == 0:
            if spec.cardinality.required:
                slot_issues.append(
                    _issue(
                        ISSUE_MISSING_REQUIRED,
                        f"required {spec.artifact_type!r} ({spec.cardinality.label}) "
                        f"not supplied to stage {stage_name!r}",
                        ValidationSeverity.ERROR,
                        loc,
                    )
                )
                slot_status = ArtifactValidationStatus.MISSING
            else:
                slot_status = ArtifactValidationStatus.VALID
        elif not spec.cardinality.allows(count):
            slot_issues.append(
                _issue(
                    ISSUE_CARDINALITY_TOO_MANY,
                    f"{count} {spec.artifact_type!r} references exceed "
                    f"{spec.cardinality.label}",
                    ValidationSeverity.ERROR,
                    loc,
                )
            )
            slot_status = ArtifactValidationStatus.INVALID
        elif any(r.status is ArtifactValidationStatus.INVALID for r in ref_outcomes):
            slot_status = ArtifactValidationStatus.INVALID
        else:
            slot_status = ArtifactValidationStatus.VALID

        slots.append(
            SlotOutcome(
                direction=spec.direction,
                spec=spec,
                status=slot_status,
                references=ref_outcomes,
                issues=tuple(slot_issues),
            )
        )

    unexpected: list[ReferenceOutcome] = []
    for ref in refs:
        if ref.artifact_type in specs_by_type:
            continue
        loc = f"{direction.value}.{ref.artifact_type}"
        unexpected.append(
            ReferenceOutcome(
                direction=direction,
                artifact_type=ref.artifact_type,
                ref=ref,
                status=ArtifactValidationStatus.INVALID,
                issues=(
                    _issue(
                        ISSUE_UNEXPECTED_TYPE,
                        f"stage {stage_name!r} declares no {direction.value} "
                        f"artifact of type {ref.artifact_type!r}",
                        ValidationSeverity.ERROR,
                        loc,
                    ),
                ),
            )
        )
    return tuple(slots), tuple(unexpected)


def validate_stage_artifacts(
    stage: str | StageArtifactDeclaration,
    *,
    inputs: Iterable[ArtifactRef] = (),
    outputs: Iterable[ArtifactRef] = (),
) -> StageArtifactValidationResult:
    """Validate supplied references against a stage's declaration.

    Metadata-only and deterministic: no filesystem, subprocess, or network
    access, no checksum computation, no fabricated validation metadata. Missing
    required slots and present-but-invalid references are distinguished; unknown
    (``None``) compatibility metadata yields a warning, never an error.
    """
    declaration = (
        stage if isinstance(stage, StageArtifactDeclaration)
        else get_stage_declaration(stage)
    )
    input_refs = tuple(inputs)
    output_refs = tuple(outputs)

    input_slots, input_unexpected = _validate_direction(
        declaration.stage_name, ArtifactDirection.INPUT, declaration.inputs, input_refs
    )
    output_slots, output_unexpected = _validate_direction(
        declaration.stage_name, ArtifactDirection.OUTPUT, declaration.outputs, output_refs
    )

    return StageArtifactValidationResult(
        stage_name=declaration.stage_name,
        slots=input_slots + output_slots,
        unexpected=input_unexpected + output_unexpected,
    )
