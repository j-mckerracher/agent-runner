"""Prompt 16 — behavior tests for the runner-layer failover seam.

Route validation, invocation preservation & no-mutation, resolution & laziness,
and result/metadata composition. Uses only in-process fake backends injected via
``RunnerRegistry(factories=...)`` — no real LLM/CLI/subprocess/network/vendor
calls.
"""

from __future__ import annotations

import pytest

from runners import (
    AgentInvocation,
    AgentResult,
    AgentResultStatus,
    FailoverConfigurationError,
    FailoverExecutor,
    FailoverExhaustedError,
    FailoverMetadata,
    FailoverMetadataConflictError,
    FailoverPlan,
    RegistryConfigurationError,
    RunnerBackend,
    RunnerRegistry,
    RunnerRoute,
    UnknownRunnerError,
    default_failover_eligibility,
)
from runners.base import RunnerInvocationError


def _invocation(**overrides) -> AgentInvocation:
    params: dict = {
        "agent": "planner",
        "runner": "claude",
        "model": "orig-model",
        "prompt": "hi",
    }
    params.update(overrides)
    return AgentInvocation(**params)


class SucceedingBackend(RunnerBackend):
    """Records invocations; returns a fully-populated SUCCEEDED result."""

    def __init__(self) -> None:
        self.calls: list[AgentInvocation] = []

    def invoke(self, invocation: AgentInvocation) -> AgentResult:
        self.calls.append(invocation)
        return AgentResult(
            status=AgentResultStatus.SUCCEEDED,
            agent=invocation.agent,
            runner=invocation.runner,
            model=invocation.model,
            response_text="done",
            stdout="out",
            stderr="err",
            exit_code=0,
            duration_ms=12.5,
            tokens_in=3,
            tokens_out=4,
            cost_usd=0.01,
            artifacts_touched=("a.txt", "b.txt"),
            metadata={"k": "v"},
        )


class FailingBackend(RunnerBackend):
    """Records invocations; returns a FAILED/TIMED_OUT result."""

    def __init__(
        self,
        status: AgentResultStatus = AgentResultStatus.FAILED,
        *,
        error_type: str | None = None,
    ) -> None:
        self.status = status
        self.error_type = error_type
        self.calls: list[AgentInvocation] = []

    def invoke(self, invocation: AgentInvocation) -> AgentResult:
        self.calls.append(invocation)
        return AgentResult(
            status=self.status,
            agent=invocation.agent,
            runner=invocation.runner,
            model=invocation.model,
            stdout="failout",
            error_type=self.error_type,
            error_message="boom detail",
        )


class RaisingBackend(RunnerBackend):
    """Records invocations; raises a configured exception from ``invoke``."""

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc
        self.calls: list[AgentInvocation] = []

    def invoke(self, invocation: AgentInvocation) -> AgentResult:
        self.calls.append(invocation)
        raise self.exc


class CountingFactory:
    """Wraps a backend instance; counts lazy resolution calls."""

    def __init__(self, backend: RunnerBackend) -> None:
        self.backend = backend
        self.count = 0

    def __call__(self) -> RunnerBackend:
        self.count += 1
        return self.backend


def _registry(**backends: RunnerBackend) -> tuple[RunnerRegistry, dict[str, CountingFactory]]:
    factories = {name: CountingFactory(be) for name, be in backends.items()}
    return RunnerRegistry(factories=factories), factories


# --- 1-6. Route + plan validation -------------------------------------------


def test_route_rejects_blank_runner():
    with pytest.raises(FailoverConfigurationError):
        RunnerRoute(runner="   ")


def test_route_rejects_non_string_runner():
    with pytest.raises(FailoverConfigurationError):
        RunnerRoute(runner=123)  # type: ignore[arg-type]


def test_route_rejects_empty_model():
    with pytest.raises(FailoverConfigurationError):
        RunnerRoute(runner="claude", model="")


def test_plan_rejects_empty_route():
    with pytest.raises(FailoverConfigurationError):
        FailoverPlan([])


def test_plan_rejects_duplicate_identical_attempts():
    with pytest.raises(FailoverConfigurationError):
        FailoverPlan([RunnerRoute("claude"), RunnerRoute("Claude")])


def test_plan_dedup_key_includes_model_and_uses_normalization():
    # Same runner (case-insensitive) but distinct models is allowed.
    plan = FailoverPlan([RunnerRoute("claude", "m1"), RunnerRoute("Claude", "m2")])
    assert len(plan.entries) == 2
    # Identical model + case-variant runner is a duplicate.
    with pytest.raises(FailoverConfigurationError):
        FailoverPlan([RunnerRoute("claude", "m1"), RunnerRoute("CLAUDE", "m1")])


def test_plan_coerces_mapping_entries():
    plan = FailoverPlan([{"runner": "claude"}, {"runner": "codex", "model": "x"}])
    assert plan.entries == (RunnerRoute("claude"), RunnerRoute("codex", "x"))


def test_plan_rejects_malformed_mapping_entry():
    with pytest.raises(FailoverConfigurationError):
        FailoverPlan([{"model": "x"}])  # missing runner
    with pytest.raises(FailoverConfigurationError):
        FailoverPlan([{"runner": "claude", "bogus": 1}])  # unknown key


def test_plan_rejects_non_route_entry():
    with pytest.raises(FailoverConfigurationError):
        FailoverPlan([42])  # type: ignore[list-item]


def test_plan_rejects_single_mapping_not_iterable_of_routes():
    with pytest.raises(FailoverConfigurationError):
        FailoverPlan({"runner": "claude"})  # a Mapping is not a route iterable


# --- 7-10. Invocation preservation & no-mutation ----------------------------


def test_primary_success_returns_result_without_hops():
    be = SucceedingBackend()
    reg, facs = _registry(claude=be)
    result = FailoverExecutor(reg).execute(_invocation(), FailoverPlan([RunnerRoute("claude")]))
    assert result.status == AgentResultStatus.SUCCEEDED
    assert result.failover_attempts is None
    assert facs["claude"].count == 1


def test_fresh_invocation_preserves_all_other_fields():
    be = SucceedingBackend()
    reg, _ = _registry(gemini=be)
    inv = _invocation(
        prompt="hello",
        env_overrides={"A": "1"},
        metadata={"nested": {"x": 1}},
        allowed_tools=("read",),
        change_id="C1",
        run_id="R1",
    )
    FailoverExecutor(reg).execute(inv, FailoverPlan([RunnerRoute("gemini", "gm")]))
    seen = be.calls[0]
    assert seen.runner == "gemini"
    assert seen.model == "gm"
    assert seen.prompt == "hello"
    assert seen.env_overrides == {"A": "1"}
    assert seen.metadata == {"nested": {"x": 1}}
    assert seen.change_id == "C1" and seen.run_id == "R1"


def test_original_invocation_not_mutated():
    be = SucceedingBackend()
    reg, _ = _registry(codex=be)
    inv = _invocation(metadata={"k": "v"})
    FailoverExecutor(reg).execute(inv, FailoverPlan([RunnerRoute("codex", "cm")]))
    assert inv.runner == "claude"
    assert inv.model == "orig-model"
    assert inv.metadata == {"k": "v"}
    # Nested metadata not shared with the derived attempt.
    assert be.calls[0].metadata is not inv.metadata


def test_route_model_none_inherits_original_not_previous():
    b1 = FailingBackend()
    b2 = FailingBackend()
    b3 = SucceedingBackend()
    reg, _ = _registry(claude=b1, codex=b2, gemini=b3)
    plan = FailoverPlan(
        [RunnerRoute("claude"), RunnerRoute("codex", "m2"), RunnerRoute("gemini")]
    )
    FailoverExecutor(reg).execute(_invocation(model="orig-model"), plan)
    assert b1.calls[0].model == "orig-model"
    assert b2.calls[0].model == "m2"
    # gemini has model=None -> inherits ORIGINAL, not codex's "m2".
    assert b3.calls[0].model == "orig-model"


# --- 11-16. Resolution & laziness -------------------------------------------


def test_later_routes_never_resolved_on_success():
    b1 = SucceedingBackend()
    b2 = SucceedingBackend()
    reg, facs = _registry(claude=b1, codex=b2)
    FailoverExecutor(reg).execute(
        _invocation(), FailoverPlan([RunnerRoute("claude"), RunnerRoute("codex")])
    )
    assert facs["claude"].count == 1
    assert facs["codex"].count == 0


def test_failover_resolves_next_route_lazily():
    b1 = FailingBackend()
    b2 = SucceedingBackend()
    reg, facs = _registry(claude=b1, codex=b2)
    FailoverExecutor(reg).execute(
        _invocation(), FailoverPlan([RunnerRoute("claude"), RunnerRoute("codex")])
    )
    assert facs["claude"].count == 1
    assert facs["codex"].count == 1


def test_registry_resolution_failure_is_eligible_and_fails_over():
    # First route resolves to an unknown runner -> UnknownRunnerError
    # (a RunnerBackendError) -> default eligibility continues to next route.
    b2 = SucceedingBackend()
    reg, _ = _registry(codex=b2)
    plan = FailoverPlan([RunnerRoute("no-such-runner"), RunnerRoute("codex")])
    result = FailoverExecutor(reg).execute(_invocation(), plan)
    assert result.status == AgentResultStatus.SUCCEEDED
    assert result.failover_attempts is not None
    assert result.failover_attempts[0].reason == "UnknownRunnerError"


def test_registry_resolution_failure_rejected_reraises_original():
    reg = RunnerRegistry()
    plan = FailoverPlan([RunnerRoute("no-such-runner"), RunnerRoute("claude")])
    # Predicate rejects everything -> the UnknownRunnerError is re-raised as-is.
    executor = FailoverExecutor(reg, eligibility=lambda **kw: False)
    with pytest.raises(UnknownRunnerError):
        executor.execute(_invocation(), plan)


def test_factory_error_is_runner_backend_error_and_eligible():
    b2 = SucceedingBackend()
    reg = RunnerRegistry(factories={"claude": lambda: object(), "codex": lambda: b2})
    plan = FailoverPlan([RunnerRoute("claude"), RunnerRoute("codex")])
    result = FailoverExecutor(reg).execute(_invocation(), plan)
    assert result.status == AgentResultStatus.SUCCEEDED
    assert result.failover_attempts[0].reason == "RegistryConfigurationError"


def test_backends_not_resolved_at_construction():
    b1 = SucceedingBackend()
    reg, facs = _registry(claude=b1)
    FailoverExecutor(reg)  # constructing the executor resolves nothing
    assert facs["claude"].count == 0


# --- 17-22. Result behavior & metadata composition --------------------------


def test_success_after_failover_preserves_result_fields():
    b1 = FailingBackend()
    b2 = SucceedingBackend()
    reg, _ = _registry(claude=b1, codex=b2)
    plan = FailoverPlan([RunnerRoute("claude"), RunnerRoute("codex")])
    result = FailoverExecutor(reg).execute(_invocation(), plan)
    assert result.stdout == "out" and result.stderr == "err"
    assert result.duration_ms == 12.5
    assert result.tokens_in == 3 and result.tokens_out == 4
    assert result.cost_usd == 0.01
    assert result.artifacts_touched == ("a.txt", "b.txt")
    assert result.metadata == {"k": "v"}
    assert len(result.failover_attempts) == 1


def test_hop_metadata_fields_on_transition():
    b1 = FailingBackend(error_type="quota")
    b2 = SucceedingBackend()
    reg, _ = _registry(claude=b1, codex=b2)
    plan = FailoverPlan([RunnerRoute("claude", "m1"), RunnerRoute("codex")])
    result = FailoverExecutor(reg).execute(_invocation(model="orig"), plan)
    (hop,) = result.failover_attempts
    assert hop.attempt_index == 0
    assert hop.from_runner == "claude" and hop.from_model == "m1"
    assert hop.to_runner == "codex" and hop.to_model == "orig"
    assert hop.reason == "failed:quota"


def test_hop_reason_timed_out_without_error_type():
    b1 = FailingBackend(status=AgentResultStatus.TIMED_OUT)
    b2 = SucceedingBackend()
    reg, _ = _registry(claude=b1, codex=b2)
    plan = FailoverPlan([RunnerRoute("claude"), RunnerRoute("codex")])
    result = FailoverExecutor(reg).execute(_invocation(), plan)
    assert result.failover_attempts[0].reason == "timed_out"


def test_ineligible_returned_failure_is_returned_with_hops():
    # First fails (eligible), second returns FAILED but predicate rejects the
    # second -> the second's result is returned, carrying the one hop from 0->1.
    b1 = FailingBackend()
    b2 = FailingBackend()
    reg, _ = _registry(claude=b1, codex=b2)
    plan = FailoverPlan([RunnerRoute("claude"), RunnerRoute("codex")])

    def _elig(*, invocation, result, error, attempt_index):
        return attempt_index == 0

    result = FailoverExecutor(reg, eligibility=_elig).execute(_invocation(), plan)
    assert result.status == AgentResultStatus.FAILED
    assert len(result.failover_attempts) == 1
    assert result.failover_attempts[0].attempt_index == 0


def test_ineligible_primary_failure_returned_without_hops():
    b1 = FailingBackend()
    reg, _ = _registry(claude=b1)
    plan = FailoverPlan([RunnerRoute("claude"), RunnerRoute("codex")])
    result = FailoverExecutor(reg, eligibility=lambda **kw: False).execute(
        _invocation(), plan
    )
    assert result.status == AgentResultStatus.FAILED
    assert result.failover_attempts is None


def test_backend_provided_metadata_composition_raises_conflict():
    class MetaSuccessThenBackend(RunnerBackend):
        def invoke(self, invocation):
            return AgentResult(
                status=AgentResultStatus.SUCCEEDED,
                failover_attempts=(
                    FailoverMetadata(attempt_index=0, from_runner="x", to_runner="y"),
                ),
            )

    b1 = FailingBackend()
    reg, _ = _registry(claude=b1, codex=MetaSuccessThenBackend())
    plan = FailoverPlan([RunnerRoute("claude"), RunnerRoute("codex")])
    with pytest.raises(FailoverMetadataConflictError):
        FailoverExecutor(reg).execute(_invocation(), plan)


def test_backend_metadata_preserved_when_no_failover():
    class MetaBackend(RunnerBackend):
        def invoke(self, invocation):
            return AgentResult(
                status=AgentResultStatus.SUCCEEDED,
                failover_attempts=(
                    FailoverMetadata(attempt_index=0, from_runner="x", to_runner="y"),
                ),
            )

    reg, _ = _registry(claude=MetaBackend())
    plan = FailoverPlan([RunnerRoute("claude")])
    result = FailoverExecutor(reg).execute(_invocation(), plan)
    assert result.failover_attempts == (
        FailoverMetadata(attempt_index=0, from_runner="x", to_runner="y"),
    )


# --- default eligibility unit ------------------------------------------------


def test_default_eligibility_matrix():
    inv = _invocation()
    fail = AgentResult(status=AgentResultStatus.FAILED)
    timed = AgentResult(status=AgentResultStatus.TIMED_OUT)
    ok = AgentResult(status=AgentResultStatus.SUCCEEDED)
    assert default_failover_eligibility(invocation=inv, result=fail, error=None, attempt_index=0)
    assert default_failover_eligibility(invocation=inv, result=timed, error=None, attempt_index=0)
    assert not default_failover_eligibility(invocation=inv, result=ok, error=None, attempt_index=0)
    err = RunnerInvocationError("x")
    assert default_failover_eligibility(invocation=inv, result=None, error=err, attempt_index=0)
    assert not default_failover_eligibility(
        invocation=inv, result=None, error=None, attempt_index=0
    )
