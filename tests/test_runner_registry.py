"""Prompt 15 — behavior + import-isolation tests for `runners.registry`.

Mirrors the conventions in `tests/test_runner_adapters.py`,
`tests/test_runner_backend.py`, and `tests/test_runners_isolation.py`:
`from __future__ import annotations`, bare pytest functions (no classes), a
local `_invocation(**overrides)` helper, constructor-injected fakes,
`pytest.mark.parametrize` where practical, and the `_run_probe` subprocess
`sys.modules` pattern with the shared `_FORBIDDEN` tuple.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from runners import (
    BuiltinOpenAICompatBackend,
    ClaudeBackend,
    CodexBackend,
    CopilotBackend,
    GeminiBackend,
    InvalidRunnerNameError,
    OpenAICompatAliasBackend,
    RegistryConfigurationError,
    RunnerBackend,
    RunnerRegistry,
    RunnerSelectionError,
    UnknownRunnerError,
    resolve_backend,
)
from runners.models import AgentInvocation, AgentResult, AgentResultStatus
from runners.registry import BUILTIN_RUNNERS

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


def _invocation(**overrides) -> AgentInvocation:
    params: dict = {"agent": "planner", "runner": "claude", "prompt": "hi"}
    params.update(overrides)
    return AgentInvocation(**params)


class FakeBackend(RunnerBackend):
    """Records invocations; returns a SUCCEEDED result. No LLM/CLI/network."""

    def __init__(self) -> None:
        self.calls: list[AgentInvocation] = []

    def invoke(self, invocation: AgentInvocation) -> AgentResult:
        self.calls.append(invocation)
        return AgentResult(status=AgentResultStatus.SUCCEEDED)


class RecordingFactory:
    """Counts calls; returns a NEW backend each call (fresh-instance semantics)."""

    def __init__(self) -> None:
        self.count = 0

    def __call__(self) -> RunnerBackend:
        self.count += 1
        return FakeBackend()


class FixedFactory:
    """Returns the SAME injected instance each call (identity injection)."""

    def __init__(self, backend: RunnerBackend) -> None:
        self.backend = backend
        self.count = 0

    def __call__(self) -> RunnerBackend:
        self.count += 1
        return self.backend


class RecordingCallable:
    """Fake family fn: records kwargs, returns a configured string."""

    def __init__(self, response: str = "dispatched-text") -> None:
        self.response = response
        self.calls: list[dict] = []

    def __call__(self, **kwargs) -> str:
        self.calls.append(kwargs)
        return self.response


def _run_probe(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
    )


# --- 1. importable -----------------------------------------------------------

def test_registry_module_importable():
    import runners.registry as reg  # noqa: F401

    from runners import RunnerRegistry as RR

    assert RR is RunnerRegistry


# --- 2. public exports / private names --------------------------------------

def test_public_names_exported_private_absent():
    import runners

    for name in (
        "InvalidRunnerNameError",
        "RegistryConfigurationError",
        "RunnerRegistry",
        "RunnerSelectionError",
        "UnknownRunnerError",
        "resolve_backend",
    ):
        assert name in runners.__all__, name
    for private in ("BUILTIN_RUNNERS", "_DEFAULT_FACTORIES", "_normalize"):
        assert private not in runners.__all__


# --- 3. each built-in name -> expected adapter type -------------------------

@pytest.mark.parametrize(
    "runner, expected",
    [
        ("claude", ClaudeBackend),
        ("codex", CodexBackend),
        ("gemini", GeminiBackend),
        ("copilot", CopilotBackend),
        ("openai-compat", BuiltinOpenAICompatBackend),
    ],
)
def test_builtin_name_resolves_to_expected_adapter(runner, expected):
    assert isinstance(RunnerRegistry().resolve(runner), expected)


# --- 4. built-in wins over alias detection ----------------------------------

def test_builtin_wins_over_alias():
    reg = RunnerRegistry(supports_openai_compat_alias=lambda r: True)
    assert isinstance(reg.resolve("claude"), ClaudeBackend)
    # copilot family also wins over an all-accepting predicate.
    assert isinstance(reg.resolve("openai-compat"), BuiltinOpenAICompatBackend)


# --- 5. recognized alias -> OpenAICompatAliasBackend ------------------------

def test_recognized_alias_resolves_to_alias_backend():
    reg = RunnerRegistry(supports_openai_compat_alias=lambda r: r == "myalias")
    assert isinstance(reg.resolve("myalias"), OpenAICompatAliasBackend)


# --- 6 / 23. alias preserves requested name at invoke -----------------------

def test_alias_forwards_original_runner_name():
    rec = RecordingCallable()
    reg = RunnerRegistry(supports_openai_compat_alias=lambda r: r == "myalias")
    backend = reg.resolve("myalias")
    assert isinstance(backend, OpenAICompatAliasBackend)
    # Inject the fake dispatch callable so no core import is needed.
    backend._backend = rec
    backend.invoke(_invocation(runner="myalias"))
    assert rec.calls and rec.calls[0]["runner"] == "myalias"


# --- 7. unknown runner -------------------------------------------------------

def test_unknown_runner_raises_with_supported():
    with pytest.raises(UnknownRunnerError) as ei:
        RunnerRegistry().resolve("nope")
    err = ei.value
    assert err.requested == "nope"
    assert err.normalized == "nope"
    assert err.supported == BUILTIN_RUNNERS
    msg = str(err)
    assert "nope" in msg
    for name in BUILTIN_RUNNERS:
        assert name in msg
    assert isinstance(err, RunnerSelectionError)


# --- 8/9/10/24. invalid names -----------------------------------------------

@pytest.mark.parametrize("name", ["", "   ", "\t", "\n "])
def test_empty_or_whitespace_name_invalid(name):
    with pytest.raises(InvalidRunnerNameError):
        RunnerRegistry().resolve(name)


@pytest.mark.parametrize("name", [None, 123, 3.5, object(), ["claude"]])
def test_non_string_name_invalid(name):
    with pytest.raises(InvalidRunnerNameError) as ei:
        RunnerRegistry().resolve(name)
    assert ei.value.requested is name


@pytest.mark.parametrize("name", [" claude", "claude "])
def test_leading_trailing_whitespace_is_unknown_not_invalid(name):
    # Dispatch does not strip(); a padded known name stays unknown.
    with pytest.raises(UnknownRunnerError):
        RunnerRegistry().resolve(name)


# --- 11. only the selected factory invoked ----------------------------------

def test_only_selected_factory_invoked():
    fc, fx, fg = RecordingFactory(), RecordingFactory(), RecordingFactory()
    reg = RunnerRegistry(factories={"claude": fc, "codex": fx, "gemini": fg})
    reg.resolve("codex")
    assert fx.count == 1
    assert fc.count == 0
    assert fg.count == 0


# --- 12. factories not invoked at construction ------------------------------

def test_factories_not_invoked_at_construction():
    facs = {k: RecordingFactory() for k in ("claude", "codex", "gemini")}
    RunnerRegistry(factories=facs)
    assert all(f.count == 0 for f in facs.values())


# --- 13. injected fake factory works ----------------------------------------

def test_injected_fixed_factory_returns_that_instance():
    fake = FakeBackend()
    reg = RunnerRegistry(factories={"claude": FixedFactory(fake)})
    assert reg.resolve("claude") is fake


# --- 14. factory returning non-RunnerBackend --------------------------------

def test_factory_returning_non_backend_raises_config_error():
    reg = RunnerRegistry(factories={"claude": lambda: object()})
    with pytest.raises(RegistryConfigurationError):
        reg.resolve("claude")


# --- 15. factory raising -----------------------------------------------------

def test_factory_raising_wraps_with_cause():
    boom = RuntimeError("kaboom")

    def _factory() -> RunnerBackend:
        raise boom

    reg = RunnerRegistry(factories={"claude": _factory})
    with pytest.raises(RegistryConfigurationError) as ei:
        reg.resolve("claude")
    assert ei.value.__cause__ is boom


# --- 16. repeated resolution returns fresh instances ------------------------

def test_repeated_resolution_default_returns_fresh_instances():
    reg = RunnerRegistry()
    assert reg.resolve("claude") is not reg.resolve("claude")


def test_recording_factory_yields_distinct_objects():
    fc = RecordingFactory()
    reg = RunnerRegistry(factories={"claude": fc})
    assert reg.resolve("claude") is not reg.resolve("claude")
    assert fc.count == 2


def test_fixed_factory_same_object_is_not_cache_regression():
    fake = FakeBackend()
    ff = FixedFactory(fake)
    reg = RunnerRegistry(factories={"claude": ff})
    # Same object by intent (identity injection), not a registry cache.
    assert reg.resolve("claude") is reg.resolve("claude") is fake
    assert ff.count == 2


# --- 17. resolving does not call the backend command ------------------------

def test_resolving_does_not_invoke_backend_command():
    fake = FakeBackend()
    reg = RunnerRegistry(factories={"claude": FixedFactory(fake)})
    reg.resolve("claude")
    assert fake.calls == []


def test_builtin_resolve_imports_no_core_subprocess():
    code = (
        "import sys\n"
        "from runners import RunnerRegistry\n"
        "RunnerRegistry().resolve('claude')\n"
        "RunnerRegistry().resolve('copilot-gemma4')\n"
        "RunnerRegistry().resolve('openai-compat')\n"
        f"forbidden = {_FORBIDDEN!r}\n"
        "bad = [m for m in sys.modules\n"
        "       if any(m == f or m.startswith(f + '.') for f in forbidden)]\n"
        "assert not bad, bad\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


# --- 18/19. import pulls in no forbidden / vendor modules -------------------

def test_importing_registry_pulls_in_no_heavy_or_vendor_modules():
    code = (
        "import sys\n"
        "import runners.registry\n"
        "from runners import RunnerRegistry, resolve_backend\n"
        f"forbidden = {_FORBIDDEN!r}\n"
        "bad = [m for m in sys.modules\n"
        "       if any(m == f or m.startswith(f + '.') for f in forbidden)]\n"
        "assert not bad, bad\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


# --- 20. existing adapter ctor-injection still valid through registry -------

def test_adapter_ctor_injection_forwards_through_registry():
    rec = RecordingCallable()
    reg = RunnerRegistry(factories={"claude": lambda: ClaudeBackend(backend=rec)})
    backend = reg.resolve("claude")
    result = backend.invoke(_invocation(runner="claude"))
    assert result.status is AgentResultStatus.SUCCEEDED
    assert rec.calls and rec.calls[0]["agent"] == "planner"


# --- 21. copilot family ------------------------------------------------------

@pytest.mark.parametrize(
    "runner", ["copilot", "copilot-gemma4", "Copilot-Gemma4", "copilot-"]
)
def test_copilot_family_resolves_to_copilot_backend(runner):
    reg = RunnerRegistry(supports_openai_compat_alias=lambda r: True)
    assert isinstance(reg.resolve(runner), CopilotBackend)


# --- 22. copilot identity forwarding (alias not collapsed) ------------------

def test_copilot_alias_forwards_original_name_as_cli_cmd():
    rec = RecordingCallable()
    reg = RunnerRegistry(factories={"copilot": lambda: CopilotBackend(backend=rec)})
    backend = reg.resolve("copilot-gemma4")
    assert isinstance(backend, CopilotBackend)
    backend.invoke(_invocation(runner="copilot-gemma4", model=None))
    assert rec.calls and rec.calls[0]["cli_cmd"] == "copilot-gemma4"


# --- 25. alias-predicate failure --------------------------------------------

def test_alias_predicate_raising_wraps_with_cause():
    boom = RuntimeError("predicate boom")

    def _pred(runner: str) -> bool:
        raise boom

    reg = RunnerRegistry(supports_openai_compat_alias=_pred)
    with pytest.raises(RegistryConfigurationError) as ei:
        reg.resolve("myalias")
    assert ei.value.__cause__ is boom


def test_alias_predicate_keyboardinterrupt_propagates():
    def _pred(runner: str) -> bool:
        raise KeyboardInterrupt

    reg = RunnerRegistry(supports_openai_compat_alias=_pred)
    with pytest.raises(KeyboardInterrupt):
        reg.resolve("myalias")


# --- 26. constructor config validation --------------------------------------

def test_non_callable_factory_value_rejected():
    with pytest.raises(RegistryConfigurationError):
        RunnerRegistry(factories={"claude": "not-callable"})


@pytest.mark.parametrize("key", ["", "   ", 123, None])
def test_bad_factory_key_rejected(key):
    with pytest.raises(RegistryConfigurationError):
        RunnerRegistry(factories={key: FakeBackend})


def test_non_mapping_factories_rejected():
    with pytest.raises(RegistryConfigurationError):
        RunnerRegistry(factories=[("claude", FakeBackend)])


def test_non_callable_alias_predicate_rejected():
    with pytest.raises(RegistryConfigurationError):
        RunnerRegistry(supports_openai_compat_alias="nope")


def test_override_keeps_all_builtins_resolvable():
    fake = FakeBackend()
    reg = RunnerRegistry(factories={"claude": FixedFactory(fake)})
    # Overridden built-in uses the injected factory...
    assert reg.resolve("claude") is fake
    # ...and the other four built-ins remain resolvable (not orphaned).
    assert isinstance(reg.resolve("codex"), CodexBackend)
    assert isinstance(reg.resolve("gemini"), GeminiBackend)
    assert isinstance(reg.resolve("copilot"), CopilotBackend)
    assert isinstance(reg.resolve("openai-compat"), BuiltinOpenAICompatBackend)


# --- 27. resolve_backend convenience ----------------------------------------

def test_resolve_backend_matches_class_for_builtin():
    assert isinstance(resolve_backend("claude"), ClaudeBackend)
    assert isinstance(resolve_backend("copilot-x"), CopilotBackend)


def test_resolve_backend_supports_injected_alias():
    backend = resolve_backend(
        "myalias", supports_openai_compat_alias=lambda r: r == "myalias"
    )
    assert isinstance(backend, OpenAICompatAliasBackend)


def test_resolve_backend_without_predicate_rejects_alias():
    with pytest.raises(UnknownRunnerError):
        resolve_backend("myalias")
