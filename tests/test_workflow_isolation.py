"""Import-isolation checks for the `workflow` package (Prompt 8).

Mirrors the subprocess `sys.modules` probe pattern used in
`tests/test_cli_isolation.py`: importing `workflow` (and constructing a
`WorkflowRunner` with an injected fake callable) must never pull in
`opik` or `server`, since neither is needed to exercise the shell.
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


def test_importing_workflow_package_does_not_import_opik_or_server():
    code = (
        "import sys\n"
        "from workflow import RunSpec, RunContext, WorkflowResult, WorkflowRunner\n"
        "bad = [m for m in sys.modules if 'opik' in m.lower() or m == 'server' or m.startswith('server.')]\n"
        "assert not bad, bad\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_importing_workflow_package_does_not_import_run_module():
    """`run.py` is only imported lazily inside the default adapter, when it
    is actually invoked -- not at `workflow` package-import time.
    """
    code = (
        "import sys\n"
        "import workflow\n"
        "assert 'run' not in sys.modules, sys.modules.get('run')\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_constructing_runner_with_fake_callable_does_not_import_run_opik_or_server():
    code = (
        "import sys\n"
        "from workflow import RunSpec, WorkflowRunner\n"
        "runner = WorkflowRunner(legacy_workflow=lambda spec, ctx: 'ok')\n"
        "result = runner.run(RunSpec())\n"
        "assert result.final_output == 'ok'\n"
        "bad = [m for m in sys.modules if m == 'run' or 'opik' in m.lower() or m == 'server' or m.startswith('server.')]\n"
        "assert not bad, bad\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
