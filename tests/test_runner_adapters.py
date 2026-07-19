"""Behavior tests for the Prompt 14 concrete runner backend adapters.

Covers `runners.claude.ClaudeBackend`, `runners.codex.CodexBackend`,
`runners.gemini.GeminiBackend`, `runners.copilot.CopilotBackend`,
`runners.omp.BuiltinOpenAICompatBackend`, and
`runners.openai_compat.OpenAICompatAliasBackend`.

Adapters are exercised with an injected `RecordingCallable` (no `core` import);
default lazy wiring is checked by monkeypatching `core.run_cmds`. Import
isolation is verified in fresh interpreters.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from runners import (
    BuiltinOpenAICompatBackend,
    ClaudeBackend,
    CodexBackend,
    CopilotBackend,
    GeminiBackend,
    OpenAICompatAliasBackend,
    RunnerBackend,
    RunnerInvocationError,
    UnsupportedInvocationError,
)
from runners.models import AgentInvocation, AgentResultStatus


class RecordingCallable:
    """Fake family fn: records kwargs, returns a configured string."""

    def __init__(self, response: str = "dispatched-text") -> None:
        self.response = response
        self.calls: list[dict] = []

    def __call__(self, **kwargs) -> str:
        self.calls.append(kwargs)
        return self.response


# Valid runner name per adapter.
RUNNERS = {
    ClaudeBackend: "claude",
    CodexBackend: "codex",
    GeminiBackend: "gemini",
    CopilotBackend: "copilot",
    BuiltinOpenAICompatBackend: "openai-compat",
    OpenAICompatAliasBackend: "myalias",
}

ADAPTERS = list(RUNNERS)

# Default lazy backend attribute on core.run_cmds per adapter.
FN_NAMES = {
    ClaudeBackend: "run_claude_cmd",
    CodexBackend: "run_codex_cmd",
    GeminiBackend: "run_gemini_cmd",
    CopilotBackend: "run_copilot_cmd",
    BuiltinOpenAICompatBackend: "run_omp_cmd",
    OpenAICompatAliasBackend: "run_openai_compat_cmd",
}

# Adapters that map metadata['extra_skills'] onto the backend.
SKILL_ADAPTERS = [CodexBackend, GeminiBackend, CopilotBackend, BuiltinOpenAICompatBackend, OpenAICompatAliasBackend]
# Adapters that accept repo / change_id.
REPO_ADAPTERS = [CodexBackend, BuiltinOpenAICompatBackend, OpenAICompatAliasBackend]
# Omit-when-None model families (fn default is a real model string).
OMIT_MODEL_ADAPTERS = [ClaudeBackend, CodexBackend, GeminiBackend, CopilotBackend]

# Telemetry that a bare-str return can never carry: must stay None.
_HONEST_NONE_FIELDS = (
    "stdout",
    "stderr",
    "exit_code",
    "duration_ms",
    "tokens_in",
    "tokens_out",
    "cost_usd",
    "error_type",
    "error_message",
    "retry",
    "failover_attempts",
    "artifacts_touched",
    "session_log_ref",
)


def _make(cls, backend, *, supports_runner=None):
    """Construct an adapter with an injected backend (predicate for the alias)."""
    if cls is OpenAICompatAliasBackend:
        pred = supports_runner if supports_runner is not None else (lambda r: True)
        return cls(backend=backend, supports_runner=pred)
    return cls(backend=backend)


def _inv(cls, **overrides) -> AgentInvocation:
    base = {"agent": "coder", "runner": RUNNERS[cls], "prompt": "write code"}
    base.update(overrides)
    return AgentInvocation(**base)


# --------------------------------------------------------------------------
# Interface
# --------------------------------------------------------------------------


@pytest.mark.parametrize("cls", ADAPTERS)
def test_adapter_is_runner_backend_subclass(cls):
    assert issubclass(cls, RunnerBackend)
    # Concrete: it can actually be instantiated (invoke implemented).
    assert isinstance(_make(cls, RecordingCallable()), RunnerBackend)


# --------------------------------------------------------------------------
# Success translation
# --------------------------------------------------------------------------


@pytest.mark.parametrize("cls", ADAPTERS)
def test_success_translation_status_identity_and_text(cls):
    rec = RecordingCallable("out-text")
    result = _make(cls, rec).invoke(_inv(cls))
    assert result.status is AgentResultStatus.SUCCEEDED
    assert result.agent == "coder"
    assert result.runner == RUNNERS[cls]
    assert result.response_text == "out-text"


@pytest.mark.parametrize("cls", ADAPTERS)
def test_prompt_and_agent_forwarded(cls):
    rec = RecordingCallable()
    _make(cls, rec).invoke(_inv(cls))
    call = rec.calls[0]
    assert call["prompt"] == "write code"
    assert call["agent"] == "coder"


@pytest.mark.parametrize("cls", ADAPTERS)
def test_backend_called_exactly_once(cls):
    rec = RecordingCallable()
    _make(cls, rec).invoke(_inv(cls))
    assert len(rec.calls) == 1


@pytest.mark.parametrize("cls", ADAPTERS)
def test_telemetry_fields_stay_none(cls):
    rec = RecordingCallable()
    result = _make(cls, rec).invoke(_inv(cls))
    for name in _HONEST_NONE_FIELDS:
        assert getattr(result, name) is None, name


@pytest.mark.parametrize("cls", ADAPTERS)
def test_invocation_not_mutated(cls):
    rec = RecordingCallable()
    inv = _inv(cls, model="m1", change_id="CHG-1", metadata={"extra_skills": ["s"]})
    before = inv.to_dict()
    try:
        _make(cls, rec).invoke(inv)
    except UnsupportedInvocationError:
        pass  # some families reject these; mutation must still not happen
    assert inv.to_dict() == before


# --------------------------------------------------------------------------
# Model translation
# --------------------------------------------------------------------------


@pytest.mark.parametrize("cls", OMIT_MODEL_ADAPTERS)
def test_model_omitted_when_none(cls):
    rec = RecordingCallable()
    _make(cls, rec).invoke(_inv(cls))
    assert "model" not in rec.calls[0]


@pytest.mark.parametrize("cls", OMIT_MODEL_ADAPTERS)
def test_model_forwarded_when_set(cls):
    rec = RecordingCallable()
    result = _make(cls, rec).invoke(_inv(cls, model="gpt-5"))
    assert rec.calls[0]["model"] == "gpt-5"
    assert result.model == "gpt-5"


def test_omp_passes_model_through_including_none():
    rec = RecordingCallable()
    _make(BuiltinOpenAICompatBackend, rec).invoke(_inv(BuiltinOpenAICompatBackend))
    assert "model" in rec.calls[0]
    assert rec.calls[0]["model"] is None
    rec2 = RecordingCallable()
    _make(BuiltinOpenAICompatBackend, rec2).invoke(_inv(BuiltinOpenAICompatBackend, model="m1"))
    assert rec2.calls[0]["model"] == "m1"


def test_alias_model_defaults_to_runner_and_passes_runner():
    rec = RecordingCallable()
    _make(OpenAICompatAliasBackend, rec).invoke(_inv(OpenAICompatAliasBackend))
    call = rec.calls[0]
    assert call["model"] == "myalias"  # inv.model or inv.runner
    assert call["runner"] == "myalias"

    rec2 = RecordingCallable()
    _make(OpenAICompatAliasBackend, rec2).invoke(_inv(OpenAICompatAliasBackend, model="m1"))
    call2 = rec2.calls[0]
    assert call2["model"] == "m1"
    assert call2["runner"] == "myalias"


# --------------------------------------------------------------------------
# repo / change_id / extra_skills mapping
# --------------------------------------------------------------------------


@pytest.mark.parametrize("cls", REPO_ADAPTERS)
def test_repo_forwarded_as_str(cls):
    rec = RecordingCallable()
    _make(cls, rec).invoke(_inv(cls, repo=Path("/tmp/repo")))
    assert rec.calls[0]["repo"] == "/tmp/repo"
    assert isinstance(rec.calls[0]["repo"], str)


@pytest.mark.parametrize("cls", REPO_ADAPTERS)
def test_change_id_forwarded(cls):
    rec = RecordingCallable()
    _make(cls, rec).invoke(_inv(cls, change_id="CHG-9"))
    assert rec.calls[0]["change_id"] == "CHG-9"


@pytest.mark.parametrize("cls", SKILL_ADAPTERS)
def test_extra_skills_forwarded_as_list(cls):
    rec = RecordingCallable()
    _make(cls, rec).invoke(_inv(cls, metadata={"extra_skills": ("a", "b")}))
    assert rec.calls[0]["extra_skills"] == ["a", "b"]


@pytest.mark.parametrize("cls", SKILL_ADAPTERS)
def test_invalid_extra_skills_rejected(cls):
    rec = RecordingCallable()
    with pytest.raises(UnsupportedInvocationError):
        _make(cls, rec).invoke(_inv(cls, metadata={"extra_skills": "not-a-list"}))
    assert rec.calls == []


@pytest.mark.parametrize("cls", [ClaudeBackend, GeminiBackend, CopilotBackend])
def test_repo_rejected(cls):
    rec = RecordingCallable()
    with pytest.raises(UnsupportedInvocationError):
        _make(cls, rec).invoke(_inv(cls, repo=Path("/tmp/repo")))
    assert rec.calls == []


@pytest.mark.parametrize("cls", [ClaudeBackend, GeminiBackend, CopilotBackend])
def test_change_id_rejected(cls):
    rec = RecordingCallable()
    with pytest.raises(UnsupportedInvocationError):
        _make(cls, rec).invoke(_inv(cls, change_id="CHG-1"))
    assert rec.calls == []


def test_claude_rejects_extra_skills():
    rec = RecordingCallable()
    with pytest.raises(UnsupportedInvocationError):
        ClaudeBackend(backend=rec).invoke(_inv(ClaudeBackend, metadata={"extra_skills": ["a"]}))
    assert rec.calls == []


# --------------------------------------------------------------------------
# Unsupported execution controls / prompt_ref
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "control",
    [
        {"timeout_s": 1.0},
        {"timeout_s": 0},
        {"working_dir": "/tmp/wd"},
        {"env_overrides": {}},
        {"env_overrides": {"A": "1"}},
        {"allowed_tools": ()},
        {"allowed_tools": ("read",)},
    ],
)
@pytest.mark.parametrize("cls", ADAPTERS)
def test_common_execution_controls_rejected(cls, control):
    rec = RecordingCallable()
    with pytest.raises(UnsupportedInvocationError):
        _make(cls, rec).invoke(_inv(cls, **control))
    assert rec.calls == []


@pytest.mark.parametrize("cls", ADAPTERS)
def test_prompt_ref_only_rejected(cls):
    rec = RecordingCallable()
    with pytest.raises(UnsupportedInvocationError):
        _make(cls, rec).invoke(_inv(cls, prompt=None, prompt_ref="ref://x"))
    assert rec.calls == []


# --------------------------------------------------------------------------
# Runner-name validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cls,bad_runner",
    [
        (ClaudeBackend, "codex"),
        (CodexBackend, "claude"),
        (GeminiBackend, "claude"),
        (CopilotBackend, "claude"),
        (BuiltinOpenAICompatBackend, "openai-compat-x"),
        (BuiltinOpenAICompatBackend, "claude"),
    ],
)
def test_foreign_runner_rejected(cls, bad_runner):
    rec = RecordingCallable()
    inv = AgentInvocation(agent="coder", runner=bad_runner, prompt="p")
    with pytest.raises(UnsupportedInvocationError):
        _make(cls, rec).invoke(inv)
    assert rec.calls == []


# --------------------------------------------------------------------------
# Copilot alias specifics
# --------------------------------------------------------------------------


def test_copilot_alias_uses_runner_as_cli_cmd_without_model():
    rec = RecordingCallable()
    inv = AgentInvocation(agent="coder", runner="copilot-gemma4", prompt="p")
    CopilotBackend(backend=rec).invoke(inv)
    call = rec.calls[0]
    assert call["cli_cmd"] == "copilot-gemma4"
    assert "model" not in call


def test_copilot_alias_with_model_rejected():
    rec = RecordingCallable()
    inv = AgentInvocation(agent="coder", runner="copilot-gemma4", prompt="p", model="x")
    with pytest.raises(UnsupportedInvocationError):
        CopilotBackend(backend=rec).invoke(inv)
    assert rec.calls == []


def test_copilot_base_passes_cli_cmd_and_model():
    rec = RecordingCallable()
    CopilotBackend(backend=rec).invoke(_inv(CopilotBackend, model="gpt-5-mini"))
    call = rec.calls[0]
    assert call["cli_cmd"] == "copilot"
    assert call["model"] == "gpt-5-mini"


# --------------------------------------------------------------------------
# openai-compat alias validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize("bad_runner", ["claude", "codex", "unknown-runner"])
def test_alias_unrecognized_runner_rejected(bad_runner):
    rec = RecordingCallable()
    inv = AgentInvocation(agent="coder", runner=bad_runner, prompt="p")
    backend = OpenAICompatAliasBackend(backend=rec, supports_runner=lambda r: False)
    with pytest.raises(UnsupportedInvocationError):
        backend.invoke(inv)
    assert rec.calls == []


def test_alias_recognized_runner_succeeds():
    rec = RecordingCallable("ok")
    inv = AgentInvocation(agent="coder", runner="my-oa-alias", prompt="p")
    backend = OpenAICompatAliasBackend(
        backend=rec, supports_runner=lambda r: r == "my-oa-alias"
    )
    result = backend.invoke(inv)
    assert result.status is AgentResultStatus.SUCCEEDED
    assert rec.calls[0]["runner"] == "my-oa-alias"
    assert rec.calls[0]["model"] == "my-oa-alias"


# --------------------------------------------------------------------------
# Error handling
# --------------------------------------------------------------------------


@pytest.mark.parametrize("cls", ADAPTERS)
def test_backend_exception_wrapped_with_cause_and_identity(cls):
    def boom(**kwargs):
        raise ValueError("secret boom")

    inv = _inv(cls, model="gpt-5")
    with pytest.raises(RunnerInvocationError) as excinfo:
        _make(cls, boom).invoke(inv)
    err = excinfo.value
    assert isinstance(err.__cause__, ValueError)
    assert err.agent == "coder"
    assert err.runner == RUNNERS[cls]
    assert err.model == "gpt-5"
    # No prompt text leaks into the message.
    assert "write code" not in str(err)


@pytest.mark.parametrize("cls", ADAPTERS)
def test_keyboard_interrupt_propagates_unconverted(cls):
    def boom(**kwargs):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _make(cls, boom).invoke(_inv(cls))


@pytest.mark.parametrize("cls", ADAPTERS)
def test_system_exit_propagates_unconverted(cls):
    def boom(**kwargs):
        raise SystemExit(2)

    with pytest.raises(SystemExit):
        _make(cls, boom).invoke(_inv(cls))


@pytest.mark.parametrize("cls", ADAPTERS)
@pytest.mark.parametrize("bad", [None, b"bytes", 42, ["list"]])
def test_non_str_return_rejected(cls, bad):
    def weird(**kwargs):
        return bad

    inv = _inv(cls)
    with pytest.raises(RunnerInvocationError) as excinfo:
        _make(cls, weird).invoke(inv)
    msg = str(excinfo.value)
    assert "write code" not in msg  # no prompt
    assert str(bad) not in msg or bad is None  # no content value (None sentinel aside)


# --------------------------------------------------------------------------
# Default (lazy) wiring
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cls", [ClaudeBackend, CodexBackend, GeminiBackend, CopilotBackend, BuiltinOpenAICompatBackend]
)
def test_lazy_default_calls_core_fn(cls, monkeypatch):
    import core.run_cmds as rc

    calls: list[dict] = []

    def fake(**kwargs):
        calls.append(kwargs)
        return "lazy-out"

    monkeypatch.setattr(rc, FN_NAMES[cls], fake)
    result = cls().invoke(_inv(cls))
    assert result.response_text == "lazy-out"
    assert len(calls) == 1


def test_lazy_default_alias_calls_core_fn(monkeypatch):
    import core.run_cmds as rc

    calls: list[dict] = []

    def fake(**kwargs):
        calls.append(kwargs)
        return "lazy-out"

    monkeypatch.setattr(rc, "run_openai_compat_cmd", fake)
    result = OpenAICompatAliasBackend(supports_runner=lambda r: True).invoke(
        _inv(OpenAICompatAliasBackend)
    )
    assert result.response_text == "lazy-out"
    assert len(calls) == 1


# --------------------------------------------------------------------------
# Import isolation (fresh-interpreter sys.modules probes)
# --------------------------------------------------------------------------


def _run_probe(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
    )


_FORBIDDEN = (
    "opik",
    "server",
    "run",
    "telemetry",
    "core",
    "workflow",
    "eval",
    "anthropic",
    "openai",
)

_MODULES = {
    "claude": "ClaudeBackend",
    "codex": "CodexBackend",
    "gemini": "GeminiBackend",
    "copilot": "CopilotBackend",
    "omp": "BuiltinOpenAICompatBackend",
    "openai_compat": "OpenAICompatAliasBackend",
}


@pytest.mark.parametrize("mod,cls", list(_MODULES.items()))
def test_importing_adapter_module_loads_no_heavy_or_vendor(mod, cls):
    code = (
        "import sys\n"
        f"from runners.{mod} import {cls}\n"
        f"forbidden = {_FORBIDDEN!r}\n"
        "bad = [m for m in sys.modules\n"
        "       if any(m == f or m.startswith(f + '.') for f in forbidden)]\n"
        "assert not bad, bad\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


_PROBE_RUNNERS = {
    "claude": "claude",
    "codex": "codex",
    "gemini": "gemini",
    "copilot": "copilot",
    "omp": "openai-compat",
    "openai_compat": "myalias",
}


@pytest.mark.parametrize("mod,cls", list(_MODULES.items()))
def test_invoking_adapter_with_injected_callable_loads_no_core(mod, cls):
    runner = _PROBE_RUNNERS[mod]
    ctor = f"{cls}(backend=lambda **k: 'ok')"
    if cls == "OpenAICompatAliasBackend":
        ctor = f"{cls}(backend=lambda **k: 'ok', supports_runner=lambda r: True)"
    code = (
        "import sys\n"
        f"from runners.{mod} import {cls}\n"
        "from runners.models import AgentInvocation\n"
        f"b = {ctor}\n"
        f"b.invoke(AgentInvocation(agent='a', runner={runner!r}, prompt='p'))\n"
        "bad = [m for m in sys.modules if m == 'core' or m.startswith('core.')]\n"
        "assert not bad, bad\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
