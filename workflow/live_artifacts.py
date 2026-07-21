"""Prompt 23 — live-workflow artifact collector.

Adopts the Prompt-22 typed-artifact contracts at real `run.py` stage
boundaries. `LiveArtifactCollector` resolves canonical artifact paths under a
bound `change_root`, loads/validates them through the typed payload loaders
(`artifacts.payloads`), mints real `ArtifactRef`s, and emits Prompt-22
lifecycle events (`artifact.created`/`validated`/`invalid`/`missing`) through
`workflow.artifact_lifecycle.emit_stage_artifact_lifecycle` — the sole
telemetry-emitting boundary, unchanged by this module.

Design constraints (Prompt 23 plan, reviewer-amended):

- No orchestration rewrite. `run.py` calls exactly three methods (`bind`,
  `emit_stage_inputs`, `register_stage_outputs`) at existing stage
  boundaries; none of them can change the legacy workflow's result or a
  propagated exception (every public method swallows unexpected errors).
- **A1**: the two per-UoW-identity output types (`UowSpecArtifact`,
  `ImplementationReportArtifact`) are validated **once per expected uow_id**
  via a fresh single-slot `EXACTLY_ONE` declaration, so one missing sibling
  never taints another present sibling's `artifact.validated`/`invalid`.
- **A2**: the ordered uow_ids driving that per-UoW iteration come from the
  same permissive, non-raising path `run.py` itself already trusts
  (`core.artifact_utils.load_assignments_file`) — decoupled from the typed
  `AssignmentArtifact` ref's own validation, so a typed-invalid assignment
  never blinds downstream per-UoW discovery.
- This module is a leaf within `workflow`: it imports `artifacts`,
  `workflow.stage_artifacts`, `workflow.artifact_lifecycle`, `telemetry` (for
  typing only), and `core.workflow_constants` / `core.artifact_utils` — never
  `run`, `server`, or `opik` at module level.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from artifacts import (
    ArtifactLoadError,
    ArtifactRef,
    ArtifactValidationError,
    ArtifactValidationStatus,
    AssignmentArtifact,
    ImplementationReportArtifact,
    QAReportArtifact,
    StoryArtifact,
    TaskPlanArtifact,
    UowSpecArtifact,
)
from core.artifact_utils import load_assignments_file
from core.workflow_constants import (
    ASSIGNMENTS_KEY_BATCH_ID,
    ASSIGNMENTS_KEY_BATCHES,
    ASSIGNMENTS_KEY_UOW_ID,
    ASSIGNMENTS_KEY_UOWS,
    STAGE_EXECUTION,
    STAGE_INTAKE,
    STAGE_QA,
    STAGE_TASK_ASSIGNMENT,
    STAGE_TASK_GENERATION,
)
from telemetry import EventSink

from .artifact_lifecycle import emit_stage_artifact_lifecycle
from .stage_artifacts import (
    ArtifactDirection,
    Cardinality,
    StageArtifactDeclaration,
    StageArtifactSpec,
    get_stage_declaration,
)

__all__ = ["LiveArtifactCollector"]

# Single-instance payload classes produced at each stage (excludes the
# per-UoW types, which A1 handles via a dedicated per-identity loop).
_SINGLE_OUTPUT_STAGES: dict[str, tuple[type, ...]] = {
    STAGE_INTAKE: (StoryArtifact,),
    STAGE_TASK_GENERATION: (TaskPlanArtifact,),
    STAGE_TASK_ASSIGNMENT: (AssignmentArtifact,),
    STAGE_QA: (QAReportArtifact,),
}

# Per-UoW-identity payload classes: one `EXACTLY_ONE` emission per expected
# uow_id (A1), keyed by the stage that produces them.
_PER_UOW_OUTPUT_STAGES: dict[str, type] = {
    STAGE_TASK_ASSIGNMENT: UowSpecArtifact,
    STAGE_EXECUTION: ImplementationReportArtifact,
}


def _dedup_key(ref: ArtifactRef) -> tuple[str, str, str | None]:
    location = str(ref.path) if ref.path is not None else str(ref.uri)
    return (ref.artifact_type, location, ref.producer_stage)


class LiveArtifactCollector:
    """Discovers, validates, and registers real workflow artifacts.

    Instantiated fresh per run (mirrors `RunContext`); never shared between
    runs. Every public method is a no-throw boundary: unexpected errors are
    swallowed so artifact instrumentation can never change the legacy
    workflow's outcome.
    """

    def __init__(self, *, run_id: str, sink: EventSink | None = None) -> None:
        self._run_id = run_id
        self._sink = sink
        self._change_root: Path | None = None
        self._refs: list[ArtifactRef] = []
        self._seen: set[tuple[str, str, str | None]] = set()
        self._refs_by_type: dict[str, list[ArtifactRef]] = {}
        self._uow_ids: tuple[str, ...] | None = None
        self._emitted_input_stages: set[str] = set()

    # ------------------------------------------------------------------
    # Public no-throw boundary.
    # ------------------------------------------------------------------

    def bind(self, change_root: Path) -> None:
        """Bind the collector to `AGENT_CONTEXT_ROOT/{change_id}`."""
        try:
            self._change_root = Path(change_root)
        except Exception:  # noqa: BLE001 - never break the legacy workflow
            self._change_root = None

    @property
    def references(self) -> tuple[ArtifactRef, ...]:
        return tuple(self._refs)

    def emit_stage_inputs(self, stage: str) -> None:
        """Emit lifecycle events for `stage`'s already-registered inputs.

        Idempotent per stage: a second call for the same stage is a no-op, so
        a legacy stage helper that is invoked more than once per run never
        double-emits input events.
        """
        try:
            self._emit_stage_inputs(stage)
        except Exception:  # noqa: BLE001 - no-throw hook boundary
            pass

    def register_stage_outputs(self, stage: str) -> None:
        """Discover, validate, and register `stage`'s produced outputs."""
        try:
            self._register_stage_outputs(stage)
        except Exception:  # noqa: BLE001 - no-throw hook boundary
            pass

    # ------------------------------------------------------------------
    # Internals.
    # ------------------------------------------------------------------

    def _emit_stage_inputs(self, stage: str) -> None:
        if self._change_root is None or stage in self._emitted_input_stages:
            return
        self._emitted_input_stages.add(stage)

        declaration = get_stage_declaration(stage)
        input_types = {spec.artifact_type for spec in declaration.inputs}
        if not input_types:
            return
        matching = [ref for ref in self._refs if ref.artifact_type in input_types]

        input_only = StageArtifactDeclaration(stage_name=stage, inputs=declaration.inputs, outputs=())
        emit_stage_artifact_lifecycle(
            run_id=self._run_id,
            declaration=input_only,
            sink=self._sink,
            inputs=matching,
        )

    def _register_stage_outputs(self, stage: str) -> None:
        if self._change_root is None:
            return

        single_classes = _SINGLE_OUTPUT_STAGES.get(stage, ())
        single_specs: list[StageArtifactSpec] = []
        single_refs: list[ArtifactRef] = []
        for payload_cls in single_classes:
            path = self._resolve_path(payload_cls)
            ref = self._resolve_ref(payload_cls, path)
            if ref is not None:
                self._register(ref)
                single_refs.append(ref)
            single_specs.append(
                StageArtifactSpec.from_artifact(
                    payload_cls, ArtifactDirection.OUTPUT, Cardinality.EXACTLY_ONE
                )
            )

        if single_specs:
            declaration = StageArtifactDeclaration(
                stage_name=stage, inputs=(), outputs=tuple(single_specs)
            )
            emit_stage_artifact_lifecycle(
                run_id=self._run_id,
                declaration=declaration,
                sink=self._sink,
                outputs=single_refs,
            )

        per_uow_cls = _PER_UOW_OUTPUT_STAGES.get(stage)
        if per_uow_cls is not None:
            self._register_per_uow_outputs(stage, per_uow_cls)

    def _register_per_uow_outputs(self, stage: str, payload_cls: type) -> None:
        """A1: one `EXACTLY_ONE` emission per expected uow_id.

        A missing sibling never taints a present one — each uow_id gets its
        own declaration/validation/emission call.
        """
        spec = StageArtifactSpec.from_artifact(
            payload_cls, ArtifactDirection.OUTPUT, Cardinality.EXACTLY_ONE
        )
        declaration = StageArtifactDeclaration(stage_name=stage, inputs=(), outputs=(spec,))
        for uow_id in self._ordered_uow_ids():
            path = self._resolve_path(payload_cls, uow_id=uow_id)
            ref = self._resolve_ref(payload_cls, path)
            outputs: tuple[ArtifactRef, ...]
            if ref is not None:
                self._register(ref)
                outputs = (ref,)
            else:
                outputs = ()
            emit_stage_artifact_lifecycle(
                run_id=self._run_id,
                declaration=declaration,
                sink=self._sink,
                outputs=outputs,
            )

    def _register(self, ref: ArtifactRef) -> None:
        key = _dedup_key(ref)
        if key in self._seen:
            return
        self._seen.add(key)
        self._refs.append(ref)
        self._refs_by_type.setdefault(ref.artifact_type, []).append(ref)

    def _resolve_path(self, payload_cls: type, *, uow_id: str | None = None) -> Path:
        assert self._change_root is not None
        params: dict[str, str] = {"change_id": self._change_root.name}
        if uow_id is not None:
            params["uow_id"] = uow_id
        relative = payload_cls.RELATIVE_PATH_TEMPLATE.format(**params)
        # RELATIVE_PATH_TEMPLATE already begins with "{change_id}/...", and
        # `change_root` *is* the change_id directory, so strip that leading
        # segment rather than double it.
        _, _, tail = relative.partition("/")
        return self._change_root / tail

    def _resolve_ref(self, payload_cls: type, path: Path) -> ArtifactRef | None:
        """Load `path` through `payload_cls`, minting a ref that reflects the
        real outcome. Returns `None` only when the artifact is genuinely
        absent — cardinality then drives `artifact.missing` at the boundary.
        """
        if not path.exists():
            return None
        try:
            instance, result = payload_cls.load_with_validation(path)
        except ArtifactLoadError as exc:
            return ArtifactRef(
                artifact_type=payload_cls.ARTIFACT_TYPE,
                producer_stage=payload_cls.PRODUCER_STAGE,
                consumer_stages=payload_cls.CONSUMER_STAGES,
                artifact_schema=payload_cls.ARTIFACT_SCHEMA,
                artifact_schema_version=payload_cls.ARTIFACT_SCHEMA_VERSION,
                path=path,
                validation_status=ArtifactValidationStatus.INVALID,
                metadata={
                    "validation_source": "typed_loader",
                    "validation_issue_codes": ["load_error"],
                    "validation_message": str(exc),
                },
            )
        except ArtifactValidationError as exc:
            return ArtifactRef(
                artifact_type=payload_cls.ARTIFACT_TYPE,
                producer_stage=payload_cls.PRODUCER_STAGE,
                consumer_stages=payload_cls.CONSUMER_STAGES,
                artifact_schema=payload_cls.ARTIFACT_SCHEMA,
                artifact_schema_version=payload_cls.ARTIFACT_SCHEMA_VERSION,
                path=path,
                validation_status=ArtifactValidationStatus.INVALID,
                metadata={
                    "validation_source": "typed_loader",
                    "validation_issue_codes": [issue.code for issue in exc.errors],
                },
            )
        warning_codes = [issue.code for issue in result.warnings]
        metadata: dict[str, Any] = {"validation_source": "typed_loader"}
        if warning_codes:
            metadata["validation_issue_codes"] = warning_codes
        return instance.to_artifact_ref(
            path=path,
            validation_status=ArtifactValidationStatus.VALID,
            metadata=metadata,
        )

    def _ordered_uow_ids(self) -> tuple[str, ...]:
        """A2: permissive, non-raising uow_id discovery.

        Uses the same readable-legacy-shape path `run.py` itself trusts
        (`core.artifact_utils.load_assignments_file`), independent of the
        typed `AssignmentArtifact` ref's own validation outcome — so a
        typed-invalid assignment never blinds this discovery. Cached: called
        once at task-assignment registration, reused by execution.
        """
        if self._uow_ids is not None:
            return self._uow_ids
        uow_ids: list[str] = []
        try:
            assert self._change_root is not None
            path = self._resolve_path(AssignmentArtifact)
            if path.exists():
                data = load_assignments_file(path)
                batches = sorted(
                    data.get(ASSIGNMENTS_KEY_BATCHES, []),
                    key=lambda b: b.get(ASSIGNMENTS_KEY_BATCH_ID, 0),
                )
                for batch in batches:
                    for uow in batch.get(ASSIGNMENTS_KEY_UOWS, []):
                        uow_id = uow.get(ASSIGNMENTS_KEY_UOW_ID)
                        if uow_id:
                            uow_ids.append(uow_id)
        except Exception:  # noqa: BLE001 - permissive: never raises (A2)
            uow_ids = []
        self._uow_ids = tuple(uow_ids)
        return self._uow_ids
