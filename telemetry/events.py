"""Versioned trace-event contract (Trace Contract v1).

This module defines the *shape* of a single trace event — a small, versioned,
JSON-serializable record describing one thing that happened during a run
(the run itself starting/ending, a stage, an agent invocation, an artifact,
a test). It does not decide how events get persisted; see `event_sink.py`
for that.

Design rules (see `docs/tracing.md` for the full rationale):

- Required fields (`event_schema_version`, `event_type`, `timestamp`,
  `run_id`) are validated eagerly in `__post_init__`; a `TraceValidationError`
  names every offending field in one pass.
- Everything else is optional and defaults to `None`. Absent means
  "unknown" — token counts, cost, and duration are never silently coerced
  to `0`.
- `event_schema_version` is checked against `SUPPORTED_SCHEMA_VERSIONS`.
  An event carrying an unsupported version is rejected rather than parsed
  as if it matched the current contract.
- `timestamp` must be a timezone-aware UTC ISO-8601 string, not merely a
  nonempty string.
- This module imports nothing from `server`, `core.opik*`, or the `opik`
  package — the contract has zero optional-vendor dependencies.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

TRACE_SCHEMA_VERSION = "1"
SUPPORTED_SCHEMA_VERSIONS = frozenset({TRACE_SCHEMA_VERSION})


class EventType(str, Enum):
    """Centralized, testable set of supported trace-event families."""

    RUN_STARTED = "run.started"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    STAGE_STARTED = "stage.started"
    STAGE_COMPLETED = "stage.completed"
    STAGE_FAILED = "stage.failed"
    AGENT_INVOCATION_STARTED = "agent.invocation.started"
    AGENT_INVOCATION_COMPLETED = "agent.invocation.completed"
    AGENT_INVOCATION_FAILED = "agent.invocation.failed"
    AGENT_INVOCATION_TIMED_OUT = "agent.invocation.timed_out"
    ARTIFACT_CREATED = "artifact.created"
    ARTIFACT_VALIDATED = "artifact.validated"
    ARTIFACT_INVALID = "artifact.invalid"
    TEST_STARTED = "test.started"
    TEST_COMPLETED = "test.completed"
    TEST_FAILED = "test.failed"


class EventStatus(str, Enum):
    """Small, optional status vocabulary for the `status` field."""

    OK = "ok"
    ERROR = "error"
    SKIPPED = "skipped"


class TraceValidationError(Exception):
    """Raised when a `TraceEvent` fails structural validation.

    Carries every problem found (not just the first) so callers see the
    full set of offending fields in one pass, mirroring
    `eval.report_schema.ReportValidationError`.
    """

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors) if self.errors else "invalid trace event")


class MetadataSerializationError(ValueError):
    """Raised when event `metadata` contains a value `json` cannot encode.

    Defined once, here, so both event construction/serialization and the
    JSONL sink can raise/catch the same type without either module
    importing the other in a cycle (`event_sink.py` imports this from
    `events.py`, never the reverse).
    """


_JSON_SCALAR_TYPES = (str, int, float, bool, type(None))


def _ensure_jsonable(value: Any, *, where: str) -> None:
    """Recursively verify `value` is made only of JSON-compatible types.

    Never stringifies unsupported values as a fallback — raises
    `MetadataSerializationError` naming the offending location instead.
    """
    if isinstance(value, _JSON_SCALAR_TYPES):
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise MetadataSerializationError(
                    f"{where}: metadata dict keys must be strings, got {type(key).__name__}"
                )
            _ensure_jsonable(item, where=f"{where}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _ensure_jsonable(item, where=f"{where}[{index}]")
        return
    raise MetadataSerializationError(
        f"{where}: value of type {type(value).__name__} is not JSON-serializable"
    )


def new_run_id() -> str:
    """Return a nonempty, unique run identifier."""
    return f"run-{uuid.uuid4().hex}"


def new_span_id() -> str:
    """Return a nonempty, unique span identifier.

    Callers pass the returned value as a child event's `span_id` and the
    parent's `span_id` as that child's `parent_span_id` to represent
    nesting. This is intentionally not a full distributed-tracing
    implementation — just enough to represent run/stage/child relationships.
    """
    return f"span-{uuid.uuid4().hex}"


def _utcnow_iso() -> str:
    """Return the current time as a timezone-aware UTC ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _validate_utc_timestamp(value: Any) -> bool:
    """Return True iff `value` is a timezone-aware UTC ISO-8601 string."""
    if not isinstance(value, str) or not value:
        return False
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return False
    if parsed.tzinfo is None:
        return False
    return parsed.utcoffset() == timedelta(0)


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and len(value) > 0


_OPTIONAL_STR_FIELDS = (
    "span_id",
    "parent_span_id",
    "stage",
    "agent",
    "runner",
    "model",
    "artifact_path",
    "prompt_sha256",
    "response_sha256",
    "error_type",
    "error_message",
)
_OPTIONAL_NUMERIC_FIELDS = (
    ("duration_ms", (int, float)),
    ("tokens_in", (int,)),
    ("tokens_out", (int,)),
    ("cost_usd", (int, float)),
)


@dataclass(kw_only=True)
class TraceEvent:
    """A single, versioned trace event.

    Required: `event_schema_version`, `event_type`, `timestamp`, `run_id`.
    Everything else defaults to `None` (unknown/not applicable) and is
    omitted from the serialized form — see `to_dict`.
    """

    event_type: EventType | str
    timestamp: str
    run_id: str
    event_schema_version: str = TRACE_SCHEMA_VERSION

    span_id: str | None = None
    parent_span_id: str | None = None
    stage: str | None = None
    agent: str | None = None
    runner: str | None = None
    model: str | None = None
    status: EventStatus | str | None = None
    duration_ms: float | None = None
    artifact_path: str | None = None
    prompt_sha256: str | None = None
    response_sha256: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    error_type: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        errors: list[str] = []

        if self.event_schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            errors.append(
                f"event_schema_version: unsupported value {self.event_schema_version!r} "
                f"(supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)})"
            )

        try:
            self.event_type = EventType(self.event_type)
        except ValueError:
            errors.append(f"event_type: unknown event type {self.event_type!r}")

        if not _validate_utc_timestamp(self.timestamp):
            errors.append(
                f"timestamp: must be a timezone-aware UTC ISO-8601 string, got {self.timestamp!r}"
            )

        if not _nonempty_str(self.run_id):
            errors.append(f"run_id: must be a nonempty string, got {self.run_id!r}")

        if self.status is not None:
            try:
                self.status = EventStatus(self.status)
            except ValueError:
                errors.append(f"status: unknown status {self.status!r}")

        for name in _OPTIONAL_STR_FIELDS:
            value = getattr(self, name)
            if value is not None and not _nonempty_str(value):
                errors.append(f"{name}: must be a nonempty string or None, got {value!r}")

        for name, types in _OPTIONAL_NUMERIC_FIELDS:
            value = getattr(self, name)
            if value is not None and (not isinstance(value, types) or isinstance(value, bool)):
                errors.append(f"{name}: must be a number or None, got {value!r}")

        if not isinstance(self.metadata, dict):
            errors.append(f"metadata: must be a dict, got {type(self.metadata).__name__}")

        if errors:
            raise TraceValidationError(errors)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict, omitting unset optional fields.

        Raises `MetadataSerializationError` (naming this event's `run_id`/
        `event_type`/`span_id`) if `metadata` contains a value `json`
        cannot encode. Callers (notably the JSONL sink) should call this
        to build the *complete* line before writing anything, so a
        serialization failure can never produce a partial line.
        """
        result: dict[str, Any] = {
            "event_schema_version": self.event_schema_version,
            "event_type": EventType(self.event_type).value,
            "timestamp": self.timestamp,
            "run_id": self.run_id,
        }
        for f in fields(self):
            name = f.name
            if name in ("event_schema_version", "event_type", "timestamp", "run_id", "metadata"):
                continue
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, Enum):
                value = value.value
            result[name] = value
        if self.metadata:
            where = (
                f"TraceEvent(run_id={self.run_id!r}, event_type={result['event_type']!r}, "
                f"span_id={self.span_id!r}) metadata"
            )
            _ensure_jsonable(self.metadata, where=where)
            result["metadata"] = self.metadata
        return result

    def to_json(self) -> str:
        """Serialize to a single, deterministic JSON line (sorted keys)."""
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TraceEvent":
        """Reconstruct a `TraceEvent` from a plain dict.

        Unknown top-level keys are ignored (forward-compatibility across
        future schema versions) rather than rejected or silently kept as
        attributes — put anything custom in `metadata` instead.
        """
        errors = cls.validate_payload(data, where="TraceEvent")
        if errors:
            raise TraceValidationError(errors)
        known_names = {f.name for f in fields(cls)}
        kwargs = {key: value for key, value in data.items() if key in known_names}
        return cls(**kwargs)

    @staticmethod
    def validate_payload(data: Any, where: str) -> list[str]:
        """Structural validation of a raw payload, without constructing."""
        errors: list[str] = []
        if not isinstance(data, dict):
            return [f"{where}: expected a dict, got {type(data).__name__}"]
        for required in ("event_type", "timestamp", "run_id"):
            if required not in data:
                errors.append(f"{where}.{required}: required field is missing")
        return errors


def make_event(
    event_type: EventType | str,
    run_id: str,
    *,
    timestamp: str | None = None,
    span_id: str | None = None,
    parent_span_id: str | None = None,
    stage: str | None = None,
    agent: str | None = None,
    runner: str | None = None,
    model: str | None = None,
    status: EventStatus | str | None = None,
    duration_ms: float | None = None,
    artifact_path: str | None = None,
    prompt_sha256: str | None = None,
    response_sha256: str | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    cost_usd: float | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> TraceEvent:
    """Construct a `TraceEvent` for any of the 16 supported event families.

    One generic helper rather than sixteen event-specific factories:
    `event_type` selects the family and every other field stays optional.
    `timestamp` defaults to "now" (UTC); tests should pass an explicit
    value for determinism.
    """
    return TraceEvent(
        event_type=event_type,
        timestamp=timestamp if timestamp is not None else _utcnow_iso(),
        run_id=run_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        stage=stage,
        agent=agent,
        runner=runner,
        model=model,
        status=status,
        duration_ms=duration_ms,
        artifact_path=artifact_path,
        prompt_sha256=prompt_sha256,
        response_sha256=response_sha256,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost_usd,
        error_type=error_type,
        error_message=error_message,
        metadata=dict(metadata) if metadata is not None else {},
    )
