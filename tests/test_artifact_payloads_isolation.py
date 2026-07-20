"""Prompt 19 — import-isolation + no-side-effect checks for artifact payloads.

Mirrors `tests/test_artifact_ref_isolation.py`: importing `artifacts`,
`artifacts.payloads`, or `artifacts.validation` must pull in none of the heavy /
vendor module families — and crucially not PyYAML (loaded lazily only inside
`load*()`). Construction, validation, serialization, and the `ArtifactRef`
factory must touch no filesystem, subprocess, network, or environment.

`load*()` is intentionally excluded from the no-side-effect probe: it reads a
file by design. Its read-only guarantee is covered in `test_artifact_payloads.py`.
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


_FORBIDDEN = (
    "core",
    "workflow",
    "runners",
    "eval",
    "server",
    "telemetry",
    "opik",
    "anthropic",
    "openai",
    "google",
    "google.generativeai",
    "google.genai",
    "yaml",
)


def _import_probe(import_line: str) -> str:
    forbidden = repr(_FORBIDDEN)
    return (
        "import sys\n"
        f"{import_line}\n"
        f"forbidden = {forbidden}\n"
        "bad = [m for m in sys.modules\n"
        "       if any(m == f or m.startswith(f + '.') for f in forbidden)]\n"
        "assert not bad, bad\n"
        "print('OK')\n"
    )


def test_importing_artifacts_pulls_in_no_heavy_or_vendor_modules():
    result = _run_probe(
        _import_probe(
            "from artifacts import (\n"
            "    StoryArtifact, TaskPlanArtifact, AssignmentArtifact, UowSpecArtifact,\n"
            "    PLANNING_ARTIFACTS, ValidationResult, ValidationIssue, ValidationSeverity,\n"
            "    ArtifactLoadError, ArtifactValidationError,\n"
            ")"
        )
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_importing_artifacts_payloads_pulls_in_no_heavy_or_vendor_modules():
    result = _run_probe(_import_probe("import artifacts.payloads"))
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_importing_artifacts_validation_pulls_in_no_heavy_or_vendor_modules():
    result = _run_probe(_import_probe("import artifacts.validation"))
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_payload_ops_touch_no_fs_subprocess_network_or_env():
    # Patch every filesystem, subprocess, network, and environment entry point to
    # explode, then exercise the pure API surface: from_mapping, validate_payload,
    # to_dict/to_json, and to_artifact_ref (path and uri).
    code = (
        "import subprocess, builtins, socket, urllib.request, os\n"
        "from pathlib import Path\n"
        "def _boom(*a, **k):\n"
        "    raise AssertionError('no FS/subprocess/network/env access allowed')\n"
        "subprocess.Popen = _boom\n"
        "subprocess.run = _boom\n"
        "builtins.open = _boom\n"
        "Path.read_text = _boom\n"
        "Path.write_text = _boom\n"
        "Path.exists = _boom\n"
        "socket.socket = _boom\n"
        "socket.create_connection = _boom\n"
        "urllib.request.urlopen = _boom\n"
        "os.getenv = _boom\n"
        "os.environ.get = _boom\n"
        "from artifacts import StoryArtifact, AssignmentArtifact, ArtifactValidationStatus\n"
        "story = StoryArtifact.from_mapping({\n"
        "    'change_id': 'C1', 'title': 'T', 'description': 'D',\n"
        "    'acceptance_criteria': {'AC1': 'x'},\n"
        "})\n"
        "StoryArtifact.validate_payload({'change_id': 'C1'})\n"
        "story.to_dict(); story.to_json()\n"
        "ref_path = story.to_artifact_ref(path='C1/intake/story.yaml',\n"
        "    validation_status=ArtifactValidationStatus.VALID, checksum_sha256='a'*64,\n"
        "    metadata={'k': {'n': [1, 2]}})\n"
        "ref_uri = story.to_artifact_ref(uri='s3://bucket/story.yaml')\n"
        "ref_path.to_dict(); ref_uri.to_dict()\n"
        "AssignmentArtifact.validate_payload({'batches': 'bad'})\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
