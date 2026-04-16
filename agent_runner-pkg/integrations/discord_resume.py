from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from ..artifacts import (
    file_timestamp,
    iso_now,
    status_dir,
    write_json,
    write_runner_log,
)
from ..console import log
from ..integrations.observability import record_observability_event
from ..models import WorkflowConfig


def write_escalation_artifact(
    config: WorkflowConfig,
    producer_stage_key: str,
    evaluator_stage_key: str,
    attempt: int,
    eval_payload: dict[str, Any],
    uow_id: str | None = None,
) -> Path:
    """Write a machine-routable ``escalated.json`` from evaluator payload."""

    status = status_dir(config)
    status.mkdir(parents=True, exist_ok=True)
    escalated_path = status / "escalated.json"

    issues = eval_payload.get("issues") or []
    blocking_questions: list[str] = []
    for issue in issues:
        desc = issue.get("description", "")
        if issue.get("requires_escalation") or issue.get("severity") == "critical":
            blocking_questions.append(desc)
    if not blocking_questions:
        esc_rec = eval_payload.get("escalation_recommendation", {})
        reason = esc_rec.get("reason") if isinstance(esc_rec, dict) else None
        if reason:
            blocking_questions.append(reason)
        else:
            blocking_questions.append(
                "Evaluator escalated without specific questions."
            )

    esc_rec = eval_payload.get("escalation_recommendation", {})
    reason = (
        esc_rec.get("reason") if isinstance(esc_rec, dict) else None
    ) or "Evaluator recommended escalation"

    artifact = {
        "stage_key": producer_stage_key,
        "producer_stage_key": producer_stage_key,
        "evaluator_stage_key": evaluator_stage_key,
        "uow_id": uow_id,
        "attempt": attempt,
        "reason": reason,
        "blocking_questions": blocking_questions,
        "recommended_next_action": (
            esc_rec.get("recommended_next_action")
            if isinstance(esc_rec, dict)
            else "Provide clarification"
        )
        or "Provide clarification",
        "timestamp": iso_now(),
    }

    write_json(escalated_path, artifact)
    write_runner_log(
        config,
        "escalation_written",
        {
            "stage_key": producer_stage_key,
            "evaluator_stage_key": evaluator_stage_key,
            "uow_id": uow_id,
            "attempt": attempt,
            "blocking_questions": blocking_questions,
        },
    )
    log("WARN", f"Escalation artifact written: {escalated_path}")
    return escalated_path


def _start_discord_bridge(
    config: WorkflowConfig,
    escalated_path: Path,
    status_path: Path,
) -> subprocess.Popen[str] | None:
    """Start the Discord escalation bridge as a background subprocess."""

    bridge_script = config.workflow_assets_root / "scripts" / "discord_escalation_bridge.py"
    if not bridge_script.is_file():
        log(
            "WARN",
            f"Discord bridge script not found: {bridge_script}  (manual resume only)",
        )
        return None

    import os as _os

    if not _os.environ.get("DISCORD_BOT_TOKEN") and not _os.environ.get(
        "DISCORD_DRY_RUN"
    ):
        log(
            "WARN",
            "DISCORD_BOT_TOKEN not set — Discord escalation disabled "
            "(manual resume only)",
        )
        return None

    cmd = [
        sys.executable,
        str(bridge_script),
        "--escalated-path",
        str(escalated_path),
        "--status-dir",
        str(status_path),
        "--change-id",
        config.change_id,
    ]
    if _os.environ.get("DISCORD_DRY_RUN"):
        cmd.append("--dry-run")

    log("INFO", "Starting Discord escalation bridge...")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(config.repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        log("OK", f"Discord bridge started (pid={proc.pid})")
        return proc
    except OSError as exc:
        log("WARN", f"Failed to start Discord bridge: {exc}  (manual resume only)")
        return None


def wait_for_resume(
    config: WorkflowConfig,
    poll_seconds: int = 5,
) -> dict[str, Any] | None:
    """Check for ``escalated.json`` and block until ``resume.json`` appears."""

    status = status_dir(config)
    escalated_path = status / "escalated.json"
    paused_path = status / "paused.json"
    resume_path = status / "resume.json"
    archive_dir = status / "escalated_archive"

    if not escalated_path.exists():
        return None

    try:
        escalation = json.loads(escalated_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        escalation = {}

    if not paused_path.exists():
        status.mkdir(parents=True, exist_ok=True)
        write_json(
            paused_path,
            {
                "paused_at": iso_now(),
                "triggered_by": "escalated.json",
                "escalation_file": "status/escalated.json",
            },
        )

    questions = escalation.get("blocking_questions", [])
    questions_text = (
        "\n".join(f"  • {q}" for q in questions) if questions else "  (none specified)"
    )
    stage = escalation.get("stage_key", "unknown")
    reason = escalation.get("reason", "No reason specified")

    log("WARN", "=" * 64)
    log("WARN", "⏸  WORKFLOW PAUSED — Human Input Required")
    log("WARN", "=" * 64)
    log("WARN", f"Stage:   {stage}")
    log("WARN", f"Reason:  {reason}")
    log("WARN", f"Blocking Questions:\n{questions_text}")
    log("WARN", f"To resume, create:\n  {resume_path}")
    log(
        "WARN",
        '  With JSON: {"responder": "<name>", "answers": {...}, "constraints": [...]}',
    )
    log(
        "WARN",
        "  OR reply RESUME: in the Discord thread (if Discord is configured)",
    )
    log("WARN", "=" * 64)

    pause_payload = {
        "stage_key": stage,
        "reason": reason,
        "blocking_questions": questions,
        "escalated_path": str(escalated_path),
        "resume_path": str(resume_path),
    }
    write_runner_log(config, "workflow_paused", pause_payload)
    record_observability_event(config, "workflow_paused", **pause_payload)

    bridge_proc = _start_discord_bridge(config, escalated_path, status)

    try:
        while not resume_path.exists():
            if bridge_proc is not None and bridge_proc.poll() is None:
                try:
                    import select as _select

                    if (
                        bridge_proc.stdout
                        and _select.select([bridge_proc.stdout], [], [], 0)[0]
                    ):
                        line = bridge_proc.stdout.readline()
                        if line.strip():
                            log("INFO", f"[discord] {line.rstrip()}")
                except Exception:
                    pass
            elif bridge_proc is not None and bridge_proc.poll() is not None:
                if bridge_proc.stdout:
                    for line in bridge_proc.stdout:
                        if line.strip():
                            log("INFO", f"[discord] {line.rstrip()}")
                bridge_exit = bridge_proc.returncode
                if bridge_exit in {0, 2}:
                    break
                log(
                    "WARN",
                    f"Discord bridge exited with code {bridge_exit} — "
                    "waiting for manual resume.json",
                )
                bridge_proc = None
            time.sleep(poll_seconds)
    finally:
        if bridge_proc is not None and bridge_proc.poll() is None:
            log("INFO", "Terminating Discord bridge process")
            bridge_proc.terminate()
            try:
                bridge_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                bridge_proc.kill()

    try:
        resolution = json.loads(resume_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log("ERROR", f"Failed to read resume.json: {exc}")
        resolution = {"responder": "unknown", "answers": {}, "constraints": []}

    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_name = f"{file_timestamp()}_escalated.json"
    shutil.move(str(escalated_path), str(archive_dir / archive_name))

    if paused_path.exists():
        paused_path.unlink()
    resume_path.unlink()

    log("OK", f"[RESUMED] Escalation cleared. Archived → {archive_dir / archive_name}")
    resume_payload = {
        "resolution": resolution,
        "archive_path": str(archive_dir / archive_name),
    }
    write_runner_log(config, "workflow_resumed", resume_payload)
    record_observability_event(config, "workflow_resumed", **resume_payload)
    return resolution

