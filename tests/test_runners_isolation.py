"""Import-isolation checks for the `runners` package (Prompt 12).

Mirrors the subprocess `sys.modules` probe pattern used in
`tests/test_workflow_isolation.py`: importing `runners` / `runners.models`
must never pull in `opik`, `server`, the `run` monolith, `telemetry`, or any
vendor SDK, and must start no subprocess — the contracts are a stdlib-only
leaf package.
"""

from __future__ import annotations

import subprocess
import sys


def _run_probe(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_importing_runners_pulls_in_no_heavy_or_vendor_modules():
    code = (
        "import sys\n"
        "from runners import AgentInvocation, AgentResult, from_completed_process\n"
        "forbidden = ('opik', 'server', 'run', 'telemetry', 'core', 'workflow',\n"
        "             'anthropic', 'openai')\n"
        "bad = [m for m in sys.modules\n"
        "       if any(m == f or m.startswith(f + '.') for f in forbidden)]\n"
        "assert not bad, bad\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_importing_runners_models_pulls_in_no_heavy_or_vendor_modules():
    code = (
        "import sys\n"
        "import runners.models\n"
        "forbidden = ('opik', 'server', 'run', 'telemetry', 'core', 'workflow',\n"
        "             'anthropic', 'openai')\n"
        "bad = [m for m in sys.modules\n"
        "       if any(m == f or m.startswith(f + '.') for f in forbidden)]\n"
        "assert not bad, bad\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_constructing_contracts_starts_no_subprocess():
    # Patch subprocess.Popen to explode if anything tries to spawn a process
    # while building the contracts, proving construction is pure.
    code = (
        "import subprocess\n"
        "def _boom(*a, **k):\n"
        "    raise AssertionError('no subprocess should be spawned')\n"
        "subprocess.Popen = _boom\n"
        "from runners import AgentInvocation, AgentResult, AgentResultStatus\n"
        "inv = AgentInvocation(agent='a', runner='r', prompt='p', repo='/no/such/path')\n"
        "res = AgentResult(status=AgentResultStatus.SUCCEEDED, stdout='x')\n"
        "inv.to_json(); res.to_json()\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_importing_failover_pulls_in_no_forbidden_modules():
    # Prompt 16 extension: importing + constructing the failover seam must not
    # load core/workflow/server/eval/telemetry/opik or any vendor SDK. The
    # google.* entries extend the base forbidden list per the Prompt 16 spec.
    code = (
        "import sys\n"
        "from runners import FailoverExecutor, FailoverPlan, RunnerRoute\n"
        "FailoverExecutor()\n"
        "forbidden = ('opik', 'server', 'run', 'telemetry', 'core', 'workflow',\n"
        "             'eval', 'anthropic', 'openai',\n"
        "             'google.generativeai', 'google.genai')\n"
        "bad = [m for m in sys.modules\n"
        "       if any(m == f or m.startswith(f + '.') for f in forbidden)]\n"
        "assert not bad, bad\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
