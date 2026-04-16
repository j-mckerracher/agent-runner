from __future__ import annotations

import os
from typing import Any

from ..console import log
from ..models import WorkflowConfig


class NullObservabilitySink:
    """No-op observability sink used when telemetry is disabled."""

    def record_event(self, event_type: str, payload: dict[str, Any]) -> None:  # noqa: D401
        return

    def flush(self) -> None:
        return


class LangfuseObservabilitySink:
    """Lightweight Langfuse event-to-trace adapter for workflow runs."""

    def __init__(self, client: Any):
        self._client = client
        self._trace: Any | None = None
        self._stage_spans: dict[str, Any] = {}
        self._uow_spans: dict[str, Any] = {}
        self._agent_spans: dict[str, Any] = {}

    def record_event(self, event_type: str, payload: dict[str, Any]) -> None:
        handler = getattr(self, f"_handle_{event_type}", None)
        if callable(handler):
            handler(payload)
            return
        self._annotate(event_type, payload)

    def flush(self) -> None:
        self._safe_call(self._client, "flush")

    def _handle_workflow_start(self, payload: dict[str, Any]) -> None:
        self._trace = self._safe_call(
            self._client,
            "trace",
            name="agent-runner-workflow",
            input=payload,
        )

    def _handle_workflow_complete(self, payload: dict[str, Any]) -> None:
        for span in list(self._agent_spans.values()):
            self._end_span(span, {"status": "closed_by_workflow_complete"})
        for span in list(self._uow_spans.values()):
            self._end_span(span, {"status": "closed_by_workflow_complete"})
        for span in list(self._stage_spans.values()):
            self._end_span(span, {"status": "closed_by_workflow_complete"})
        self._agent_spans.clear()
        self._uow_spans.clear()
        self._stage_spans.clear()
        if self._trace is not None:
            if self._safe_call(self._trace, "end", output=payload) is None:
                self._safe_call(self._trace, "update", output=payload)
        self.flush()

    def _handle_stage_start(self, payload: dict[str, Any]) -> None:
        stage = str(payload.get("stage", "unknown"))
        self._stage_spans[stage] = self._open_span(f"stage:{stage}", payload)

    def _handle_stage_complete(self, payload: dict[str, Any]) -> None:
        stage = str(payload.get("stage", "unknown"))
        span = self._stage_spans.pop(stage, None)
        self._end_span(span, payload)

    def _handle_uow_start(self, payload: dict[str, Any]) -> None:
        uow_id = str(payload.get("uow_id", "unknown"))
        self._uow_spans[uow_id] = self._open_span(
            f"uow:{uow_id}",
            payload,
            parent=self._stage_spans.get("software_engineer"),
        )

    def _handle_uow_complete(self, payload: dict[str, Any]) -> None:
        uow_id = str(payload.get("uow_id", "unknown"))
        span = self._uow_spans.pop(uow_id, None)
        self._end_span(span, payload)

    def _handle_agent_dispatch(self, payload: dict[str, Any]) -> None:
        key = self._agent_key(payload)
        parent = self._uow_parent(payload) or self._stage_parent(payload)
        self._agent_spans[key] = self._open_span(
            f"agent:{payload.get('stage_key', 'unknown')}",
            payload,
            parent=parent,
        )

    def _handle_agent_result(self, payload: dict[str, Any]) -> None:
        key = self._agent_key(payload)
        span = self._agent_spans.pop(key, None)
        self._end_span(span, payload)

    def _handle_eval_attempt(self, payload: dict[str, Any]) -> None:
        self._record_instant_span(
            f"eval:{payload.get('stage', 'unknown')}",
            payload,
            parent=self._stage_spans.get(str(payload.get("stage", "unknown"))),
        )

    def _handle_escalation_start(self, payload: dict[str, Any]) -> None:
        self._record_instant_span(
            f"escalation:{payload.get('stage', 'unknown')}",
            payload,
            parent=self._uow_parent(payload) or self._stage_parent(payload),
        )

    def _handle_workflow_paused(self, payload: dict[str, Any]) -> None:
        self._record_instant_span(
            "workflow:paused",
            payload,
            parent=self._stage_parent(payload),
        )

    def _handle_workflow_resumed(self, payload: dict[str, Any]) -> None:
        self._record_instant_span(
            "workflow:resumed",
            payload,
            parent=self._stage_parent(payload),
        )

    def _annotate(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._trace is None:
            return
        metadata = {"event_type": event_type, **payload}
        if self._safe_call(self._trace, "event", name=event_type, metadata=metadata) is None:
            self._safe_call(self._trace, "update", metadata=metadata)

    def _open_span(
        self,
        name: str,
        payload: dict[str, Any],
        parent: Any | None = None,
    ) -> Any | None:
        creator = parent if parent is not None and hasattr(parent, "span") else self._trace
        if creator is None:
            return None
        return self._safe_call(creator, "span", name=name, input=payload)

    def _end_span(self, span: Any | None, payload: dict[str, Any]) -> None:
        if span is None:
            return
        if self._safe_call(span, "end", output=payload) is None:
            self._safe_call(span, "update", output=payload)

    def _record_instant_span(
        self,
        name: str,
        payload: dict[str, Any],
        parent: Any | None = None,
    ) -> None:
        span = self._open_span(name, payload, parent=parent)
        self._end_span(span, payload)

    def _stage_parent(self, payload: dict[str, Any]) -> Any | None:
        stage = payload.get("stage") or payload.get("stage_key")
        if stage is None:
            return None
        return self._stage_spans.get(str(stage))

    def _uow_parent(self, payload: dict[str, Any]) -> Any | None:
        uow_id = payload.get("uow_id")
        if uow_id is None:
            return None
        return self._uow_spans.get(str(uow_id))

    def _agent_key(self, payload: dict[str, Any]) -> str:
        return ":".join(
            [
                str(payload.get("stage_key", "")),
                str(payload.get("attempt", "")),
                str(payload.get("uow_id", "")),
            ]
        )

    @staticmethod
    def _safe_call(obj: Any, method: str, *args: Any, **kwargs: Any) -> Any:
        fn = getattr(obj, method, None)
        if callable(fn):
            return fn(*args, **kwargs)
        return None


def build_observability_sink_from_env() -> Any | None:
    """Build the configured observability sink, if any."""

    provider = os.environ.get("AGENT_RUNNER_OBSERVABILITY", "").strip().lower()
    langfuse_requested = provider == "langfuse" or bool(
        os.environ.get("LANGFUSE_PUBLIC_KEY")
    )
    if not langfuse_requested:
        return None

    try:
        from langfuse import get_client
    except ImportError as exc:
        log("WARN", f"Langfuse observability requested but langfuse is not installed: {exc}")
        return None

    try:
        return LangfuseObservabilitySink(get_client())
    except Exception as exc:
        log("WARN", f"Failed to initialize Langfuse observability: {exc}")
        return None


def record_observability_event(
    config: WorkflowConfig,
    event_type: str,
    **payload: Any,
) -> None:
    """Record an observability event through the configured sink."""

    sink = config.observability_sink
    if sink is None:
        return
    try:
        sink.record_event(event_type, payload)
    except Exception as exc:
        log("WARN", f"Observability sink failed for {event_type}: {exc}")

