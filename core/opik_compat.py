"""Small compatibility layer so Opik remains optional at import time.

The real SDK is still used when installed. When it is absent, tracing calls become
no-ops and explicit Opik client configuration raises a clear runtime error that
callers can catch and use to disable tracing.
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, Callable, Iterator

try:  # pragma: no cover - exercised when the optional dependency is installed
    import opik as opik  # type: ignore[no-redef]
    from opik import opik_context as opik_context  # type: ignore[no-redef]
    OPIK_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - depends on local environment
    OPIK_AVAILABLE = False

    class _NoopOpikContext:
        def get_current_span_data(self) -> None:
            return None

        def get_current_trace_data(self) -> None:
            return None

        def update_current_span(self, **_kwargs: Any) -> None:
            return None

        def update_current_trace(self, **_kwargs: Any) -> None:
            return None

    class _NoopOpikClient:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def auth_check(self) -> None:
            return None

        def flush(self) -> None:
            return None

    class _NoopOpikModule:
        Opik = _NoopOpikClient

        @staticmethod
        def configure(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("Opik SDK is not installed")

        @staticmethod
        def track(*_args: Any, **_kwargs: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
            def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
                return func

            return decorator

        @staticmethod
        @contextmanager
        def start_as_current_trace(*_args: Any, **_kwargs: Any) -> Iterator[SimpleNamespace]:
            yield SimpleNamespace(input=None, output=None)

        @staticmethod
        @contextmanager
        def start_as_current_span(*_args: Any, **_kwargs: Any) -> Iterator[SimpleNamespace]:
            yield SimpleNamespace(input=None, output=None)

    opik = _NoopOpikModule()
    opik_context = _NoopOpikContext()
