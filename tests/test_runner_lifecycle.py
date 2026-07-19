"""Prompt 17 — tests for `runners.lifecycle.InstrumentedRunnerBackend`.

Deterministic, fake backends only: no real LLM, no network, no server, no Opik.
Uses `tmp_path` + `JsonlEventSink` + `json.loads` per line (mirrors
`tests/test_event_sink.py`). Covers prompt items 2-21.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from runners.base import RunnerBackend, RunnerBackendError
from runners.lifecycle import (
    InstrumentedRunnerBackend,
    RunnerLifecycleConfigurationError,
)
from runners.models import AgentInvocation, AgentResult, AgentResultStatus, TraceContext
from telemetry.event_sink import JsonlEventSink

FIXED_START = datetime(2026, 7, 19, 10, 0, 0, tzinfo=timezone.utc)
FIXED_END = datetime(2026, 7, 19, 10, 0, 1, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Fake backends
# --------------------------------------------------------------------------- #
class ReturningBackend(RunnerBackend):
    """Returns a canned result; counts calls."""

    def __init__(self, result: AgentResult) -> None:
        self._result = result
        self.calls: list[AgentInvocation] = []

    def invoke(self, invocation: AgentInvocation) -> AgentResult:
        self.calls.append(invocation)
        return self._result


class RaisingBackend(RunnerBackend):
    """Raises a caller-supplied exception; counts calls."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.calls: list[AgentInvocation] = []

    def invoke(self, invocation: AgentInvocation) -> AgentResult:
        self.calls.append(invocation)
        raise self._exc


class NeverCalledBackend(RunnerBackend):
    """Fails the test if ever invoked."""

    def __init__(self) -> None:
        self.calls: list[AgentInvocation] = []

    def invoke(self, invocation: AgentInvocation) -> AgentResult:  # pragma: no cover
        self.calls.append(invocation)
        raise AssertionError("backend must not be called")


class SpySink(JsonlEventSink):
    """JSONL sink that records whether close()/flush() were called by the wrapper."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.close_calls = 0
        self.flush_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        super().close()

    def flush(self) -> None:
        self.flush_calls += 1
        super().flush()


class FailingSink(JsonlEventSink):
    """Sink whose emit() always raises."""

    def emit(self, event) -> None:  # noqa: ANN001
        raise RuntimeError("sink is on fire")


class ScriptedClock:
    """Returns scripted datetimes in order; raises when the script is exhausted
    unless `raise_after` is set to trigger a controlled failure."""

    def __init__(self, times, *, raise_after: int | None = None) -> None:
        self._times = list(times)
        self._i = 0
        self._raise_after = raise_after

    def __call__(self) -> datetime:
        if self._raise_after is not None and self._i >= self._raise_after:
            raise RuntimeError("clock exploded")
        value = self._times[self._i]
        self._i += 1
        return value


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _invocation(**overrides) -> AgentInvocation:
    base = {
        "agent": "planner",
        "runner": "claude",
        "model": "opus",
        "prompt": "do the thing",
        "run_id": "run-abc",
    }
    base.update(overrides)
    return AgentInvocation(**base)


def _read_events(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def _fixed_clock():
    return ScriptedClock([FIXED_START, FIXED_END])


class _AutoflushSink(JsonlEventSink):
    """JSONL sink that flushes after every emit so tests can read the trace
    without closing it — the wrapper deliberately never flushes/closes the
    caller-owned sink, so tests provide their own durability."""

    def emit(self, event) -> None:  # noqa: ANN001
        super().emit(event)
        if self._handle is not None:
            self._handle.flush()


def _sink(tmp_path: Path) -> JsonlEventSink:
    return _AutoflushSink(tmp_path / "trace.jsonl")


# --------------------------------------------------------------------------- #
# Item 2-4: terminal event per returned status
# --------------------------------------------------------------------------- #
def test_success_emits_started_then_completed(tmp_path: Path) -> None:
    result = AgentResult(status=AgentResultStatus.SUCCEEDED, response_text="ok")
    backend = ReturningBackend(result)
    wrapper = InstrumentedRunnerBackend(
        backend, sink=_sink(tmp_path), clock=_fixed_clock()
    )

    returned = wrapper.invoke(_invocation())

    assert returned is result
    events = _read_events(tmp_path / "trace.jsonl")
    assert [e["event_type"] for e in events] == [
        "agent.invocation.started",
        "agent.invocation.completed",
    ]
    assert events[1]["status"] == "ok"


def test_failed_result_emits_started_then_failed_without_error_message(tmp_path: Path) -> None:
    result = AgentResult(
        status=AgentResultStatus.FAILED,
        error_type="ProviderError",
        error_message="leaky secret text",
    )
    wrapper = InstrumentedRunnerBackend(
        ReturningBackend(result), sink=_sink(tmp_path), clock=_fixed_clock()
    )

    wrapper.invoke(_invocation())

    events = _read_events(tmp_path / "trace.jsonl")
    assert [e["event_type"] for e in events] == [
        "agent.invocation.started",
        "agent.invocation.failed",
    ]
    terminal = events[1]
    assert terminal["status"] == "error"
    assert terminal["error_type"] == "ProviderError"
    assert "error_message" not in terminal  # returned error_message never copied


def test_timed_out_result_emits_started_then_timed_out(tmp_path: Path) -> None:
    result = AgentResult(status=AgentResultStatus.TIMED_OUT, error_type="Timeout")
    wrapper = InstrumentedRunnerBackend(
        ReturningBackend(result), sink=_sink(tmp_path), clock=_fixed_clock()
    )

    wrapper.invoke(_invocation())

    events = _read_events(tmp_path / "trace.jsonl")
    assert [e["event_type"] for e in events] == [
        "agent.invocation.started",
        "agent.invocation.timed_out",
    ]
    terminal = events[1]
    assert terminal["status"] == "error"
    assert terminal["error_type"] == "Timeout"
    assert "error_message" not in terminal


def test_returned_failure_without_error_type_omits_error_type(tmp_path: Path) -> None:
    result = AgentResult(status=AgentResultStatus.FAILED)  # error_type None
    wrapper = InstrumentedRunnerBackend(
        ReturningBackend(result), sink=_sink(tmp_path), clock=_fixed_clock()
    )

    wrapper.invoke(_invocation())

    terminal = _read_events(tmp_path / "trace.jsonl")[1]
    assert "error_type" not in terminal


# --------------------------------------------------------------------------- #
# Item (correction #4): construction validation
# --------------------------------------------------------------------------- #
def test_constructing_with_none_backend_raises() -> None:
    with pytest.raises(RunnerLifecycleConfigurationError):
        InstrumentedRunnerBackend(None)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Item 5-7: exceptions
# --------------------------------------------------------------------------- #
def test_raised_runner_backend_error_emits_failed_and_reraises(tmp_path: Path) -> None:
    exc = RunnerBackendError("dispatch failed", agent="planner", runner="claude")
    backend = RaisingBackend(exc)
    wrapper = InstrumentedRunnerBackend(backend, sink=_sink(tmp_path), clock=_fixed_clock())

    with pytest.raises(RunnerBackendError) as caught:
        wrapper.invoke(_invocation())

    assert caught.value is exc
    events = _read_events(tmp_path / "trace.jsonl")
    assert [e["event_type"] for e in events] == [
        "agent.invocation.started",
        "agent.invocation.failed",
    ]
    terminal = events[1]
    assert terminal["error_type"] == "RunnerBackendError"
    # RunnerBackendError message is contract-safe (identity only) -> may appear.
    assert terminal["error_message"] == "dispatch failed"


def test_unexpected_exception_emits_failed_without_error_message_and_reraises(
    tmp_path: Path,
) -> None:
    exc = ValueError("some internal detail")
    wrapper = InstrumentedRunnerBackend(
        RaisingBackend(exc), sink=_sink(tmp_path), clock=_fixed_clock()
    )

    with pytest.raises(ValueError) as caught:
        wrapper.invoke(_invocation())

    assert caught.value is exc
    terminal = _read_events(tmp_path / "trace.jsonl")[1]
    assert terminal["event_type"] == "agent.invocation.failed"
    assert terminal["error_type"] == "ValueError"
    assert "error_message" not in terminal  # unexpected exception message omitted


@pytest.mark.parametrize("exc_cls", [KeyboardInterrupt, SystemExit])
def test_base_exception_emits_failed_type_only_and_propagates(
    tmp_path: Path, exc_cls
) -> None:
    exc = exc_cls()
    wrapper = InstrumentedRunnerBackend(
        RaisingBackend(exc), sink=_sink(tmp_path), clock=_fixed_clock()
    )

    with pytest.raises(exc_cls) as caught:
        wrapper.invoke(_invocation())

    assert caught.value is exc
    terminal = _read_events(tmp_path / "trace.jsonl")[1]
    assert terminal["event_type"] == "agent.invocation.failed"
    assert terminal["error_type"] == exc_cls.__name__
    assert "error_message" not in terminal


# --------------------------------------------------------------------------- #
# Item 8: backend called exactly once
# --------------------------------------------------------------------------- #
def test_backend_called_exactly_once_on_success(tmp_path: Path) -> None:
    backend = ReturningBackend(AgentResult(status=AgentResultStatus.SUCCEEDED))
    wrapper = InstrumentedRunnerBackend(backend, sink=_sink(tmp_path), clock=_fixed_clock())
    wrapper.invoke(_invocation())
    assert len(backend.calls) == 1


# --------------------------------------------------------------------------- #
# Item 9-10: span/parent-span resolution and sharing
# --------------------------------------------------------------------------- #
def test_explicit_span_and_parent_shared_across_both_events(tmp_path: Path) -> None:
    tc = TraceContext(span_id="span-explicit", parent_span_id="span-parent")
    wrapper = InstrumentedRunnerBackend(
        ReturningBackend(AgentResult(status=AgentResultStatus.SUCCEEDED)),
        sink=_sink(tmp_path),
        clock=_fixed_clock(),
    )

    wrapper.invoke(_invocation(trace_context=tc))

    events = _read_events(tmp_path / "trace.jsonl")
    assert all(e["span_id"] == "span-explicit" for e in events)
    assert all(e["parent_span_id"] == "span-parent" for e in events)


def test_missing_span_generated_via_injected_factory_and_shared(tmp_path: Path) -> None:
    wrapper = InstrumentedRunnerBackend(
        ReturningBackend(AgentResult(status=AgentResultStatus.SUCCEEDED)),
        sink=_sink(tmp_path),
        clock=_fixed_clock(),
        span_id_factory=lambda: "span-generated",
    )

    wrapper.invoke(_invocation())  # no trace_context

    events = _read_events(tmp_path / "trace.jsonl")
    assert all(e["span_id"] == "span-generated" for e in events)
    assert all("parent_span_id" not in e for e in events)


# --------------------------------------------------------------------------- #
# Item 11: exact SHA-256 hashes
# --------------------------------------------------------------------------- #
def test_prompt_and_response_hashes_match_hashlib_reference(tmp_path: Path) -> None:
    prompt = "prompt content here"
    response = "response content here"
    result = AgentResult(status=AgentResultStatus.SUCCEEDED, response_text=response)
    wrapper = InstrumentedRunnerBackend(
        ReturningBackend(result), sink=_sink(tmp_path), clock=_fixed_clock()
    )

    wrapper.invoke(_invocation(prompt=prompt))

    events = _read_events(tmp_path / "trace.jsonl")
    expected_prompt = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    expected_response = hashlib.sha256(response.encode("utf-8")).hexdigest()
    started, completed = events
    assert started["prompt_sha256"] == expected_prompt
    assert "response_sha256" not in started  # response hash is terminal-only
    assert completed["prompt_sha256"] == expected_prompt
    assert completed["response_sha256"] == expected_response


def test_response_hash_never_substitutes_stdout_or_stderr(tmp_path: Path) -> None:
    # response_text absent -> no response_sha256, even when stdout/stderr present.
    result = AgentResult(
        status=AgentResultStatus.SUCCEEDED, stdout="raw out", stderr="raw err"
    )
    wrapper = InstrumentedRunnerBackend(
        ReturningBackend(result), sink=_sink(tmp_path), clock=_fixed_clock()
    )

    wrapper.invoke(_invocation())

    completed = _read_events(tmp_path / "trace.jsonl")[1]
    assert "response_sha256" not in completed


# --------------------------------------------------------------------------- #
# Item 12/16: raw-content safety + exception identity
# --------------------------------------------------------------------------- #
def test_raw_prompt_and_response_never_appear_in_events_and_exc_reraised(
    tmp_path: Path,
) -> None:
    raw_prompt = "SUPER_SECRET_PROMPT_TEXT"
    raw_response = "SUPER_SECRET_RESPONSE_TEXT"
    # Backend raises an *unexpected* exception whose message embeds both raw
    # texts. Non-RunnerBackendError messages are omitted from telemetry, so no
    # wrapper-created artifact carries the raw text. (A RunnerBackendError is
    # trusted contract-safe and its message IS emitted, per item 5 — see
    # test_raised_runner_backend_error_emits_failed_and_reraises.)
    exc = ValueError(f"boom {raw_prompt} :: {raw_response}")
    wrapper = InstrumentedRunnerBackend(
        RaisingBackend(exc), sink=_sink(tmp_path), clock=_fixed_clock()
    )

    with pytest.raises(ValueError) as caught:
        wrapper.invoke(_invocation(prompt=raw_prompt))

    assert caught.value is exc  # original exception, unchanged
    raw_text = (tmp_path / "trace.jsonl").read_text(encoding="utf-8")
    assert raw_prompt not in raw_text
    assert raw_response not in raw_text


# --------------------------------------------------------------------------- #
# Item (correction #3): best-effort terminal instrumentation (clock fails)
# --------------------------------------------------------------------------- #
def test_clock_failure_on_terminal_still_returns_exact_result(tmp_path: Path) -> None:
    result = AgentResult(status=AgentResultStatus.SUCCEEDED)
    # First call (started_at) OK; second call (terminal) raises.
    clock = ScriptedClock([FIXED_START], raise_after=1)
    wrapper = InstrumentedRunnerBackend(
        ReturningBackend(result), sink=_sink(tmp_path), clock=clock
    )

    returned = wrapper.invoke(_invocation())

    assert returned is result
    events = _read_events(tmp_path / "trace.jsonl")
    # Start event still written; terminal instrumentation swallowed.
    assert [e["event_type"] for e in events] == ["agent.invocation.started"]


def test_clock_failure_on_terminal_still_reraises_backend_exception(tmp_path: Path) -> None:
    exc = RunnerBackendError("dispatch failed")
    clock = ScriptedClock([FIXED_START], raise_after=1)
    wrapper = InstrumentedRunnerBackend(
        RaisingBackend(exc), sink=_sink(tmp_path), clock=clock
    )

    with pytest.raises(RunnerBackendError) as caught:
        wrapper.invoke(_invocation())

    assert caught.value is exc


# --------------------------------------------------------------------------- #
# Item 13: prompt_ref-only invocation reads no file
# --------------------------------------------------------------------------- #
def test_prompt_ref_only_reads_no_file_and_omits_prompt_hash(
    tmp_path: Path, monkeypatch
) -> None:
    def _boom(*a, **k):
        raise AssertionError("no file read should happen")

    monkeypatch.setattr(Path, "read_text", _boom)
    monkeypatch.setattr("builtins.open", _boom)

    wrapper = InstrumentedRunnerBackend(
        ReturningBackend(AgentResult(status=AgentResultStatus.SUCCEEDED)),
        sink=_sink(tmp_path),
        clock=_fixed_clock(),
    )

    wrapper.invoke(_invocation(prompt=None, prompt_ref="ref://prompt"))

    monkeypatch.undo()  # restore read_text/open so the test can read the trace
    events = _read_events(tmp_path / "trace.jsonl")
    assert all("prompt_sha256" not in e for e in events)


# --------------------------------------------------------------------------- #
# Item 14-15: metrics copied exactly; duration precedence
# --------------------------------------------------------------------------- #
def test_none_metrics_omitted(tmp_path: Path) -> None:
    result = AgentResult(status=AgentResultStatus.SUCCEEDED)  # all metrics None
    wrapper = InstrumentedRunnerBackend(
        ReturningBackend(result), sink=_sink(tmp_path), clock=_fixed_clock()
    )

    wrapper.invoke(_invocation())

    completed = _read_events(tmp_path / "trace.jsonl")[1]
    for field in ("tokens_in", "tokens_out", "cost_usd"):
        assert field not in completed


def test_observed_zero_metrics_and_zero_duration_retained(tmp_path: Path) -> None:
    result = AgentResult(
        status=AgentResultStatus.SUCCEEDED,
        duration_ms=0,
        tokens_in=0,
        tokens_out=0,
        cost_usd=0.0,
    )
    wrapper = InstrumentedRunnerBackend(
        ReturningBackend(result), sink=_sink(tmp_path), clock=_fixed_clock()
    )

    wrapper.invoke(_invocation())

    completed = _read_events(tmp_path / "trace.jsonl")[1]
    assert completed["duration_ms"] == 0  # result.duration_ms wins even at 0
    assert completed["tokens_in"] == 0
    assert completed["tokens_out"] == 0
    assert completed["cost_usd"] == 0.0


def test_measured_duration_used_when_result_duration_absent(tmp_path: Path) -> None:
    result = AgentResult(status=AgentResultStatus.SUCCEEDED)  # duration_ms None
    # 1 second between scripted clock ticks -> 1000.0 ms measured.
    wrapper = InstrumentedRunnerBackend(
        ReturningBackend(result),
        sink=_sink(tmp_path),
        clock=ScriptedClock([FIXED_START, FIXED_END]),
    )

    wrapper.invoke(_invocation())

    completed = _read_events(tmp_path / "trace.jsonl")[1]
    assert completed["duration_ms"] == 1000.0


def test_result_duration_wins_over_measured(tmp_path: Path) -> None:
    result = AgentResult(status=AgentResultStatus.SUCCEEDED, duration_ms=42.5)
    wrapper = InstrumentedRunnerBackend(
        ReturningBackend(result),
        sink=_sink(tmp_path),
        clock=ScriptedClock([FIXED_START, FIXED_END]),
    )

    wrapper.invoke(_invocation())

    completed = _read_events(tmp_path / "trace.jsonl")[1]
    assert completed["duration_ms"] == 42.5


# --------------------------------------------------------------------------- #
# Item 16: caller sink never closed/flushed
# --------------------------------------------------------------------------- #
def test_caller_sink_never_closed_or_flushed(tmp_path: Path) -> None:
    sink = SpySink(tmp_path / "trace.jsonl")
    wrapper = InstrumentedRunnerBackend(
        ReturningBackend(AgentResult(status=AgentResultStatus.SUCCEEDED)),
        sink=sink,
        clock=_fixed_clock(),
    )

    wrapper.invoke(_invocation())

    assert sink.close_calls == 0
    assert sink.flush_calls == 0
    sink.close()


# --------------------------------------------------------------------------- #
# Item 17: failing sink does not change outcome
# --------------------------------------------------------------------------- #
def test_failing_sink_success_still_returns_exact_result(tmp_path: Path) -> None:
    result = AgentResult(status=AgentResultStatus.SUCCEEDED)
    wrapper = InstrumentedRunnerBackend(
        ReturningBackend(result),
        sink=FailingSink(tmp_path / "trace.jsonl"),
        clock=_fixed_clock(),
    )

    assert wrapper.invoke(_invocation()) is result


def test_failing_sink_backend_failure_still_reraises(tmp_path: Path) -> None:
    exc = RunnerBackendError("dispatch failed")
    wrapper = InstrumentedRunnerBackend(
        RaisingBackend(exc),
        sink=FailingSink(tmp_path / "trace.jsonl"),
        clock=_fixed_clock(),
    )

    with pytest.raises(RunnerBackendError) as caught:
        wrapper.invoke(_invocation())
    assert caught.value is exc


# --------------------------------------------------------------------------- #
# Item 18: no-sink transparent mode
# --------------------------------------------------------------------------- #
def test_no_sink_delegates_transparently_without_run_id() -> None:
    result = AgentResult(status=AgentResultStatus.SUCCEEDED)
    backend = ReturningBackend(result)
    wrapper = InstrumentedRunnerBackend(backend)  # no sink

    inv = _invocation(run_id=None)  # missing run_id tolerated in transparent mode
    assert wrapper.invoke(inv) is result
    assert backend.calls == [inv]


def test_no_sink_reraises_exact_exception() -> None:
    exc = ValueError("nope")
    wrapper = InstrumentedRunnerBackend(RaisingBackend(exc))
    with pytest.raises(ValueError) as caught:
        wrapper.invoke(_invocation(run_id=None))
    assert caught.value is exc


# --------------------------------------------------------------------------- #
# Item 19: sink + missing run_id raises before backend
# --------------------------------------------------------------------------- #
def test_sink_with_missing_run_id_raises_before_backend(tmp_path: Path) -> None:
    backend = NeverCalledBackend()
    wrapper = InstrumentedRunnerBackend(backend, sink=_sink(tmp_path), clock=_fixed_clock())

    with pytest.raises(RunnerLifecycleConfigurationError):
        wrapper.invoke(_invocation(run_id=None))

    assert backend.calls == []
    assert not (tmp_path / "trace.jsonl").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Item 20: invocation/result not mutated; exact result returned
# --------------------------------------------------------------------------- #
def test_invocation_and_result_not_mutated(tmp_path: Path) -> None:
    result = AgentResult(
        status=AgentResultStatus.SUCCEEDED, response_text="ok", tokens_in=3
    )
    before_result = result.to_dict()
    inv = _invocation()
    before_inv = inv.to_dict()
    wrapper = InstrumentedRunnerBackend(
        ReturningBackend(result), sink=_sink(tmp_path), clock=_fixed_clock()
    )

    returned = wrapper.invoke(inv)

    assert returned is result
    assert result.to_dict() == before_result
    assert inv.to_dict() == before_inv


# --------------------------------------------------------------------------- #
# Item 21: JSONL proof — exactly two valid lines, correct order/types
# --------------------------------------------------------------------------- #
def test_one_success_produces_exactly_two_valid_jsonl_lines(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    wrapper = InstrumentedRunnerBackend(
        ReturningBackend(AgentResult(status=AgentResultStatus.SUCCEEDED)),
        sink=_sink(tmp_path),
        clock=_fixed_clock(),
    )

    wrapper.invoke(_invocation())

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]  # each line parses independently
    assert [p["event_type"] for p in parsed] == [
        "agent.invocation.started",
        "agent.invocation.completed",
    ]
    assert all(p["event_schema_version"] == "1" for p in parsed)
    assert all(p["run_id"] == "run-abc" for p in parsed)
