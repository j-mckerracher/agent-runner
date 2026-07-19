"""Prompt 17 — import-isolation checks for the runner lifecycle seam.

Mirrors the subprocess `sys.modules` probe in `tests/test_runners_isolation.py`.
Two guarantees:

* `import runners` still pulls in **no** `telemetry` (and no other heavy/legacy/
  vendor module): the lifecycle module is intentionally not re-exported from
  `runners/__init__.py`.
* `import runners.lifecycle` may import `telemetry` (it needs the trace
  contract) but none of `core`/`workflow`/`server`/`eval`/`opik` or any vendor
  SDK, and starts **no** subprocess.
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


def test_importing_runners_pulls_in_no_telemetry_or_vendor_modules():
    # runners/__init__.py must stay free of telemetry so `import runners`
    # remains a stdlib-only leaf — lifecycle is import-path-only.
    code = (
        "import sys\n"
        "import runners\n"
        "forbidden = ('telemetry', 'core', 'workflow', 'server', 'eval',\n"
        "             'opik', 'anthropic', 'openai',\n"
        "             'google.generativeai', 'google.genai')\n"
        "bad = [m for m in sys.modules\n"
        "       if any(m == f or m.startswith(f + '.') for f in forbidden)]\n"
        "assert not bad, bad\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_importing_lifecycle_pulls_in_no_forbidden_modules_and_no_subprocess():
    # Replace subprocess.Popen/run with a boom BEFORE importing lifecycle so a
    # successful import proves no subprocess was spawned. telemetry IS allowed.
    code = (
        "import subprocess\n"
        "def _boom(*a, **k):\n"
        "    raise AssertionError('no subprocess should be spawned')\n"
        "subprocess.Popen = _boom\n"
        "subprocess.run = _boom\n"
        "import sys\n"
        "from runners.lifecycle import InstrumentedRunnerBackend\n"
        "forbidden = ('core', 'workflow', 'server', 'eval', 'opik',\n"
        "             'anthropic', 'openai',\n"
        "             'google.generativeai', 'google.genai')\n"
        "bad = [m for m in sys.modules\n"
        "       if any(m == f or m.startswith(f + '.') for f in forbidden)]\n"
        "assert not bad, bad\n"
        "assert 'telemetry' in sys.modules  # telemetry is the allowed dependency\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
