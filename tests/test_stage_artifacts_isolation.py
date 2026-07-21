"""Import-isolation checks for the Prompt-22 stage-artifact modules.

Mirrors `tests/test_workflow_stage_isolation.py`'s subprocess `sys.modules`
probe. `workflow.stage_artifacts` must stay a pure leaf (no telemetry, run,
server, opik, or vendor SDK); `workflow.artifact_lifecycle` may import
telemetry but never run/server/opik/vendor.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

import workflow.stage_artifacts as _sa


def _run_probe(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
    )


def _imported_modules(module) -> set[str]:
    """Top-level module names imported by a module's source (AST, no exec)."""
    tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_stage_artifacts_source_does_not_import_telemetry_run_server_opik() -> None:
    # Design intent: the declaration/validation module is a pure leaf. The
    # package __init__ pulls telemetry (via workflow.stages), so a runtime
    # sys.modules probe can't isolate it — assert the module's own imports.
    imported = _imported_modules(_sa)
    forbidden = {"telemetry", "run", "server", "opik", "google", "openai", "anthropic"}
    assert not (imported & forbidden), imported & forbidden
    assert imported <= {
        "__future__",
        "collections",
        "dataclasses",
        "enum",
        "typing",
        "artifacts",
        "core",
    }, imported


def test_artifact_lifecycle_imports_telemetry_but_not_run_server_opik() -> None:
    code = (
        "import sys\n"
        "import workflow.artifact_lifecycle\n"
        "assert 'telemetry' in sys.modules\n"
        "bad = [m for m in sys.modules if m == 'run' or m == 'server' "
        "or m.startswith('server.') or 'opik' in m.lower()]\n"
        "assert not bad, bad\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_no_vendor_sdk_pulled_by_stage_artifact_modules() -> None:
    code = (
        "import sys\n"
        "import workflow.stage_artifacts, workflow.artifact_lifecycle\n"
        "vendor = ('google', 'googleapiclient', 'opik', 'openai', 'anthropic', 'fastmcp')\n"
        "bad = [m for m in sys.modules if any(m == v or m.startswith(v + '.') for v in vendor)]\n"
        "assert not bad, bad\n"
        "print('OK')\n"
    )
    result = _run_probe(code)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
