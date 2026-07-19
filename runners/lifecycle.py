"""Prompt 17 — Runner-boundary lifecycle instrumentation seam.

`InstrumentedRunnerBackend` wraps a single `RunnerBackend` and emits canonical
Trace Contract v1 events around one `invoke` call: exactly one
`agent.invocation.started` event, then exactly one terminal event
(`agent.invocation.completed` / `.failed` / `.timed_out`). It returns the
wrapped backend's `AgentResult` unchanged, or re-raises the backend's exception
object unchanged — instrumentation never alters the backend's outcome.

Design mirrors `workflow.stages.CallableStage`: an injected clock + span-id
factory, a start event, exactly one terminal event, sink-failure swallowing so
the backend outcome always wins, and `BaseException` handling that still
re-raises. Like `CallableStage`, this class never constructs, owns, or closes
the caller-supplied `EventSink`.

Safe-hash capture: prompt/response text are never copied into any artifact this
wrapper creates. Only their SHA-256 hex digests (`prompt_sha256`,
`response_sha256`) reach telemetry. Returned `error_message` on a
`FAILED`/`TIMED_OUT` result is deliberately *not* copied — it is untrusted
free-form text with no contract excluding prompt/response/credential content;
only a nonempty `error_type` is emitted for returned failures.

Import isolation: this module imports only stdlib, `runners.*`, and the local
`telemetry` package. It is intentionally **not** re-exported from
`runners/__init__.py`, so `import runners` stays free of `telemetry`. Live
production dispatch migration (`core/`, `run.py`, CLI, eval, server, workflow,
registry, failover call sites) is deferred; this is the reusable seam only.

See `docs/runner-lifecycle-events.md` for the full contract writeup.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Callable

from telemetry import EventSink, EventStatus, EventType, make_event, new_span_id

from runners.base import RunnerBackend, RunnerBackendError
from runners.models import AgentInvocation, AgentResult, AgentResultStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sha256_text(text: str | None) -> str | None:
    """Return the SHA-256 hex digest of `text`, or None when `text` is None.

    Leaf-safe local helper — deliberately does not import
    `core.run_cmds._sha256_text`, which would drag the legacy stack in.
    """

    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Returned-result status -> (terminal event family, event status).
_TERMINAL_BY_STATUS = {
    AgentResultStatus.SUCCEEDED: (EventType.AGENT_INVOCATION_COMPLETED, EventStatus.OK),
    AgentResultStatus.FAILED: (EventType.AGENT_INVOCATION_FAILED, EventStatus.ERROR),
    AgentResultStatus.TIMED_OUT: (EventType.AGENT_INVOCATION_TIMED_OUT, EventStatus.ERROR),
}


class RunnerLifecycleConfigurationError(RuntimeError):
    """Invalid instrumentation setup — never a dispatch/failover signal.

    Raised for an invalid construction (`backend is None`) or an instrumented
    invocation that cannot produce a valid trace event (a sink is present but
    the invocation carries no nonempty `run_id`). Always raised **before** the
    wrapped backend is called. Standalone (not a `RunnerBackendError`), so it is
    never mistaken for a dispatch failure or treated as failover-eligible.
    """


class InstrumentedRunnerBackend(RunnerBackend):
    """Emit lifecycle events around one wrapped `RunnerBackend.invoke` call.

    Transparent when constructed without a sink: `invoke` delegates directly,
    requires no `run_id`, and returns the exact result / re-raises the exact
    exception with no telemetry.

    With a sink, `invoke` emits one start event and exactly one terminal event.
    All post-execution instrumentation (clock, field prep, emission) is
    best-effort: the wrapped backend's result or exception always wins.
    """

    def __init__(
        self,
        backend: RunnerBackend,
        *,
        sink: EventSink | None = None,
        clock: Callable[[], datetime] = _utcnow,
        span_id_factory: Callable[[], str] = new_span_id,
    ) -> None:
        if backend is None:
            raise RunnerLifecycleConfigurationError("backend must not be None")
        self._backend = backend
        self._sink = sink
        self._clock = clock
        self._span_id_factory = span_id_factory

    def _emit(self, event_type: EventType, run_id: str, **fields: object) -> None:
        """Emit one lifecycle event. Never raises — a sink/serialization
        failure is swallowed so it can never replace the backend's outcome."""

        if self._sink is None:
            return
        try:
            self._sink.emit(make_event(event_type, run_id, **fields))
        except Exception:  # noqa: BLE001 - deliberate: local sink failure must not
            # mask the backend's real result/exception. The canonical sink logs
            # its own failures at its own layer.
            pass

    def invoke(self, invocation: AgentInvocation) -> AgentResult:
        # No-sink transparent mode: pure delegation, no run_id requirement.
        if self._sink is None:
            return self._backend.invoke(invocation)

        run_id = invocation.run_id
        if not isinstance(run_id, str) or not run_id:
            raise RunnerLifecycleConfigurationError(
                "invocation.run_id must be a nonempty string for instrumented invocation"
            )

        tc = invocation.trace_context
        span_id = tc.span_id if (tc and tc.span_id) else self._span_id_factory()
        parent_span_id = tc.parent_span_id if tc else None

        prompt_hash = _sha256_text(invocation.prompt)

        started_at = self._clock()
        self._emit(
            EventType.AGENT_INVOCATION_STARTED,
            run_id,
            timestamp=started_at.isoformat(),
            span_id=span_id,
            parent_span_id=parent_span_id,
            agent=invocation.agent,
            runner=invocation.runner,
            model=invocation.model,
            prompt_sha256=prompt_hash,
        )

        try:
            result = self._backend.invoke(invocation)
        except BaseException as exc:  # noqa: BLE001 - every started invocation
            # gets exactly one terminal event, whatever the exception type.
            try:  # best-effort: instrumentation never replaces the exception.
                finished_at = self._clock()
                elapsed = (finished_at - started_at).total_seconds() * 1000.0
                # RunnerBackendError.__str__ is contract-safe (identity only).
                # Unexpected Exception / KeyboardInterrupt / SystemExit ->
                # type name only, message omitted.
                error_fields: dict[str, object] = {}
                # RunnerBackendError.__str__ is contract-safe (identity only) ->
                # include only when nonempty. Unexpected Exception /
                # KeyboardInterrupt / SystemExit -> type name only, message omitted.
                if isinstance(exc, RunnerBackendError) and str(exc):
                    error_fields["error_message"] = str(exc)
                self._emit(
                    EventType.AGENT_INVOCATION_FAILED,
                    run_id,
                    timestamp=finished_at.isoformat(),
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    agent=invocation.agent,
                    runner=invocation.runner,
                    model=invocation.model,
                    status=EventStatus.ERROR,
                    duration_ms=elapsed,
                    prompt_sha256=prompt_hash,
                    error_type=type(exc).__name__,
                    **error_fields,
                )
            except Exception:  # noqa: BLE001
                pass
            raise  # bare re-raise: the original backend exception always wins.

        try:  # best-effort terminal instrumentation for a returned result.
            finished_at = self._clock()
            measured = (finished_at - started_at).total_seconds() * 1000.0
            duration = result.duration_ms if result.duration_ms is not None else measured
            response_hash = _sha256_text(result.response_text)
            event_type, event_status = _TERMINAL_BY_STATUS[result.status]

            fields: dict[str, object] = {
                "timestamp": finished_at.isoformat(),
                "span_id": span_id,
                "parent_span_id": parent_span_id,
                "agent": invocation.agent,
                "runner": invocation.runner,
                "model": invocation.model,
                "status": event_status,
                "duration_ms": duration,
                "prompt_sha256": prompt_hash,
                "response_sha256": response_hash,
                # Copy metrics exactly: None stays None (omitted by to_dict);
                # an observed 0 is a real measurement and is preserved.
                "tokens_in": result.tokens_in,
                "tokens_out": result.tokens_out,
                "cost_usd": result.cost_usd,
            }
            # Returned failures: emit only a nonempty error_type. Never copy the
            # untrusted free-form error_message from a returned result.
            if result.status is not AgentResultStatus.SUCCEEDED and isinstance(
                result.error_type, str
            ) and result.error_type:
                fields["error_type"] = result.error_type

            self._emit(event_type, run_id, **fields)
        except Exception:  # noqa: BLE001
            pass
        return result  # exact object, unmutated.
