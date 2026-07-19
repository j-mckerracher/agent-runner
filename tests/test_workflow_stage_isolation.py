"""Import-isolation and instance-isolation checks for `workflow.stages`
(Prompt 9). Mirrors `tests/test_workflow_isolation.py`'s subprocess
`sys.modules` probe pattern.
"""

from __future__ import annotations

import subprocess
import sys

from workflow.models import RunContext, RunSpec
from workflow.stages import CallableStage, StageStatus


def _run_probe(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_importing_stage_contracts_does_not_import_opik_or_server():
    code = (
        "import sys\n"
        "from workflow import StageStatus, StageFailure, StageResult, WorkflowStage, CallableStage\n"
        "bad = [m for m in sys.modules if 'opik' in m.lower() or m == 'server' or m.startswith('server.')]\n"
        "assert not bad, bad\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_importing_workflow_stages_module_does_not_import_run_module():
    code = (
        "import sys\n"
        "import workflow.stages\n"
        "assert 'run' not in sys.modules, sys.modules.get('run')\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_running_callable_stage_does_not_import_run_opik_or_server():
    code = (
        "import sys\n"
        "from workflow.models import RunContext, RunSpec\n"
        "from workflow.stages import CallableStage\n"
        "stage = CallableStage('intake', lambda: 'ok')\n"
        "ctx = RunContext.for_spec(RunSpec())\n"
        "result = stage.run(ctx)\n"
        "assert result.output == 'ok'\n"
        "bad = [m for m in sys.modules if m == 'run' or 'opik' in m.lower() or m == 'server' or m.startswith('server.')]\n"
        "assert not bad, bad\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


# --------------------------------------------------------------------------
# Instance isolation (in-process, no subprocess needed)
# --------------------------------------------------------------------------


def test_separate_callable_stage_instances_have_independent_state():
    stage_a = CallableStage("intake", lambda: "a")
    stage_b = CallableStage("qa", lambda: "b")
    ctx_a = RunContext.for_spec(RunSpec())
    ctx_b = RunContext.for_spec(RunSpec())

    result_a = stage_a.run(ctx_a)
    result_b = stage_b.run(ctx_b)

    assert result_a.stage_name == "intake"
    assert result_b.stage_name == "qa"
    assert result_a.output == "a"
    assert result_b.output == "b"
    # No shared span id across independent invocations.
    assert result_a.span_id != result_b.span_id


def test_separate_run_contexts_do_not_leak_current_stage():
    ctx_a = RunContext.for_spec(RunSpec())
    ctx_b = RunContext.for_spec(RunSpec())
    stage = CallableStage("intake", lambda: "ok")

    stage.run(ctx_a)

    assert ctx_a.current_stage is None  # restored after success
    assert ctx_b.current_stage is None  # never touched


def test_current_stage_restored_after_failure_does_not_affect_other_context():
    ctx_a = RunContext.for_spec(RunSpec())
    ctx_b = RunContext.for_spec(RunSpec())
    ctx_b.current_stage = "unrelated-stage"

    def boom():
        raise ValueError("bad")

    stage = CallableStage("intake", boom)
    result = stage.run_capturing(ctx_a)

    assert result.status is StageStatus.FAILED
    assert ctx_a.current_stage is None
    assert ctx_b.current_stage == "unrelated-stage"


def test_callable_stage_constructor_defaults_are_not_shared_mutable_state():
    stage_a = CallableStage("intake", lambda: "a")
    stage_b = CallableStage("intake", lambda: "b")
    ctx = RunContext.for_spec(RunSpec())

    result_a = stage_a.run(ctx)
    result_b = stage_b.run(ctx)

    # Same stage name, independently constructed -- no cross-talk between
    # the two adapter instances' internal state (span factory, clock, sink).
    assert result_a.output == "a"
    assert result_b.output == "b"
    assert result_a.span_id != result_b.span_id
