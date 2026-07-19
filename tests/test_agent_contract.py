"""Deterministic tests for the runner contracts (Prompt 12).

Bare-function pytest style, matching `tests/test_workflow_models.py`.
Covers `AgentInvocation`, `AgentResult`, their supporting metadata types, and
the `from_completed_process` compatibility helper.
"""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess

import pytest

from runners import (
    AgentContractError,
    AgentInvocation,
    AgentResult,
    AgentResultStatus,
    FailoverMetadata,
    MetadataSerializationError,
    RetryMetadata,
    TraceContext,
    from_completed_process,
)


# --------------------------------------------------------------------------
# AgentInvocation
# --------------------------------------------------------------------------


def test_agent_invocation_minimal_valid_without_model():
    inv = AgentInvocation(agent="planner", runner="claude", prompt="do the thing")
    assert inv.agent == "planner"
    assert inv.runner == "claude"
    assert inv.model is None
    assert inv.to_dict() == {"agent": "planner", "runner": "claude", "prompt": "do the thing"}


def test_agent_invocation_fully_populated_roundtrips():
    inv = AgentInvocation(
        agent="coder",
        runner="codex",
        model="gpt-5",
        prompt="write code",
        prompt_ref="ref://p/1",
        repo="some/repo",
        working_dir="some/wd",
        timeout_s=30.0,
        env_overrides={"K": "V"},
        allowed_tools=["read", "write"],
        trace_context=TraceContext(span_id="s1", parent_span_id="s0"),
        change_id="CHG-1",
        run_id="run-abc",
        metadata={"extra_skills": ["x"]},
    )
    assert AgentInvocation.from_dict(inv.to_dict()) == inv


@pytest.mark.parametrize(
    "kwargs",
    [
        {"agent": "", "runner": "claude", "prompt": "p"},
        {"agent": "planner", "runner": "", "prompt": "p"},
    ],
)
def test_agent_invocation_requires_nonempty_identity(kwargs):
    with pytest.raises(AgentContractError):
        AgentInvocation(**kwargs)


def test_agent_invocation_requires_a_prompt_source():
    with pytest.raises(AgentContractError, match="at least one of prompt or prompt_ref"):
        AgentInvocation(agent="planner", runner="claude")


def test_agent_invocation_accepts_only_prompt_ref():
    inv = AgentInvocation(agent="planner", runner="claude", prompt_ref="ref://p/1")
    assert inv.to_dict() == {"agent": "planner", "runner": "claude", "prompt_ref": "ref://p/1"}


def test_agent_invocation_empty_model_rejected_but_none_accepted():
    with pytest.raises(AgentContractError, match="model"):
        AgentInvocation(agent="a", runner="r", prompt="p", model="")
    inv = AgentInvocation(agent="a", runner="r", prompt="p", model=None)
    assert inv.model is None


@pytest.mark.parametrize("timeout_s", [0, 0.0, 5, 12.5])
def test_agent_invocation_accepts_zero_and_positive_timeout(timeout_s):
    inv = AgentInvocation(agent="a", runner="r", prompt="p", timeout_s=timeout_s)
    assert inv.timeout_s == timeout_s


def test_agent_invocation_rejects_negative_timeout():
    with pytest.raises(AgentContractError, match="timeout_s"):
        AgentInvocation(agent="a", runner="r", prompt="p", timeout_s=-1)


def test_agent_invocation_allows_custom_unknown_runner():
    inv = AgentInvocation(agent="a", runner="my-bespoke-runner", prompt="p")
    assert inv.runner == "my-bespoke-runner"


def test_agent_invocation_coerces_and_serializes_paths_as_str():
    inv = AgentInvocation(
        agent="a", runner="r", prompt="p", repo="some/repo", working_dir="some/wd"
    )
    assert inv.repo == Path("some/repo")
    assert inv.working_dir == Path("some/wd")
    payload = inv.to_dict()
    assert payload["repo"] == "some/repo"
    assert payload["working_dir"] == "some/wd"


def test_agent_invocation_env_overrides_tristate():
    absent = AgentInvocation(agent="a", runner="r", prompt="p")
    assert "env_overrides" not in absent.to_dict()

    empty = AgentInvocation(agent="a", runner="r", prompt="p", env_overrides={})
    assert empty.to_dict()["env_overrides"] == {}

    values = AgentInvocation(agent="a", runner="r", prompt="p", env_overrides={"K": "V"})
    assert values.to_dict()["env_overrides"] == {"K": "V"}


def test_agent_invocation_allowed_tools_tristate():
    absent = AgentInvocation(agent="a", runner="r", prompt="p")
    assert "allowed_tools" not in absent.to_dict()

    empty = AgentInvocation(agent="a", runner="r", prompt="p", allowed_tools=())
    assert empty.to_dict()["allowed_tools"] == []

    values = AgentInvocation(agent="a", runner="r", prompt="p", allowed_tools=["read"])
    assert values.to_dict()["allowed_tools"] == ["read"]


def test_agent_invocation_trace_context_and_run_identity_serialize():
    inv = AgentInvocation(
        agent="a",
        runner="r",
        prompt="p",
        trace_context=TraceContext(span_id="s1", parent_span_id="s0"),
        run_id="run-1",
        change_id="CHG-1",
    )
    payload = inv.to_dict()
    assert payload["trace_context"] == {"span_id": "s1", "parent_span_id": "s0"}
    assert payload["run_id"] == "run-1"
    assert payload["change_id"] == "CHG-1"


def test_agent_invocation_to_json_is_deterministic():
    inv = AgentInvocation(
        agent="a", runner="r", prompt="p", metadata={"b": 2, "a": 1}, env_overrides={"Z": "1"}
    )
    assert inv.to_json() == inv.to_json()
    # sorted keys -> agent precedes runner precedes ...
    assert inv.to_json().index('"agent"') < inv.to_json().index('"runner"')


def test_agent_invocation_construction_has_no_filesystem_side_effects():
    # A bogus, nonexistent path must not raise or be stat'd at construction.
    inv = AgentInvocation(
        agent="a", runner="r", prompt="p", repo="/no/such/path/xyz", working_dir="/also/missing"
    )
    assert inv.repo == Path("/no/such/path/xyz")
    assert not inv.repo.exists()


def test_agent_invocation_from_dict_ignores_unknown_top_level_keys():
    data = {"agent": "a", "runner": "r", "prompt": "p", "bogus": 123}
    inv = AgentInvocation.from_dict(data)
    assert inv.agent == "a"
    assert not hasattr(inv, "bogus")


def test_agent_invocation_metadata_non_json_raises_with_no_partial_output():
    inv = AgentInvocation(agent="a", runner="r", prompt="p", metadata={"bad": object()})
    with pytest.raises(MetadataSerializationError):
        inv.to_dict()


def test_agent_invocation_defensive_copy_of_metadata_and_env():
    meta = {"k": "v"}
    env = {"E": "1"}
    inv = AgentInvocation(agent="a", runner="r", prompt="p", metadata=meta, env_overrides=env)
    meta["k"] = "MUTATED"
    env["E"] = "MUTATED"
    assert inv.metadata == {"k": "v"}
    assert inv.env_overrides == {"E": "1"}


def test_agent_invocation_is_frozen():
    inv = AgentInvocation(agent="a", runner="r", prompt="p")
    with pytest.raises((AttributeError, TypeError)):
        inv.agent = "b"  # type: ignore[misc]


# --------------------------------------------------------------------------
# Supporting metadata types
# --------------------------------------------------------------------------


def test_retry_metadata_requires_attempt_count_ge_1():
    with pytest.raises(AgentContractError, match="attempt_count"):
        RetryMetadata(attempt_count=0)
    ok = RetryMetadata(attempt_count=1)
    assert ok.to_dict() == {"attempt_count": 1}


def test_retry_metadata_zero_retry_is_valid_and_full_roundtrips():
    retry = RetryMetadata(
        attempt_count=1, max_attempts=3, last_error_type="TimeoutError", retryable=True
    )
    assert RetryMetadata.from_dict(retry.to_dict()) == retry


def test_retry_metadata_rejects_max_attempts_below_one():
    with pytest.raises(AgentContractError, match="max_attempts"):
        RetryMetadata(attempt_count=1, max_attempts=0)


def test_failover_metadata_requires_index_ge_0_and_from_runner():
    with pytest.raises(AgentContractError, match="attempt_index"):
        FailoverMetadata(attempt_index=-1, from_runner="claude")
    with pytest.raises(AgentContractError, match="from_runner"):
        FailoverMetadata(attempt_index=0, from_runner="")


def test_failover_metadata_roundtrips():
    hop = FailoverMetadata(
        attempt_index=0,
        from_runner="claude",
        to_runner="codex",
        reason="rate_limited",
        from_model="opus",
        to_model="gpt-5",
    )
    assert FailoverMetadata.from_dict(hop.to_dict()) == hop


def test_trace_context_omits_none():
    assert TraceContext().to_dict() == {}
    assert TraceContext(span_id="s1").to_dict() == {"span_id": "s1"}


# --------------------------------------------------------------------------
# AgentResult
# --------------------------------------------------------------------------


def test_agent_result_succeeded_shape():
    result = AgentResult(status=AgentResultStatus.SUCCEEDED, stdout="done", exit_code=0)
    payload = result.to_dict()
    assert payload["status"] == "succeeded"
    assert payload["stdout"] == "done"
    assert payload["exit_code"] == 0


def test_agent_result_failed_with_structured_error():
    result = AgentResult(
        status=AgentResultStatus.FAILED,
        exit_code=1,
        error_type="RuntimeError",
        error_message="boom",
    )
    payload = result.to_dict()
    assert payload["status"] == "failed"
    assert payload["error_type"] == "RuntimeError"
    assert payload["error_message"] == "boom"


def test_agent_result_timed_out_status():
    result = AgentResult(status=AgentResultStatus.TIMED_OUT)
    assert result.to_dict()["status"] == "timed_out"


def test_agent_result_empty_successful_output_is_allowed():
    result = AgentResult(status=AgentResultStatus.SUCCEEDED, stdout="", response_text=None)
    payload = result.to_dict()
    assert payload["stdout"] == ""  # observed empty string kept
    assert "response_text" not in payload  # None omitted


def test_agent_result_keeps_stdout_and_stderr_separate():
    result = AgentResult(status=AgentResultStatus.FAILED, stdout="out", stderr="err")
    payload = result.to_dict()
    assert payload["stdout"] == "out"
    assert payload["stderr"] == "err"


def test_agent_result_missing_telemetry_stays_absent():
    result = AgentResult(status=AgentResultStatus.SUCCEEDED)
    payload = result.to_dict()
    for key in ("duration_ms", "tokens_in", "tokens_out", "cost_usd"):
        assert key not in payload


def test_agent_result_observed_zero_is_kept():
    result = AgentResult(
        status=AgentResultStatus.SUCCEEDED,
        duration_ms=0.0,
        tokens_in=0,
        tokens_out=0,
        cost_usd=0.0,
    )
    payload = result.to_dict()
    assert payload["duration_ms"] == 0.0
    assert payload["tokens_in"] == 0
    assert payload["tokens_out"] == 0
    assert payload["cost_usd"] == 0.0


def test_agent_result_retry_metadata_serializes_nested():
    result = AgentResult(
        status=AgentResultStatus.SUCCEEDED, retry=RetryMetadata(attempt_count=2, max_attempts=3)
    )
    assert result.to_dict()["retry"] == {"attempt_count": 2, "max_attempts": 3}


def test_agent_result_failover_attempts_tristate():
    none_case = AgentResult(status=AgentResultStatus.FAILED)
    assert "failover_attempts" not in none_case.to_dict()

    empty_case = AgentResult(status=AgentResultStatus.FAILED, failover_attempts=())
    assert empty_case.to_dict()["failover_attempts"] == []

    populated = AgentResult(
        status=AgentResultStatus.SUCCEEDED,
        failover_attempts=(
            FailoverMetadata(attempt_index=0, from_runner="claude", to_runner="codex"),
            FailoverMetadata(attempt_index=1, from_runner="codex", to_runner="gemini"),
        ),
    )
    hops = populated.to_dict()["failover_attempts"]
    # Order preserved.
    assert [h["attempt_index"] for h in hops] == [0, 1]
    assert hops[0]["to_runner"] == "codex"
    assert hops[1]["to_runner"] == "gemini"


def test_agent_result_artifacts_touched_tristate():
    none_case = AgentResult(status=AgentResultStatus.SUCCEEDED)
    assert "artifacts_touched" not in none_case.to_dict()

    empty_case = AgentResult(status=AgentResultStatus.SUCCEEDED, artifacts_touched=())
    assert empty_case.to_dict()["artifacts_touched"] == []

    populated = AgentResult(
        status=AgentResultStatus.SUCCEEDED, artifacts_touched=("a.py", "b.py")
    )
    assert populated.to_dict()["artifacts_touched"] == ["a.py", "b.py"]


def test_agent_result_to_json_is_deterministic():
    result = AgentResult(status=AgentResultStatus.SUCCEEDED, stdout="x", metadata={"b": 2, "a": 1})
    assert result.to_json() == result.to_json()


def test_agent_result_from_dict_roundtrips_and_ignores_unknown_keys():
    result = AgentResult(
        status=AgentResultStatus.SUCCEEDED,
        agent="coder",
        runner="codex",
        model="gpt-5",
        stdout="out",
        stderr="err",
        exit_code=0,
        duration_ms=12.0,
        retry=RetryMetadata(attempt_count=1),
        failover_attempts=(
            FailoverMetadata(attempt_index=0, from_runner="claude", to_runner="codex"),
        ),
        artifacts_touched=("a.py",),
        metadata={"k": "v"},
    )
    data = dict(result.to_dict())
    data["bogus_key"] = 999
    assert AgentResult.from_dict(data) == result


def test_agent_result_defensive_copy_of_metadata():
    meta = {"k": "v"}
    result = AgentResult(status=AgentResultStatus.SUCCEEDED, metadata=meta)
    meta["k"] = "MUTATED"
    assert result.metadata == {"k": "v"}


# --------------------------------------------------------------------------
# from_completed_process — compatibility proof
# --------------------------------------------------------------------------


def test_from_completed_process_success_maps_returncode_zero():
    proc = CompletedProcess(args=["x"], returncode=0, stdout="ok", stderr="")
    result = from_completed_process(proc, agent="coder", runner="codex", model="gpt-5")
    assert result.status is AgentResultStatus.SUCCEEDED
    assert result.exit_code == 0
    assert result.stdout == "ok"
    assert result.stderr == ""
    # Caller identity retained.
    assert (result.agent, result.runner, result.model) == ("coder", "codex", "gpt-5")


def test_from_completed_process_failure_maps_nonzero_returncode():
    proc = CompletedProcess(args=["x"], returncode=2, stdout="", stderr="bad")
    result = from_completed_process(proc)
    assert result.status is AgentResultStatus.FAILED
    assert result.exit_code == 2
    assert result.stderr == "bad"


def test_from_completed_process_preserves_stdout_and_stderr_separately():
    proc = CompletedProcess(args=["x"], returncode=0, stdout="OUT", stderr="ERR")
    result = from_completed_process(proc)
    assert result.stdout == "OUT"
    assert result.stderr == "ERR"


def test_from_completed_process_leaves_telemetry_missing():
    proc = CompletedProcess(args=["x"], returncode=0, stdout="", stderr="")
    result = from_completed_process(proc)
    payload = result.to_dict()
    for key in ("duration_ms", "tokens_in", "tokens_out", "cost_usd"):
        assert key not in payload


def test_from_completed_process_honors_explicit_timed_out_status():
    proc = CompletedProcess(args=["x"], returncode=0, stdout="", stderr="")
    result = from_completed_process(proc, status=AgentResultStatus.TIMED_OUT)
    # Explicit status wins even over a zero return code.
    assert result.status is AgentResultStatus.TIMED_OUT
