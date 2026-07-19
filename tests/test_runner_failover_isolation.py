"""Prompt 16 — import-isolation checks for `runners.failover`.

Mirrors the subprocess `sys.modules` probe used by `test_runner_registry.py`
and `test_runners_isolation.py`: importing `runners.failover` and constructing a
`FailoverExecutor` must load nothing from `core`, `workflow`, `server`, `eval`,
`telemetry`, `opik`, or any vendor SDK, and must start no subprocess.

The forbidden set extends the existing repo lists with the two Google GenAI SDK
import paths (`google.generativeai`, `google.genai`) as a deliberate Prompt 16
extension.
"""

from __future__ import annotations

import subprocess
import sys

_FORBIDDEN = (
    "core",
    "workflow",
    "server",
    "eval",
    "telemetry",
    "opik",
    "anthropic",
    "openai",
    "google.generativeai",
    "google.genai",
)

_ASSERT_FORBIDDEN = (
    "forbidden = " + repr(_FORBIDDEN) + "\n"
    "bad = [m for m in sys.modules\n"
    "       if any(m == f or m.startswith(f + '.') for f in forbidden)]\n"
    "assert not bad, bad\n"
)


def _run_probe(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_importing_failover_pulls_in_no_forbidden_modules():
    code = "import sys\nimport runners.failover\n" + _ASSERT_FORBIDDEN + "print('OK')\n"
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_constructing_executor_pulls_in_no_forbidden_modules():
    code = (
        "import sys\n"
        "from runners.failover import FailoverExecutor\n"
        "FailoverExecutor()\n" + _ASSERT_FORBIDDEN + "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_importing_failover_via_package_stays_isolated():
    code = (
        "import sys\n"
        "from runners import FailoverExecutor, FailoverPlan, RunnerRoute\n"
        "FailoverExecutor()\n" + _ASSERT_FORBIDDEN + "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_constructing_failover_starts_no_subprocess():
    code = (
        "import subprocess\n"
        "def _boom(*a, **k):\n"
        "    raise AssertionError('no subprocess should be spawned')\n"
        "subprocess.Popen = _boom\n"
        "from runners import FailoverExecutor, FailoverPlan, RunnerRoute\n"
        "FailoverExecutor()\n"
        "FailoverPlan([RunnerRoute('claude'), RunnerRoute('codex')])\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
