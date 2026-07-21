"""Prompt 21 — TraceArtifact: typed loader + ArtifactRef adapter for JSONL traces.

Wraps a sequence of :class:`~telemetry.events.TraceEvent` objects parsed from
a ``trace.jsonl`` evidence file.  Follows the same ``load_with_validation`` /
``to_artifact_ref`` contract as the planning and report artifact adapters.

**No duplicate schema**: per-event structural validation is delegated entirely
to :meth:`~telemetry.events.TraceEvent.from_dict`; this module adds only the
JSONL-parsing loop, run-id consistency check, and the
:class:`~artifacts.models.ArtifactRef` factory.

Allowed import direction: ``telemetry`` → ``artifacts`` ✓ (artifacts is a
stdlib-only leaf).

Round-trip guarantee: :meth:`to_jsonl` produces output byte-identical to what
:class:`~telemetry.event_sink.JsonlEventSink` would write for the same events:
one sorted-keys JSON line per event followed by ``\\n``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from artifacts.models import ArtifactRef, ArtifactValidationStatus
from artifacts.validation import (
    ArtifactLoadError,
    ArtifactValidationError,
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
)
from telemetry.events import (
    TRACE_SCHEMA_VERSION,
    TraceEvent,
    TraceValidationError,
)


def _err(code: str, message: str, location: str | None = None) -> ValidationIssue:
    return ValidationIssue(
        code=code, message=message, severity=ValidationSeverity.ERROR, location=location
    )


class TraceArtifact:
    """Immutable, read-only wrapper for a ``trace.jsonl`` evidence file.

    Construction: use :meth:`load_with_validation`.

    Attributes
    ----------
    events:
        Ordered tuple of :class:`~telemetry.events.TraceEvent` objects parsed
        from the file.  Empty when the file contained no non-blank lines.
    run_id:
        The ``run_id`` shared by all events, or ``None`` for an empty trace.
    """

    # ------------------------------------------------------------------
    # Class-level contract metadata
    # ------------------------------------------------------------------

    ARTIFACT_TYPE: ClassVar[str] = "trace"
    ARTIFACT_SCHEMA: ClassVar[str] = "agent-workbench.trace"
    ARTIFACT_SCHEMA_VERSION: ClassVar[str] = TRACE_SCHEMA_VERSION
    PRODUCER_STAGE: ClassVar[str] = "eval"
    CONSUMER_STAGES: ClassVar[tuple[str, ...]] = ("reporting", "analysis")
    CONTENT_FORMAT: ClassVar[str] = "jsonl"

    # ------------------------------------------------------------------
    # Slots / construction
    # ------------------------------------------------------------------

    __slots__ = ("_events", "_run_id")

    def __init__(
        self,
        events: tuple[TraceEvent, ...],
        run_id: str | None,
    ) -> None:
        object.__setattr__(self, "_events", events)
        object.__setattr__(self, "_run_id", run_id)

    def __setattr__(self, name: str, value: object) -> None:  # pragma: no cover
        raise AttributeError("TraceArtifact is immutable")

    def __delattr__(self, name: str) -> None:  # pragma: no cover
        raise AttributeError("TraceArtifact is immutable")

    def __repr__(self) -> str:
        return (
            f"TraceArtifact(event_count={self.event_count!r}, run_id={self.run_id!r})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TraceArtifact):
            return NotImplemented
        return self._events == other._events  # type: ignore[operator]

    # ------------------------------------------------------------------
    # Public payload
    # ------------------------------------------------------------------

    @property
    def events(self) -> tuple[TraceEvent, ...]:
        """Ordered tuple of parsed :class:`~telemetry.events.TraceEvent` objects."""
        return self._events  # type: ignore[return-value]

    @property
    def run_id(self) -> str | None:
        """Shared ``run_id`` for all events, or ``None`` for an empty trace."""
        return self._run_id  # type: ignore[return-value]

    @property
    def event_count(self) -> int:
        """Number of events in this trace."""
        return len(self._events)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_jsonl(self) -> str:
        """Serialize to a JSONL string matching :class:`~telemetry.event_sink.JsonlEventSink`.

        Each event becomes one deterministic, sorted-keys JSON line followed
        by ``\\n``.  An empty trace serializes to the empty string.
        """
        return "".join(event.to_json() + "\n" for event in self._events)  # type: ignore[union-attr]

    # ------------------------------------------------------------------
    # Loader
    # ------------------------------------------------------------------

    @classmethod
    def load_with_validation(cls, path: Path | str) -> "TraceArtifact":
        """Read, parse, and validate a ``trace.jsonl`` file.

        Each non-blank line must be a valid JSON object representing a single
        :class:`~telemetry.events.TraceEvent`.  All events must share the same
        ``run_id``; a mix of run ids is rejected.

        Raises
        ------
        ArtifactLoadError
            File not found or unreadable.
        ArtifactValidationError
            Malformed JSON on any line, invalid event on any line, or
            inconsistent ``run_id`` values across events.  The error message
            names the 1-based line number of the first offending line.
        """
        p = Path(path)
        try:
            raw = p.read_bytes()
        except FileNotFoundError:
            raise ArtifactLoadError(f"trace file not found: {p}")
        except OSError as exc:
            raise ArtifactLoadError(f"could not read {p}: {exc}") from exc

        text = raw.decode("utf-8", errors="replace")
        lines = text.split("\n")

        events: list[TraceEvent] = []
        run_ids: set[str] = set()

        for line_index, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped:
                continue

            # Parse JSON.
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError as exc:
                issue = _err(
                    "invalid_value",
                    f"line {line_index}: not valid JSON: {exc}",
                    location=f"line[{line_index}]",
                )
                raise ArtifactValidationError(
                    ValidationResult((issue,)), "TraceArtifact"
                ) from exc

            if not isinstance(data, dict):
                issue = _err(
                    "wrong_type",
                    f"line {line_index}: expected a JSON object, "
                    f"got {type(data).__name__}",
                    location=f"line[{line_index}]",
                )
                raise ArtifactValidationError(
                    ValidationResult((issue,)), "TraceArtifact"
                )

            # Construct the event (validates schema + required fields).
            try:
                event = TraceEvent.from_dict(data)
            except TraceValidationError as exc:
                combined = "; ".join(exc.errors)
                issue = _err(
                    "invalid_value",
                    f"line {line_index}: invalid trace event: {combined}",
                    location=f"line[{line_index}]",
                )
                raise ArtifactValidationError(
                    ValidationResult((issue,)), "TraceArtifact"
                ) from exc

            events.append(event)
            run_ids.add(event.run_id)

        # Consistency check: all events must share the same run_id.
        if len(run_ids) > 1:
            sorted_ids = sorted(run_ids)
            issue = _err(
                "invalid_value",
                f"trace contains events with mixed run_ids: {sorted_ids}",
                location="run_id",
            )
            raise ArtifactValidationError(
                ValidationResult((issue,)), "TraceArtifact"
            )

        run_id = next(iter(run_ids)) if run_ids else None
        return cls(tuple(events), run_id)

    # ------------------------------------------------------------------
    # ArtifactRef conversion
    # ------------------------------------------------------------------

    def to_artifact_ref(
        self,
        *,
        path: Path | str | None = None,
        uri: str | None = None,
        checksum_sha256: str | None = None,
        validation_status: ArtifactValidationStatus | None = None,
    ) -> ArtifactRef:
        """Build an :class:`~artifacts.models.ArtifactRef` for this artifact.

        ``metadata`` carries ``run_id`` and ``event_count``.
        """
        meta: dict[str, Any] = {
            "run_id": self.run_id,
            "event_count": self.event_count,
        }
        kwargs: dict[str, Any] = {
            "artifact_type": self.ARTIFACT_TYPE,
            "artifact_schema": self.ARTIFACT_SCHEMA,
            "artifact_schema_version": self.ARTIFACT_SCHEMA_VERSION,
            "producer_stage": self.PRODUCER_STAGE,
            "consumer_stages": self.CONSUMER_STAGES,
            "metadata": meta,
        }
        if path is not None:
            kwargs["path"] = path
        if uri is not None:
            kwargs["uri"] = uri
        if checksum_sha256 is not None:
            kwargs["checksum_sha256"] = checksum_sha256
        if validation_status is not None:
            kwargs["validation_status"] = validation_status
        return ArtifactRef(**kwargs)
