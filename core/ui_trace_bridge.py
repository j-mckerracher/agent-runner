from __future__ import annotations

import functools
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator, ParamSpec, TypeVar

import opik

from server.events import emit

P = ParamSpec("P")
R = TypeVar("R")

_TRACE_DEPTH: ContextVar[int] = ContextVar("ui_trace_depth", default=0)


def _compact_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return {}
    compact: dict[str, Any] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            compact[key] = value
        else:
            compact[key] = str(value)
    return compact


@contextmanager
def _mirror_trace_events(
    *,
    name: str,
    trace_type: str,
    kind: str,
    metadata: dict[str, Any] | None = None,
) -> Iterator[None]:
    compact_metadata = _compact_metadata(metadata)
    depth = _TRACE_DEPTH.get()
    emit(
        "opik.start",
        name=name,
        kind=kind,
        trace_type=trace_type,
        depth=depth,
        metadata=compact_metadata,
    )
    token = _TRACE_DEPTH.set(depth + 1)
    started = time.perf_counter()
    try:
        yield
    except Exception as exc:
        emit(
            "opik.end",
            name=name,
            kind=kind,
            trace_type=trace_type,
            depth=depth,
            status="error",
            duration_ms=int((time.perf_counter() - started) * 1000),
            error=f"{type(exc).__name__}: {exc}",
            metadata=compact_metadata,
        )
        raise
    else:
        emit(
            "opik.end",
            name=name,
            kind=kind,
            trace_type=trace_type,
            depth=depth,
            status="ok",
            duration_ms=int((time.perf_counter() - started) * 1000),
            metadata=compact_metadata,
        )
    finally:
        _TRACE_DEPTH.reset(token)


def track_with_ui(
    *,
    name: str,
    type: str,
    metadata_getter: Callable[P, dict[str, Any] | None] | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            metadata = metadata_getter(*args, **kwargs) if metadata_getter else None
            with _mirror_trace_events(name=name, trace_type=type, kind="trace", metadata=metadata):
                return func(*args, **kwargs)

        return opik.track(name=name, type=type)(wrapped)

    return decorator


@contextmanager
def start_span_with_ui(
    name: str,
    *,
    type: str,
    metadata: dict[str, Any] | None = None,
):
    with opik.start_as_current_span(name, type=type) as span:
        with _mirror_trace_events(name=name, trace_type=type, kind="span", metadata=metadata):
            yield span
