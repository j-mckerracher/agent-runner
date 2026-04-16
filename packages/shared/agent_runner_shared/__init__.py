"""Shared types, schemas, and contracts for the Agent Runner monorepo.

This package has no dependencies on the runner, registry, or harness
packages. All cross-cutting types live here so the three subsystems can
communicate through stable interfaces.
"""
from __future__ import annotations

__version__ = "0.1.0"

from .models import (
    AcceptanceCriterion,
    AgentRef,
    BaselineBand,
    EventRecord,
    GradingRecord,
    MaterializationManifest,
    RunLineage,
    StageArtifact,
    Task,
    TaskVersion,
)
from .events import EVENT_VERSION, emit_event_line, parse_event_line

__all__ = [
    "AcceptanceCriterion",
    "AgentRef",
    "BaselineBand",
    "EVENT_VERSION",
    "EventRecord",
    "GradingRecord",
    "MaterializationManifest",
    "RunLineage",
    "StageArtifact",
    "Task",
    "TaskVersion",
    "emit_event_line",
    "parse_event_line",
]
