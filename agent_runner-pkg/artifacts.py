from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .models import ResumeCandidate, WorkflowConfig, WorkflowError


def iso_now() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def file_timestamp() -> str:
    """Return a filesystem-safe UTC timestamp."""

    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def normalize_change_id(raw_value: str) -> str:
    """Normalize a change identifier from either WI-123 or bare digits."""

    value = raw_value.strip()
    match = re.fullmatch(r"WI-(\d+)", value, flags=re.IGNORECASE)
    if match:
        return f"WI-{match.group(1)}"
    if value.isdigit():
        return f"WI-{value}"
    return value


def change_root(config: WorkflowConfig) -> Path:
    """Return the change-specific artifact root."""

    return config.artifact_root / config.change_id


def intake_dir(config: WorkflowConfig) -> Path:
    return change_root(config) / "intake"


def planning_dir(config: WorkflowConfig) -> Path:
    return change_root(config) / "planning"


def execution_dir(config: WorkflowConfig, uow_id: str | None = None) -> Path:
    base = change_root(config) / "execution"
    return base if uow_id is None else base / uow_id


def qa_dir(config: WorkflowConfig) -> Path:
    return change_root(config) / "qa"


def summary_dir(config: WorkflowConfig) -> Path:
    return change_root(config) / "summary"


def status_dir(config: WorkflowConfig) -> Path:
    """Return the status directory for the active change."""

    return change_root(config) / "status"


def story_path(config: WorkflowConfig) -> Path:
    return intake_dir(config) / "story.yaml"


def config_path(config: WorkflowConfig) -> Path:
    return intake_dir(config) / "config.yaml"


def constraints_path(config: WorkflowConfig) -> Path:
    return intake_dir(config) / "constraints.md"


def tasks_path(config: WorkflowConfig) -> Path:
    return planning_dir(config) / "tasks.yaml"


def assignments_path(config: WorkflowConfig) -> Path:
    return planning_dir(config) / "assignments.json"


def task_plan_eval_path(config: WorkflowConfig, attempt: int) -> Path:
    return planning_dir(config) / f"eval_tasks_{attempt}.json"


def assignment_eval_path(config: WorkflowConfig, attempt: int) -> Path:
    return planning_dir(config) / f"eval_assignments_{attempt}.json"


def uow_spec_path(config: WorkflowConfig, uow_id: str) -> Path:
    return execution_dir(config, uow_id) / "uow_spec.yaml"


def impl_report_path(config: WorkflowConfig, uow_id: str) -> Path:
    return execution_dir(config, uow_id) / "impl_report.yaml"


def impl_eval_path(config: WorkflowConfig, uow_id: str, attempt: int) -> Path:
    return execution_dir(config, uow_id) / f"eval_impl_{attempt}.json"


def qa_report_path(config: WorkflowConfig) -> Path:
    return qa_dir(config) / "qa_report.yaml"


def qa_eval_path(config: WorkflowConfig, attempt: int) -> Path:
    return qa_dir(config) / f"eval_qa_{attempt}.json"


def lessons_report_path(config: WorkflowConfig) -> Path:
    return summary_dir(config) / "lessons_optimizer_report.yaml"


def intake_artifact_paths(artifact_root: Path, change_id: str) -> list[Path]:
    """Return the canonical intake artifact paths for a change."""

    base = artifact_root / change_id / "intake"
    return [base / "story.yaml", base / "config.yaml", base / "constraints.md"]


def intake_artifacts_exist(artifact_root: Path, change_id: str) -> bool:
    """Return whether all intake artifacts already exist for a change."""

    return all(path.is_file() for path in intake_artifact_paths(artifact_root, change_id))


def list_resume_candidates(artifact_root: Path) -> list[ResumeCandidate]:
    """List changes with complete intake artifacts that can be reused."""

    if not artifact_root.is_dir():
        return []

    candidates: list[ResumeCandidate] = []
    for change_dir in artifact_root.iterdir():
        if not change_dir.is_dir():
            continue
        if not intake_artifacts_exist(artifact_root, change_dir.name):
            continue
        updated_at = max(
            path.stat().st_mtime
            for path in intake_artifact_paths(artifact_root, change_dir.name)
        )
        candidates.append(
            ResumeCandidate(
                change_id=change_dir.name,
                base_path=change_dir,
                updated_at=updated_at,
            )
        )

    return sorted(candidates, key=lambda candidate: candidate.updated_at, reverse=True)


def write_json(path: Path, payload: dict) -> None:
    """Write a JSON file, creating parent directories when needed."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    """Write a UTF-8 text file, creating parent directories when needed."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_runner_log(config: WorkflowConfig, event_type: str, payload: dict) -> Path:
    """Write a workflow-runner log entry into the workflow-runner log folder."""

    log_path = (
        change_root(config)
        / "logs"
        / "workflow_runner"
        / f"{file_timestamp()}_{event_type}.json"
    )
    enriched = {
        "log_type": "workflow_runner",
        "event_type": event_type,
        "timestamp": iso_now(),
        "change_id": config.change_id,
        **payload,
    }
    write_json(log_path, enriched)
    return log_path


def read_json_file(path: Path) -> dict:
    """Load a JSON document with a clear error message on failure."""

    if not path.is_file():
        raise WorkflowError(f"Expected JSON artifact does not exist: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"Failed to parse JSON artifact {path}: {exc}") from exc


def load_execution_schedule(assignments_path_value: Path) -> list[dict]:
    """Load the execution schedule batches from `assignments.json`."""

    payload = read_json_file(assignments_path_value)
    schedule = payload.get("batches")
    if schedule is None:
        schedule = payload.get("execution_schedule")
    if not isinstance(schedule, list):
        raise WorkflowError(
            "Invalid assignments.json: expected batches list "
            f"(or legacy execution_schedule list) in {assignments_path_value}"
        )
    return schedule

