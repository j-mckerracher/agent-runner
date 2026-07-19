"""Prompt 16 — exception & exhaustion tests for the runner-layer failover seam.

Covers `KeyboardInterrupt`/`SystemExit` propagation, `__cause__` chaining,
no-secret/no-prompt leakage in the exhaustion error, programmer errors not
being swallowed, exhaustion transition metadata, the `len(failover_attempts)+1`
attempt count, final runner/model identity, `final_result` exposure vs. its
exclusion from `str(exc)`, and the §10 typed-argument contract.
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
    FailoverPlan,
    RunnerBackend,
    RunnerBackendError,
    RunnerRegistry,
    RunnerRoute,
)
from runners.base import RunnerInvocationError


def _invocation(**overrides) -> AgentInvocation:
    params: dict = {
        "agent": "planner",
        "runner": "claude",
        "model": "orig-model",
        "prompt": "TOP-SECRET-PROMPT",
    }
    params.update(overrides)
    return AgentInvocation(**params)


class FailingBackend(RunnerBackend):
    def __init__(self, status: AgentResultStatus = AgentResultStatus.FAILED) -> None:
        self.status = status

    def invoke(self, invocation: AgentInvocation) -> AgentResult:
        return AgentResult(
            status=self.status,
            agent=invocation.agent,
            runner=invocation.runner,
            model=invocation.model,
            error_message="SECRET-STDERR-LEAK",
            stderr="SENSITIVE",
        )


class RaisingBackend(RunnerBackend):
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    def invoke(self, invocation: AgentInvocation) -> AgentResult:
        raise self.exc


def _registry(**backends: RunnerBackend) -> RunnerRegistry:
    return RunnerRegistry(factories={name: (lambda b=be: b) for name, be in backends.items()})


# --- 23-24. KeyboardInterrupt / SystemExit propagate ------------------------


def test_keyboard_interrupt_propagates_untouched():
    reg = _registry(claude=RaisingBackend(KeyboardInterrupt()))
    plan = FailoverPlan([RunnerRoute("claude"), RunnerRoute("codex")])
    with pytest.raises(KeyboardInterrupt):
        FailoverExecutor(reg).execute(_invocation(), plan)


def test_system_exit_propagates_untouched():
    reg = _registry(claude=RaisingBackend(SystemExit(2)))
    plan = FailoverPlan([RunnerRoute("claude"), RunnerRoute("codex")])
    with pytest.raises(SystemExit):
        FailoverExecutor(reg).execute(_invocation(), plan)


# --- 25. programmer errors are not swallowed --------------------------------


def test_non_runner_backend_error_propagates_unwrapped():
    boom = ValueError("programmer bug")
    reg = _registry(claude=RaisingBackend(boom))
    plan = FailoverPlan([RunnerRoute("claude"), RunnerRoute("codex")])
    with pytest.raises(ValueError) as ei:
        FailoverExecutor(reg).execute(_invocation(), plan)
    assert ei.value is boom


# --- 26-30. Exhaustion ------------------------------------------------------


def test_all_raising_routes_exhaust_with_cause():
    final = RunnerInvocationError("last dispatch failed")
    reg = _registry(
        claude=RaisingBackend(RunnerInvocationError("first")),
        codex=RaisingBackend(final),
    )
    plan = FailoverPlan([RunnerRoute("claude"), RunnerRoute("codex")])
    with pytest.raises(FailoverExhaustedError) as ei:
        FailoverExecutor(reg).execute(_invocation(), plan)
    exc = ei.value
    assert exc.__cause__ is final
    assert exc.final_result is None
    # One hop (0->1), two attempts total.
    assert len(exc.failover_attempts) == 1
    assert exc.failover_attempts[0].attempt_index == 0


def test_exhaustion_final_result_exposed_when_last_returned_failure():
    reg = _registry(claude=FailingBackend(), codex=FailingBackend())
    plan = FailoverPlan([RunnerRoute("claude"), RunnerRoute("codex", "cm")])
    with pytest.raises(FailoverExhaustedError) as ei:
        FailoverExecutor(reg).execute(_invocation(), plan)
    exc = ei.value
    assert exc.final_result is not None
    assert exc.final_result.status == AgentResultStatus.FAILED
    assert exc.__cause__ is None


def test_exhaustion_attempt_count_and_final_identity():
    reg = _registry(
        claude=FailingBackend(),
        codex=FailingBackend(),
        gemini=FailingBackend(),
    )
    plan = FailoverPlan(
        [RunnerRoute("claude"), RunnerRoute("codex"), RunnerRoute("gemini", "gm")]
    )
    with pytest.raises(FailoverExhaustedError) as ei:
        FailoverExecutor(reg).execute(_invocation(model="orig"), plan)
    exc = ei.value
    # 3 failed routes -> 2 hop records; total attempts = len+1 = 3.
    assert len(exc.failover_attempts) == 2
    assert len(exc.failover_attempts) + 1 == 3
    # Final runner/model identity carried on the exception base.
    assert exc.runner == "gemini"
    assert exc.model == "gm"
    assert exc.agent == "planner"
    # Ordered transition indices.
    assert [h.attempt_index for h in exc.failover_attempts] == [0, 1]


def test_exhaustion_message_excludes_prompt_and_secrets():
    reg = _registry(claude=FailingBackend(), codex=FailingBackend())
    plan = FailoverPlan([RunnerRoute("claude"), RunnerRoute("codex")])
    with pytest.raises(FailoverExhaustedError) as ei:
        FailoverExecutor(reg).execute(_invocation(), plan)
    rendered = f"{ei.value}" + repr(ei.value)
    assert "TOP-SECRET-PROMPT" not in rendered
    assert "SECRET-STDERR-LEAK" not in rendered
    assert "SENSITIVE" not in rendered
    # final_result carries the sensitive data but is not in the string form.
    assert ei.value.final_result is not None


def test_exhaustion_is_runner_backend_error():
    reg = _registry(claude=FailingBackend())
    plan = FailoverPlan([RunnerRoute("claude")])
    with pytest.raises(RunnerBackendError):
        FailoverExecutor(reg).execute(_invocation(), plan)


def test_single_route_eligible_failure_exhausts_immediately():
    reg = _registry(claude=FailingBackend())
    plan = FailoverPlan([RunnerRoute("claude")])
    with pytest.raises(FailoverExhaustedError) as ei:
        FailoverExecutor(reg).execute(_invocation(), plan)
    assert ei.value.failover_attempts == ()  # no transitions
    assert ei.value.final_result is not None


# --- §10 typed-argument contract --------------------------------------------


def test_execute_rejects_non_invocation_argument():
    reg = _registry(claude=FailingBackend())
    plan = FailoverPlan([RunnerRoute("claude")])
    with pytest.raises(FailoverConfigurationError):
        FailoverExecutor(reg).execute("not-an-invocation", plan)  # type: ignore[arg-type]


def test_execute_rejects_non_plan_argument():
    reg = _registry(claude=FailingBackend())
    with pytest.raises(FailoverConfigurationError):
        FailoverExecutor(reg).execute(_invocation(), [RunnerRoute("claude")])  # type: ignore[arg-type]
