"""Workflow-as-data: load, validate, and represent workflow definitions.

The runner still drives stage execution imperatively via the existing
`engine.py`, but the stage structure itself is now expressed as data in
`workflows/<id>.yaml`. The authoritative YAML (`standard.yaml`)
corresponds 1:1 to the stage sequence encoded in `stages.py` and
exercised by `engine.py`.

Downstream subsystems (harness, docs, validators) read workflows via
this module so they have a stable, version-addressable view of the
workflow shape without importing engine internals.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

_WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / "workflows"

# Optional dependency — only required when validation is requested.
try:  # pragma: no cover - exercised indirectly
    import jsonschema  # type: ignore
except Exception:  # pragma: no cover
    jsonschema = None  # type: ignore


@dataclass(frozen=True)
class WorkflowStage:
    id: str
    kind: Literal["single", "producer_evaluator_loop"]
    agent: str | None = None
    producer: str | None = None
    evaluator: str | None = None
    artifacts_in: tuple[str, ...] = ()
    artifacts_out: tuple[str, ...] = ()
    retry: dict[str, Any] = field(default_factory=dict)
    on_failure: str | None = None
    on_evaluator_reject: str | None = None


@dataclass(frozen=True)
class WorkflowDefinition:
    id: str
    version: int
    description: str
    defaults: dict[str, Any]
    stages: tuple[WorkflowStage, ...]
    escalation: dict[str, Any]
    artifacts: dict[str, dict[str, str]]
    source_path: Path | None = None

    def agent_refs(self) -> list[str]:
        """Return every unique `name@version` string referenced by stages."""
        refs: list[str] = []
        seen: set[str] = set()
        for stage in self.stages:
            for candidate in (stage.agent, stage.producer, stage.evaluator):
                if candidate and candidate not in seen:
                    seen.add(candidate)
                    refs.append(candidate)
        return refs


def _schema_path() -> Path | None:
    """Locate the shared workflow JSON Schema, if available."""
    candidate = (
        Path(__file__).resolve().parents[3]
        / "shared"
        / "agent_runner_shared"
        / "schemas"
        / "workflow.schema.json"
    )
    return candidate if candidate.exists() else None


def load_workflow_file(path: Path, *, validate: bool = True) -> WorkflowDefinition:
    """Parse a workflow YAML document into a WorkflowDefinition."""
    path = Path(path)
    raw_text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw_text)
    if not isinstance(data, dict):
        raise ValueError(f"Workflow file is not a mapping: {path}")
    if validate:
        schema_path = _schema_path()
        if schema_path and jsonschema is not None:
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            jsonschema.validate(instance=data, schema=schema)  # type: ignore[attr-defined]
    stages = tuple(
        WorkflowStage(
            id=s["id"],
            kind=s["kind"],
            agent=s.get("agent"),
            producer=s.get("producer"),
            evaluator=s.get("evaluator"),
            artifacts_in=tuple(s.get("artifacts_in") or ()),
            artifacts_out=tuple(s.get("artifacts_out") or ()),
            retry=dict(s.get("retry") or {}),
            on_failure=s.get("on_failure"),
            on_evaluator_reject=s.get("on_evaluator_reject"),
        )
        for s in data.get("stages") or ()
    )
    return WorkflowDefinition(
        id=data["id"],
        version=int(data["version"]),
        description=data.get("description", ""),
        defaults=dict(data.get("defaults") or {}),
        stages=stages,
        escalation=dict(data.get("escalation") or {}),
        artifacts=dict(data.get("artifacts") or {}),
        source_path=path,
    )


def load_workflow(workflow_id: str, *, validate: bool = True) -> WorkflowDefinition:
    """Load a workflow by id from the built-in workflows directory."""
    candidates = (
        _WORKFLOWS_DIR / f"{workflow_id}.yaml",
        _WORKFLOWS_DIR / f"{workflow_id}.yml",
    )
    for candidate in candidates:
        if candidate.exists():
            return load_workflow_file(candidate, validate=validate)
    raise FileNotFoundError(
        f"Workflow {workflow_id!r} not found under {_WORKFLOWS_DIR}"
    )
