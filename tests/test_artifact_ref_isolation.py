"""Import-isolation + no-side-effect checks for the `artifacts` package (Prompt 18).

Mirrors the subprocess `sys.modules` probe pattern used in
`tests/test_runners_isolation.py`: importing `artifacts` / `artifacts.models`
must never pull in `core`, `workflow`, `runners`, `eval`, `server`,
`telemetry`, `opik`, or any vendor SDK, and constructing / serializing an
`ArtifactRef` must touch no filesystem, subprocess, or network.
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


def test_importing_artifacts_pulls_in_no_heavy_or_vendor_modules():
    code = (
        "import sys\n"
        "from artifacts import (\n"
        "    ArtifactRef, ArtifactValidationStatus, ArtifactRefValidationError,\n"
        "    ArtifactMetadataSerializationError, ARTIFACT_REF_SCHEMA_VERSION,\n"
        "    SUPPORTED_ARTIFACT_REF_SCHEMA_VERSIONS,\n"
        ")\n"
        "forbidden = ('core', 'workflow', 'runners', 'eval', 'server',\n"
        "             'telemetry', 'opik', 'anthropic', 'openai', 'google',\n"
        "             'google.generativeai', 'google.genai')\n"
        "bad = [m for m in sys.modules\n"
        "       if any(m == f or m.startswith(f + '.') for f in forbidden)]\n"
        "assert not bad, bad\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_importing_artifacts_models_pulls_in_no_heavy_or_vendor_modules():
    code = (
        "import sys\n"
        "import artifacts.models\n"
        "forbidden = ('core', 'workflow', 'runners', 'eval', 'server',\n"
        "             'telemetry', 'opik', 'anthropic', 'openai', 'google',\n"
        "             'google.generativeai', 'google.genai')\n"
        "bad = [m for m in sys.modules\n"
        "       if any(m == f or m.startswith(f + '.') for f in forbidden)]\n"
        "assert not bad, bad\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_constructing_and_serializing_touches_no_fs_subprocess_or_network():
    # Patch every filesystem, subprocess, and network entry point to explode,
    # then build a path-based ref and a URI-based ref and serialize both. The
    # URI ref is the path most likely to accidentally grow resolution behavior
    # later, so it is exercised explicitly.
    code = (
        "import subprocess, builtins, socket, urllib.request\n"
        "from pathlib import Path\n"
        "def _boom(*a, **k):\n"
        "    raise AssertionError('no FS/subprocess/network access allowed')\n"
        "subprocess.Popen = _boom\n"
        "subprocess.run = _boom\n"
        "builtins.open = _boom\n"
        "Path.read_text = _boom\n"
        "Path.exists = _boom\n"
        "socket.socket = _boom\n"
        "socket.create_connection = _boom\n"
        "urllib.request.urlopen = _boom\n"
        "from artifacts import ArtifactRef, ArtifactValidationStatus\n"
        "path_ref = ArtifactRef(\n"
        "    artifact_type='plan', path='planning/plan.json',\n"
        "    validation_status=ArtifactValidationStatus.VALID,\n"
        "    checksum_sha256='a' * 64, metadata={'k': {'n': [1, 2]}},\n"
        ")\n"
        "uri_ref = ArtifactRef(artifact_type='plan', uri='s3://bucket/plan.json')\n"
        "path_ref.to_dict(); path_ref.to_json()\n"
        "uri_ref.to_dict(); uri_ref.to_json()\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
