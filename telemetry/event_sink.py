"""EventSink abstraction and the canonical local JSONL trace sink.

This module imports only `telemetry.events` — no `server`, no `core.opik*`,
no `opik`. Local trace writing works with no server, no network, and no
optional vendor SDK installed.
"""
from __future__ import annotations

import abc
from pathlib import Path
from typing import TYPE_CHECKING

from telemetry.events import MetadataSerializationError, TraceEvent

if TYPE_CHECKING:
    from typing import IO


class SinkClosedError(RuntimeError):
    """Raised when `emit`/`flush` is attempted on a closed sink."""


class EventSink(abc.ABC):
    """Minimal contract for anything that can receive `TraceEvent`s."""

    @abc.abstractmethod
    def emit(self, event: TraceEvent) -> None:
        """Record one event. Must raise `SinkClosedError` if closed."""

    @abc.abstractmethod
    def flush(self) -> None:
        """Flush any buffered data to durable storage."""

    @abc.abstractmethod
    def close(self) -> None:
        """Close the sink. Should be safe to call more than once."""

    def __enter__(self) -> "EventSink":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()


class JsonlEventSink(EventSink):
    """Canonical local JSONL sink: one serialized event per line, appended.

    - Parent directories are created (or validated) intentionally via
      `create_parents`; when `False`, a missing parent directory raises
      `FileNotFoundError` rather than being silently created.
    - Encoding is explicit (`utf-8` by default).
    - Every event is fully serialized to a single line *before* anything is
      written, so a serialization failure (e.g. `MetadataSerializationError`
      for unserializable metadata) never leaves a partial JSON line in the
      file.
    - `flush()`/`close()` are deterministic; `close()` is idempotent.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        create_parents: bool = True,
        encoding: str = "utf-8",
    ) -> None:
        self.path = Path(path)
        self._encoding = encoding
        if create_parents:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        elif not self.path.parent.exists():
            raise FileNotFoundError(
                f"JsonlEventSink: parent directory does not exist and create_parents=False: "
                f"{self.path.parent}"
            )
        self._handle: "IO[str] | None" = self.path.open("a", encoding=self._encoding)
        self._closed = False

    def emit(self, event: TraceEvent) -> None:
        if self._closed or self._handle is None:
            raise SinkClosedError(f"JsonlEventSink is closed: cannot emit to {self.path}")
        # Serialize the complete line first. If this raises (malformed event,
        # unserializable metadata), nothing has been written to the file yet.
        try:
            line = event.to_json()
        except MetadataSerializationError:
            raise
        self._handle.write(line + "\n")

    def flush(self) -> None:
        if self._closed or self._handle is None:
            raise SinkClosedError(f"JsonlEventSink is closed: cannot flush {self.path}")
        self._handle.flush()

    def close(self) -> None:
        if self._closed:
            return
        if self._handle is not None:
            self._handle.flush()
            self._handle.close()
            self._handle = None
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed
