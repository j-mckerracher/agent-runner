"""Opik tracing helpers for workflow runs.

Failure-mode contract:
- Missing runtime settings fail fast before the workflow starts.
- Unreachable Opik fails fast during tracer construction.
- Opik errors while opening/closing traces or spans propagate, because this
  integration is intentionally required for workflow observability.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, Iterator

import opik
from opik import opik_context


_TRACE_DEPTH: ContextVar[int] = ContextVar("opik_trace_depth", default=0)


class OpikConfigurationError(RuntimeError):
    """Raised when required Opik configuration is absent or invalid."""


@dataclass(frozen=True)
class OpikRuntimeConfig:
    dashboard_url: str
    workspace_name: str
    project_id: str
    project_name: str
    api_url: str


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _derive_api_url(dashboard_url: str) -> str:
    explicit = _clean(os.environ.get("OPIK_BASE_URL")) or _clean(os.environ.get("OPIK_URL_OVERRIDE"))
    if explicit:
        return explicit.rstrip("/")
    return f"{dashboard_url.rstrip('/')}/api"


def opik_config_from_settings(settings: dict[str, Any] | None) -> OpikRuntimeConfig:
    cfg = settings or {}
    dashboard_url = _clean(cfg.get("dashboard_url")).rstrip("/")
    workspace_name = _clean(cfg.get("workspace_name"))
    project_id = _clean(cfg.get("project_id"))
    project_name = _clean(cfg.get("project_name")) or "agent-runner"
    missing = [
        name
        for name, value in (
            ("dashboard_url", dashboard_url),
            ("workspace_name", workspace_name),
            ("project_id", project_id),
            ("project_name", project_name),
        )
        if not value
    ]
    if missing:
        raise OpikConfigurationError(
            "Opik is required but not fully configured; missing "
            + ", ".join(f"opik.{name}" for name in missing)
        )
    return OpikRuntimeConfig(
        dashboard_url=dashboard_url,
        workspace_name=workspace_name,
        project_id=project_id,
        project_name=project_name,
        api_url=_derive_api_url(dashboard_url),
    )


def _is_local_opik_url(api_url: str) -> bool:
    """Return True if `api_url` points to a local Opik deployment."""
    from urllib.parse import urlparse

    hostname = (urlparse(api_url).hostname or "").lower()
    return hostname in ("localhost", "127.0.0.1", "0.0.0.0", "[::1]")


def configure_opik_client(settings: dict[str, Any] | None):
    """Configure the SDK from persisted settings and verify the endpoint."""

    cfg = opik_config_from_settings(settings)
    use_local = _is_local_opik_url(cfg.api_url)

    configure_kwargs: dict[str, Any] = {
        "url_override": cfg.api_url,
        "project_name": cfg.project_name,
        "use_local": use_local,
        "force": True,
    }
    if not use_local:
        api_key = _clean(os.environ.get("OPIK_API_KEY")) or None
        if api_key:
            configure_kwargs["api_key"] = api_key
        configure_kwargs["workspace"] = cfg.workspace_name
    opik.configure(**configure_kwargs)
    client = opik.Opik(project_name=cfg.project_name, workspace=cfg.workspace_name)
    auth_check = getattr(client, "auth_check", None)
    if callable(auth_check):
        auth_check()
    return client


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


class OpikTracer:
    def __init__(
        self,
        *,
        settings: dict[str, Any],
        change_id: str,
        runner: str,
        model: str | None,
        emit_event: Callable[..., None] | None = None,
    ) -> None:
        self.config = opik_config_from_settings(settings)
        self.change_id = change_id
        self.runner = runner
        self.model = model
        self.emit_event = emit_event
        self.client = configure_opik_client(settings)

    def _base_metadata(self, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        return _compact_metadata(
            {
                "change_id": self.change_id,
                "runner": self.runner,
                "model": self.model,
                **(metadata or {}),
            }
        )

    def _emit(self, event_type: str, **fields: Any) -> None:
        if self.emit_event is not None:
            self.emit_event(event_type, **fields)

    @contextmanager
    def _mirror_events(
        self,
        *,
        name: str,
        trace_type: str,
        kind: str,
        metadata: dict[str, Any],
    ) -> Iterator[None]:
        depth = _TRACE_DEPTH.get()
        self._emit(
            "opik.start",
            name=name,
            kind=kind,
            trace_type=trace_type,
            depth=depth,
            metadata=metadata,
        )
        token = _TRACE_DEPTH.set(depth + 1)
        started = time.perf_counter()
        try:
            yield
        except Exception as exc:
            self._emit(
                "opik.end",
                name=name,
                kind=kind,
                trace_type=trace_type,
                depth=depth,
                status="error",
                duration_ms=int((time.perf_counter() - started) * 1000),
                error=f"{type(exc).__name__}: {exc}",
                metadata=metadata,
            )
            raise
        else:
            self._emit(
                "opik.end",
                name=name,
                kind=kind,
                trace_type=trace_type,
                depth=depth,
                status="ok",
                duration_ms=int((time.perf_counter() - started) * 1000),
                metadata=metadata,
            )
        finally:
            _TRACE_DEPTH.reset(token)

    @contextmanager
    def trace(
        self,
        name: str,
        *,
        metadata: dict[str, Any] | None = None,
        input: dict[str, Any] | None = None,
    ) -> Iterator[Any]:
        merged_metadata = self._base_metadata(metadata)
        with opik.start_as_current_trace(
            name,
            input=input,
            tags=["agent-workbench", self.runner],
            metadata=merged_metadata,
            project_name=self.config.project_name,
            thread_id=self.change_id,
        ) as trace:
            with self._mirror_events(
                name=name,
                trace_type="general",
                kind="trace",
                metadata=merged_metadata,
            ):
                yield trace

    @contextmanager
    def span(
        self,
        name: str,
        *,
        type: str = "general",
        metadata: dict[str, Any] | None = None,
        input: dict[str, Any] | None = None,
    ) -> Iterator[Any]:
        merged_metadata = self._base_metadata(metadata)
        with opik.start_as_current_span(
            name,
            type=type,
            input=input,
            metadata=merged_metadata,
            project_name=self.config.project_name,
            model=self.model,
        ) as span:
            with self._mirror_events(
                name=name,
                trace_type=type,
                kind="span",
                metadata=merged_metadata,
            ):
                yield span

    def update_current_trace(
        self,
        *,
        metadata: dict[str, Any] | None = None,
        feedback_scores: list[dict[str, Any]] | None = None,
        output: dict[str, Any] | None = None,
    ) -> None:
        opik_context.update_current_trace(
            metadata=self._base_metadata(metadata),
            feedback_scores=feedback_scores,
            output=output,
            thread_id=self.change_id,
        )

    def update_current_span(
        self,
        *,
        metadata: dict[str, Any] | None = None,
        feedback_scores: list[dict[str, Any]] | None = None,
        input: dict[str, Any] | None = None,
        output: dict[str, Any] | None = None,
        usage: dict[str, Any] | None = None,
    ) -> None:
        opik_context.update_current_span(
            metadata=self._base_metadata(metadata),
            feedback_scores=feedback_scores,
            input=input,
            output=output,
            usage=usage,
            model=self.model,
        )

    def flush(self) -> None:
        flush = getattr(self.client, "flush", None)
        if callable(flush):
            flush()


def maybe_trace(tracer: OpikTracer | None, name: str, **kwargs: Any):
    if tracer is None:
        return nullcontext()
    return tracer.trace(name, **kwargs)


def maybe_span(tracer: OpikTracer | None, name: str, **kwargs: Any):
    if tracer is None:
        return nullcontext()
    return tracer.span(name, **kwargs)
