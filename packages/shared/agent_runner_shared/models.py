"""Data models shared across runner, registry, and harness.

Pydantic v2 is used for validation and schema generation. Model schemas
back the JSON Schema documents in `agent_runner_shared/schemas/`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AgentRef(BaseModel):
    """Reference to a versioned agent bundle: name@version."""
    model_config = ConfigDict(frozen=True)

    name: str
    version: str

    @classmethod
    def parse(cls, ref: str) -> "AgentRef":
        if "@" not in ref:
            raise ValueError(f"Agent reference must be 'name@version': {ref!r}")
        name, version = ref.split("@", 1)
        if not name or not version:
            raise ValueError(f"Invalid agent ref (empty segment): {ref!r}")
        return cls(name=name, version=version)

    def __str__(self) -> str:
        return f"{self.name}@{self.version}"


class MaterializationManifest(BaseModel):
    """Record of which agent versions were materialized into a working copy."""
    agents: list[AgentRef]
    target_dir: str
    content_hashes: dict[str, str] = Field(default_factory=dict)
    materialized_at: str = Field(default_factory=_iso_now)


class AcceptanceCriterionKind(str):
    """Enumerable kinds for deterministic acceptance criteria."""
    FILE_EXISTS = "file_exists"
    SCRIPT = "script"
    EVENT_ASSERTION = "event_assertion"
    SCHEMA_VALID = "schema_valid"


class AcceptanceCriterion(BaseModel):
    """A single acceptance criterion, deterministic or rubric-based."""
    model_config = ConfigDict(protected_namespaces=())

    id: str
    description: str
    kind: Literal["file_exists", "script", "event_assertion", "schema_valid", "rubric"]
    # Deterministic-specific fields
    path: Optional[str] = None
    script: Optional[str] = None
    event: Optional[dict[str, Any]] = None
    schema_ref: Optional[str] = Field(default=None, alias="schema")
    # Rubric-specific fields
    scale: Optional[str] = None
    threshold: Optional[float] = None
    judge_prompt: Optional[str] = None


class TaskCalibration(BaseModel):
    """Calibration record for a task."""
    model: str
    runs: int
    target_pass_rate: float
    recorded_at: Optional[str] = None
    measured_pass_rate: Optional[float] = None
    band: Optional[dict[str, float]] = None


class SubstrateRef(BaseModel):
    """Reference into substrates manifest."""
    ref: str


class WorkflowRef(BaseModel):
    """Reference to a workflow definition by id + version."""
    id: str
    version: int


class Task(BaseModel):
    """A single task in the corpus."""
    id: str
    version: int = 1
    title: str
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    tags: list[str] = Field(default_factory=list)
    substrate: SubstrateRef
    workflow: WorkflowRef
    agents: list[str] = Field(default_factory=list)  # raw "name@ver" strings
    inputs: dict[str, Any] = Field(default_factory=dict)
    models: dict[str, str] = Field(default_factory=dict)
    seed: Optional[int] = None
    acceptance_criteria: dict[str, list[AcceptanceCriterion]] = Field(default_factory=dict)
    calibration: Optional[TaskCalibration] = None


class TaskVersion(BaseModel):
    """Summary of a task's version used in lineage."""
    id: str
    version: int
    content_hash: str


class BaselineBand(BaseModel):
    """Per-task pass-rate band with provenance."""
    task_id: str
    task_version: int
    low: float
    high: float
    mean: float
    sample_size: int
    established_at: str = Field(default_factory=_iso_now)
    judge_model: str
    reason: str = "initial_calibration"


class StageArtifact(BaseModel):
    """A single stage's produced artifact."""
    stage_id: str
    artifact_id: str
    path: str
    schema_ref: Optional[str] = None
    written_at: str = Field(default_factory=_iso_now)


class EventRecord(BaseModel):
    """A structured event emitted by the runner."""
    event_version: str = "1"
    ts: str = Field(default_factory=_iso_now)
    run_id: str
    stage: Optional[str] = None
    kind: str
    data: dict[str, Any] = Field(default_factory=dict)


class GradingRecord(BaseModel):
    """The complete grading result for a run."""
    run_id: str
    task_id: str
    task_version: int
    deterministic: list[dict[str, Any]] = Field(default_factory=list)
    rubric: list[dict[str, Any]] = Field(default_factory=list)
    overall_pass: bool
    reason: str = ""
    judge_model: str
    graded_at: str = Field(default_factory=_iso_now)


class RunLineage(BaseModel):
    """The full lineage header for a single run.

    This is the identity of a run. Two runs with identical lineage (modulo
    run_id and timestamp) are expected to produce identical outputs under
    cassette replay.
    """
    run_id: str
    cycle_id: Optional[str] = None
    started_at: str = Field(default_factory=_iso_now)
    runner_version: str
    workflow: WorkflowRef
    agent_versions: list[AgentRef]
    task_id: str
    task_version: int
    substrate_commit: str
    substrate_ref: str
    models: dict[str, str]
    judge_model: str
    cassette_mode: Literal["live", "record", "replay"]
    cassette_id: Optional[str] = None
    seed: Optional[int] = None
    mode: Literal["authoritative", "dev"] = "authoritative"
    container_image_digest: Optional[str] = None
    k: int = 1
    k_index: int = 0
