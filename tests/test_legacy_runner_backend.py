"""Behavior tests for `runners.legacy.LegacyDispatchBackend` (Prompt 13).

Most tests inject a recording fake dispatcher via the constructor — no
patching, no `core` import. One test exercises the lazy default wiring by
patching `core.agent_cmd.run_agent_cmd`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runners.base import RunnerInvocationError, UnsupportedInvocationError
from runners.legacy import LegacyDispatchBackend
from runners.models import AgentInvocation, AgentResultStatus


class RecordingDispatcher:
    """Fake `run_agent_cmd`: records kwargs, returns a configured string."""

    def __init__(self, response: str = "dispatched-text") -> None:
        self.response = response
        self.calls: list[dict] = []

    def __call__(self, **kwargs) -> str:
        self.calls.append(kwargs)
        return self.response


def _invocation(**overrides) -> AgentInvocation:
    base = {"agent": "coder", "runner": "codex", "prompt": "write code"}
    base.update(overrides)
    return AgentInvocation(**base)


# --------------------------------------------------------------------------
# Field mapping / success path
# --------------------------------------------------------------------------


def test_maps_core_fields_and_returns_succeeded():
    dispatcher = RecordingDispatcher()
    backend = LegacyDispatchBackend(dispatcher=dispatcher)
    result = backend.invoke(
        _invocation(model="gpt-5", repo="some/repo", change_id="CHG-1")
    )
    assert len(dispatcher.calls) == 1
    call = dispatcher.calls[0]
    assert call == {
        "runner": "codex",
        "prompt": "write code",
        "agent": "coder",
        "runner_model": "gpt-5",
        "repo": "some/repo",
        "change_id": "CHG-1",
    }
    assert result.status is AgentResultStatus.SUCCEEDED
    assert result.response_text == "dispatched-text"


def test_repo_path_forwarded_as_str():
    dispatcher = RecordingDispatcher()
    LegacyDispatchBackend(dispatcher=dispatcher).invoke(_invocation(repo=Path("a/b")))
    assert dispatcher.calls[0]["repo"] == "a/b"
    assert isinstance(dispatcher.calls[0]["repo"], str)


def test_none_model_and_repo_omit_kwargs():
    dispatcher = RecordingDispatcher()
    LegacyDispatchBackend(dispatcher=dispatcher).invoke(_invocation())
    call = dispatcher.calls[0]
    assert "runner_model" not in call
    assert "repo" not in call
    assert "change_id" not in call
    assert "extra_skills" not in call


def test_extra_skills_forwarded_as_list():
    dispatcher = RecordingDispatcher()
    LegacyDispatchBackend(dispatcher=dispatcher).invoke(
        _invocation(metadata={"extra_skills": ("skill-a", "skill-b")})
    )
    assert dispatcher.calls[0]["extra_skills"] == ["skill-a", "skill-b"]


def test_dispatcher_called_exactly_once():
    dispatcher = RecordingDispatcher()
    LegacyDispatchBackend(dispatcher=dispatcher).invoke(_invocation())
    assert len(dispatcher.calls) == 1


def test_identity_preserved_and_telemetry_stays_none():
    dispatcher = RecordingDispatcher()
    result = LegacyDispatchBackend(dispatcher=dispatcher).invoke(
        _invocation(model="gpt-5")
    )
    assert (result.agent, result.runner, result.model) == ("coder", "codex", "gpt-5")
    for name in (
        "stdout",
        "stderr",
        "exit_code",
        "duration_ms",
        "tokens_in",
        "tokens_out",
        "cost_usd",
        "retry",
        "failover_attempts",
        "artifacts_touched",
        "session_log_ref",
    ):
        assert getattr(result, name) is None


# --------------------------------------------------------------------------
# Rejections (dispatcher must NOT be called)
# --------------------------------------------------------------------------


def test_prompt_ref_only_is_unsupported():
    dispatcher = RecordingDispatcher()
    inv = AgentInvocation(agent="coder", runner="codex", prompt_ref="ref://x")
    with pytest.raises(UnsupportedInvocationError):
        LegacyDispatchBackend(dispatcher=dispatcher).invoke(inv)
    assert dispatcher.calls == []


def test_arbitrary_metadata_not_forwarded():
    dispatcher = RecordingDispatcher()
    LegacyDispatchBackend(dispatcher=dispatcher).invoke(
        _invocation(metadata={"note": "annotation", "trace_hint": 3})
    )
    call = dispatcher.calls[0]
    assert set(call) == {"runner", "prompt", "agent"}


def test_invalid_extra_skills_is_unsupported():
    dispatcher = RecordingDispatcher()
    inv = _invocation(metadata={"extra_skills": "not-a-list"})
    with pytest.raises(UnsupportedInvocationError):
        LegacyDispatchBackend(dispatcher=dispatcher).invoke(inv)
    assert dispatcher.calls == []


def test_extra_skills_with_non_str_element_is_unsupported():
    dispatcher = RecordingDispatcher()
    inv = _invocation(metadata={"extra_skills": ["ok", 5]})
    with pytest.raises(UnsupportedInvocationError):
        LegacyDispatchBackend(dispatcher=dispatcher).invoke(inv)
    assert dispatcher.calls == []


@pytest.mark.parametrize(
    "control",
    [
        {"timeout_s": 30.0},
        {"timeout_s": 0},
        {"working_dir": "some/wd"},
        {"env_overrides": {"K": "V"}},
        {"env_overrides": {}},
        {"allowed_tools": ("read",)},
        {"allowed_tools": ()},
    ],
)
def test_unsupported_execution_controls_are_rejected(control):
    dispatcher = RecordingDispatcher()
    inv = _invocation(**control)
    with pytest.raises(UnsupportedInvocationError):
        LegacyDispatchBackend(dispatcher=dispatcher).invoke(inv)
    assert dispatcher.calls == []


def test_trace_context_and_run_id_are_ignored_not_rejected():
    from runners.models import TraceContext

    dispatcher = RecordingDispatcher()
    inv = _invocation(
        trace_context=TraceContext(span_id="s1"), run_id="run-1", change_id="CHG-1"
    )
    result = LegacyDispatchBackend(dispatcher=dispatcher).invoke(inv)
    assert result.status is AgentResultStatus.SUCCEEDED
    call = dispatcher.calls[0]
    assert "trace_context" not in call
    assert "run_id" not in call
    assert call["change_id"] == "CHG-1"


# --------------------------------------------------------------------------
# Error handling
# --------------------------------------------------------------------------


def test_dispatcher_exception_wrapped_with_cause_and_no_prompt_in_message():
    def boom(**kwargs):
        raise RuntimeError("underlying failure")

    inv = _invocation(model="gpt-5", prompt="secret prompt text")
    with pytest.raises(RunnerInvocationError) as excinfo:
        LegacyDispatchBackend(dispatcher=boom).invoke(inv)
    err = excinfo.value
    assert isinstance(err.__cause__, RuntimeError)
    assert "codex" in str(err)
    assert "coder" in str(err)
    assert "gpt-5" in str(err)
    assert "secret prompt text" not in str(err)
    assert err.agent == "coder"
    assert err.runner == "codex"
    assert err.model == "gpt-5"


def test_keyboard_interrupt_propagates_unconverted():
    def boom(**kwargs):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        LegacyDispatchBackend(dispatcher=boom).invoke(_invocation())


def test_system_exit_propagates_unconverted():
    def boom(**kwargs):
        raise SystemExit(1)

    with pytest.raises(SystemExit):
        LegacyDispatchBackend(dispatcher=boom).invoke(_invocation())


# --------------------------------------------------------------------------
# Default (lazy) wiring
# --------------------------------------------------------------------------


def test_default_path_calls_lazily_imported_run_agent_cmd(monkeypatch):
    import core.agent_cmd as agent_cmd

    calls: list[dict] = []

    def fake_run_agent_cmd(**kwargs):
        calls.append(kwargs)
        return "default-wired"

    monkeypatch.setattr(agent_cmd, "run_agent_cmd", fake_run_agent_cmd)

    backend = LegacyDispatchBackend()  # no injection -> lazy default
    result = backend.invoke(_invocation())
    assert result.status is AgentResultStatus.SUCCEEDED
    assert result.response_text == "default-wired"
    assert calls == [{"runner": "codex", "prompt": "write code", "agent": "coder"}]


# --------------------------------------------------------------------------
# Doc example (keeps the documented usage honest)
# --------------------------------------------------------------------------


def test_documented_example_roundtrips():
    dispatcher = RecordingDispatcher(response="patch applied")
    backend = LegacyDispatchBackend(dispatcher=dispatcher)
    invocation = AgentInvocation(
        agent="coder", runner="codex", model="gpt-5", prompt="apply the patch"
    )
    result = backend.invoke(invocation)
    assert result.status is AgentResultStatus.SUCCEEDED
    assert result.response_text == "patch applied"
    assert result.agent == "coder"
    assert result.runner == "codex"
    assert result.model == "gpt-5"
