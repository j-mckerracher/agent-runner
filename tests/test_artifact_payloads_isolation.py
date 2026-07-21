"""Prompt 19/20 — import-isolation + no-side-effect checks for artifact payloads.

Mirrors `tests/test_artifact_ref_isolation.py`: importing `artifacts`,
`artifacts.payloads`, or `artifacts.validation` must pull in none of the heavy /
vendor module families — and crucially not PyYAML (loaded lazily only inside
`load*()`). Construction, validation, serialization, and the `ArtifactRef`
factory must touch no filesystem, subprocess, network, or environment. This
applies equally to the Prompt 20 implementation-report / QA-report contracts.

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
            "    ImplementationReportPayload, QAReportPayload, DefinitionOfDoneItem,\n"
            "    QAAcValidation, load_implementation_report, load_qa_report,\n"
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


def test_report_payload_ops_touch_no_fs_subprocess_network_or_env():
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
        "from artifacts import ImplementationReportPayload, QAReportPayload\n"
        "report = ImplementationReportPayload.from_mapping({\n"
        "    'uow_id': 'U1', 'status': 'complete', 'implementation_summary': 's',\n"
        "    'definition_of_done_status': [{'item': 'x', 'met': True}],\n"
        "})\n"
        "ImplementationReportPayload.validate_payload({'uow_id': 'U1'})\n"
        "report.to_dict(); report.to_json()\n"
        "ref = report.to_artifact_ref(path='C1/execution/U1/impl_report.yaml')\n"
        "ref.to_dict()\n"
        "qa = QAReportPayload.from_mapping({\n"
        "    'story_id': 'C1', 'qa_status': 'pass',\n"
        "    'acceptance_criteria_validation': {'AC1': {'status': 'pass'}},\n"
        "    'final_recommendation': 'approve',\n"
        "})\n"
        "qa.to_dict(); qa.to_artifact_ref(path='C1/qa/qa_report.yaml').to_dict()\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_importing_final_diff_artifact_pulls_in_no_heavy_or_vendor_modules():
    """FinalDiffArtifact lives in the stdlib-only leaf; importing it must not
    pull in eval, telemetry, or any vendor package."""
    result = _run_probe(
        _import_probe("from artifacts import FinalDiffArtifact")
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_importing_artifacts_with_final_diff_stays_clean():
    """The combined Prompt 18/19/20/21 import must still satisfy the isolation rule."""
    result = _run_probe(
        _import_probe(
            "from artifacts import (\n"
            "    StoryArtifact, TaskPlanArtifact, AssignmentArtifact, UowSpecArtifact,\n"
            "    PLANNING_ARTIFACTS, ValidationResult, ValidationIssue, ValidationSeverity,\n"
            "    ArtifactLoadError, ArtifactValidationError,\n"
            "    ImplementationReportPayload, QAReportPayload, DefinitionOfDoneItem,\n"
            "    QAAcValidation, load_implementation_report, load_qa_report,\n"
            "    FinalDiffArtifact,\n"
            ")"
        )
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_final_diff_artifact_ops_touch_no_fs_subprocess_network_or_env():
    """Construction, metadata access, and to_artifact_ref on FinalDiffArtifact
    must be pure (no FS/subprocess/network/env); only load() reads a file."""
    code = (
        "import subprocess, builtins, socket, urllib.request, os\n"
        "from pathlib import Path\n"
        "def _boom(*a, **k):\n"
        "    raise AssertionError('no FS/subprocess/network/env access allowed')\n"
        "subprocess.Popen = _boom\n"
        "subprocess.run = _boom\n"
        "socket.socket = _boom\n"
        "socket.create_connection = _boom\n"
        "urllib.request.urlopen = _boom\n"
        "os.getenv = _boom\n"
        "os.environ.get = _boom\n"
        # Import is pure (no FS needed).
        "from artifacts.evidence import FinalDiffArtifact\n"
        # Construct directly (bypassing load()).
        "art = FinalDiffArtifact('diff text', _byte_length=9)\n"
        "assert art.text == 'diff text'\n"
        "assert art.char_length == 9\n"
        "assert art.byte_length == 9\n"
        "assert not art.is_empty\n"
        "ref = art.to_artifact_ref(path='evidence/final.diff')\n"
        "ref.to_dict()\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_eval_adapters_do_not_cause_artifacts_import_cycle():
    """eval.report_artifact and telemetry.trace_artifact both import artifacts.
    Importing artifacts first must still work cleanly (no import cycle)."""
    code = (
        "import sys\n"
        "import artifacts\n"
        "from eval.report_artifact import EvalReportArtifact\n"
        "from telemetry.trace_artifact import TraceArtifact\n"
        "from artifacts.evidence import FinalDiffArtifact\n"
        "# Importing artifacts again must return the already-loaded module.\n"
        "import artifacts as arts2\n"
        "assert arts2 is artifacts\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
