"""Import-isolation checks for `workflow.live_artifacts` (Prompt 23).

Mirrors the subprocess `sys.modules` probe pattern in
`tests/test_workflow_isolation.py`: importing `artifacts` must never pull in
`workflow` or `telemetry`, and importing `workflow.live_artifacts` must never
pull in `run`, `server`, or `opik` — the collector is a leaf module usable
without any orchestration/server/vendor-SDK weight.
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


def test_importing_artifacts_does_not_import_workflow_or_telemetry():
    code = (
        "import sys\n"
        "import artifacts\n"
        "bad = [m for m in sys.modules if m == 'workflow' or m.startswith('workflow.')"
        " or m == 'telemetry' or m.startswith('telemetry.')]\n"
        "assert not bad, bad\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_importing_live_artifacts_does_not_import_run_server_or_opik():
    code = (
        "import sys\n"
        "from workflow.live_artifacts import LiveArtifactCollector\n"
        "bad = [m for m in sys.modules if m == 'run' or m == 'server' or m.startswith('server.')"
        " or 'opik' in m.lower()]\n"
        "assert not bad, bad\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_constructing_and_binding_collector_does_not_import_run_server_or_opik():
    code = (
        "import sys\n"
        "import tempfile\n"
        "from pathlib import Path\n"
        "from workflow.live_artifacts import LiveArtifactCollector\n"
        "collector = LiveArtifactCollector(run_id='r1', sink=None)\n"
        "with tempfile.TemporaryDirectory() as d:\n"
        "    collector.bind(Path(d))\n"
        "    collector.register_stage_outputs('intake')\n"
        "bad = [m for m in sys.modules if m == 'run' or m == 'server' or m.startswith('server.')"
        " or 'opik' in m.lower()]\n"
        "assert not bad, bad\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
