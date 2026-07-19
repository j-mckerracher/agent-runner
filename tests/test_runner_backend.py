"""Interface-level tests for `runners.base` (Prompt 13).

Bare-function pytest style, no conftest. Covers the `RunnerBackend` ABC
contract, a recording test double, and import-isolation probes proving the
interface pulls in none of the heavy/legacy/vendor stack.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from runners.base import (
    RunnerBackend,
    RunnerBackendError,
    RunnerInvocationError,
    UnsupportedInvocationError,
)
from runners.models import AgentInvocation, AgentResult, AgentResultStatus


class RecordingBackend(RunnerBackend):
    """Deterministic test double: records the invocation, returns a result."""

    def __init__(self, result: AgentResult | None = None) -> None:
        self.calls: list[AgentInvocation] = []
        self._result = result or AgentResult(status=AgentResultStatus.SUCCEEDED)

    def invoke(self, invocation: AgentInvocation) -> AgentResult:
        self.calls.append(invocation)
        return self._result


def _invocation(**overrides) -> AgentInvocation:
    base = {"agent": "planner", "runner": "claude", "prompt": "do it"}
    base.update(overrides)
    return AgentInvocation(**base)


def test_runner_backend_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        RunnerBackend()  # type: ignore[abstract]


def test_subclass_missing_invoke_cannot_be_instantiated():
    class Incomplete(RunnerBackend):
        pass

    with pytest.raises(TypeError):
        Incomplete()  # type: ignore[abstract]


def test_recording_backend_receives_exact_invocation_and_returns_result():
    result = AgentResult(status=AgentResultStatus.SUCCEEDED, response_text="ok")
    backend = RecordingBackend(result=result)
    inv = _invocation()
    returned = backend.invoke(inv)
    assert returned is result
    assert backend.calls == [inv]
    assert isinstance(returned, AgentResult)


def test_error_hierarchy_relationships():
    assert issubclass(RunnerInvocationError, RunnerBackendError)
    assert issubclass(UnsupportedInvocationError, RunnerInvocationError)
    assert issubclass(RunnerBackendError, RuntimeError)


def test_error_carries_identity_context_without_prompt():
    err = RunnerBackendError("boom", agent="planner", runner="claude", model="opus")
    assert err.agent == "planner"
    assert err.runner == "claude"
    assert err.model == "opus"
    assert "boom" in str(err)


def test_invoke_uses_no_subprocess():
    # Guard against any accidental process spawn from the interface layer.
    original = subprocess.Popen
    subprocess.Popen = lambda *a, **k: (_ for _ in ()).throw(  # type: ignore[assignment]
        AssertionError("no subprocess should be spawned")
    )
    try:
        RecordingBackend().invoke(_invocation())
    finally:
        subprocess.Popen = original  # type: ignore[assignment]


# --------------------------------------------------------------------------
# Import isolation (subprocess sys.modules probes)
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


def test_importing_runners_base_loads_no_heavy_or_vendor_modules():
    code = (
        "import sys\n"
        "from runners.base import RunnerBackend, RunnerBackendError, "
        "RunnerInvocationError, UnsupportedInvocationError\n"
        f"forbidden = {_FORBIDDEN!r}\n"
        "bad = [m for m in sys.modules\n"
        "       if any(m == f or m.startswith(f + '.') for f in forbidden)]\n"
        "assert not bad, bad\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_importing_runners_package_loads_no_core():
    code = (
        "import sys\n"
        "import runners\n"
        f"forbidden = {_FORBIDDEN!r}\n"
        "bad = [m for m in sys.modules\n"
        "       if any(m == f or m.startswith(f + '.') for f in forbidden)]\n"
        "assert not bad, bad\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_importing_legacy_backend_class_loads_no_core():
    # Importing the adapter class (not invoking it) must not load `core`,
    # proving the no-module-scope-import design.
    code = (
        "import sys\n"
        "from runners.legacy import LegacyDispatchBackend\n"
        "bad = [m for m in sys.modules if m == 'core' or m.startswith('core.')]\n"
        "assert not bad, bad\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
