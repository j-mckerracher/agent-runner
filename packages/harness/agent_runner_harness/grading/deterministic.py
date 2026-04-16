"""Deterministic acceptance criterion grader.

Evaluates file_exists, script, event_assertion, and schema_valid criteria
against an artifact directory and event log.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from agent_runner_shared.models import AcceptanceCriterion


def _grade_file_exists(criterion: AcceptanceCriterion, artifact_dir: Path) -> dict[str, Any]:
    """Check that a file exists in the artifact directory."""
    assert criterion.path is not None
    target = artifact_dir / criterion.path
    passed = target.exists()
    return {
        "id": criterion.id,
        "kind": criterion.kind,
        "passed": passed,
        "detail": f"{'Found' if passed else 'Missing'}: {target}",
    }


def _grade_script(
    criterion: AcceptanceCriterion,
    artifact_dir: Path,
    task_dir: Path | None,
) -> dict[str, Any]:
    """Run a script criterion and check its exit code."""
    assert criterion.script is not None
    # Script path is relative to the task directory (where criteria/ lives),
    # or else relative to artifact_dir.
    if task_dir is not None:
        script_path = task_dir / criterion.script
    else:
        script_path = artifact_dir / criterion.script

    if not script_path.exists():
        return {
            "id": criterion.id,
            "kind": criterion.kind,
            "passed": False,
            "detail": f"Script not found: {script_path}",
        }

    result = subprocess.run(
        ["python3", str(script_path)],
        capture_output=True,
        text=True,
        env={"ARTIFACT_DIR": str(artifact_dir)},
    )
    passed = result.returncode == 0
    return {
        "id": criterion.id,
        "kind": criterion.kind,
        "passed": passed,
        "detail": result.stdout.strip() or result.stderr.strip() or f"exit={result.returncode}",
    }


def _grade_event_assertion(
    criterion: AcceptanceCriterion,
    event_log_path: Path,
) -> dict[str, Any]:
    """Assert that an event matching certain fields exists in the event log."""
    assert criterion.event is not None
    if not event_log_path.exists():
        return {
            "id": criterion.id,
            "kind": criterion.kind,
            "passed": False,
            "detail": "Event log not found",
        }

    expected = criterion.event
    for line in event_log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        # Check that all expected fields match (subset match)
        if all(record.get(k) == v for k, v in expected.items()):
            return {
                "id": criterion.id,
                "kind": criterion.kind,
                "passed": True,
                "detail": f"Matched event: {json.dumps(record)}",
            }

    return {
        "id": criterion.id,
        "kind": criterion.kind,
        "passed": False,
        "detail": f"No event matched: {json.dumps(expected)}",
    }


def _grade_schema_valid(
    criterion: AcceptanceCriterion,
    artifact_dir: Path,
) -> dict[str, Any]:
    """Validate a file against a JSON/YAML schema."""
    assert criterion.path is not None
    target = artifact_dir / criterion.path
    if not target.exists():
        return {
            "id": criterion.id,
            "kind": criterion.kind,
            "passed": False,
            "detail": f"File not found: {target}",
        }

    # Load the file
    text = target.read_text(encoding="utf-8")
    if target.suffix in (".yaml", ".yml"):
        instance = yaml.safe_load(text)
    else:
        instance = json.loads(text)

    # schema_ref can be a path to a schema file
    schema_ref = criterion.schema_ref
    if schema_ref is None:
        return {
            "id": criterion.id,
            "kind": criterion.kind,
            "passed": False,
            "detail": "No schema_ref provided",
        }

    schema_path = artifact_dir / schema_ref
    if not schema_path.exists():
        # Try relative to repo schemas
        return {
            "id": criterion.id,
            "kind": criterion.kind,
            "passed": False,
            "detail": f"Schema file not found: {schema_path}",
        }

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    try:
        jsonschema.validate(instance=instance, schema=schema)
        passed = True
        detail = "Schema validation passed"
    except jsonschema.ValidationError as exc:
        passed = False
        detail = str(exc.message)

    return {
        "id": criterion.id,
        "kind": criterion.kind,
        "passed": passed,
        "detail": detail,
    }


def grade_deterministic(
    criteria: list[AcceptanceCriterion],
    artifact_dir: Path,
    event_log_path: Path,
    task_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Evaluate a list of deterministic acceptance criteria.

    Args:
        criteria: List of AcceptanceCriterion with kind in
                  {file_exists, script, event_assertion, schema_valid}.
        artifact_dir: Directory containing run artifacts.
        event_log_path: Path to the JSONL event log.
        task_dir: Directory of the task definition (for script paths).

    Returns:
        List of result dicts with keys: id, kind, passed, detail.
    """
    results: list[dict[str, Any]] = []
    for criterion in criteria:
        if criterion.kind == "file_exists":
            results.append(_grade_file_exists(criterion, artifact_dir))
        elif criterion.kind == "script":
            results.append(_grade_script(criterion, artifact_dir, task_dir))
        elif criterion.kind == "event_assertion":
            results.append(_grade_event_assertion(criterion, event_log_path))
        elif criterion.kind == "schema_valid":
            results.append(_grade_schema_valid(criterion, artifact_dir))
        else:
            results.append({
                "id": criterion.id,
                "kind": criterion.kind,
                "passed": False,
                "detail": f"Unknown deterministic criterion kind: {criterion.kind!r}",
            })
    return results
