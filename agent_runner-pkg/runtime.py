from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from .agents import build_agent_command, strip_agent_bullet_prefix
from .artifacts import read_json_file, write_runner_log
from .commands import run_command
from .console import log, print_agent_output
from .dry_run import materialize_dry_run_artifacts
from .integrations.observability import record_observability_event
from .models import AgentSpec, CommandResult, WorkflowConfig, WorkflowError


def call_helper_script(config: WorkflowConfig, script_name: str, *args: str) -> CommandResult:
    """Run one of the repository's helper scripts."""

    script_path = config.workflow_assets_root / "scripts" / script_name
    if not script_path.is_file():
        raise WorkflowError(f"Helper script not found: {script_path}")

    log("INFO", f"Running helper script: {script_name}  args={args}")
    command = [sys.executable, str(script_path), *args]
    result = run_command(
        command,
        cwd=config.repo_root,
        timeout_seconds=config.timeout_seconds,
    )
    if result.exit_code != 0:
        raise WorkflowError(
            f"Helper script failed: {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    log("OK", f"Helper script succeeded: {script_name}")
    return result


def ensure_artifact_dirs(config: WorkflowConfig) -> None:
    """Create the artifact directory tree using the repository helper script."""

    call_helper_script(
        config,
        "init-artifact-dirs.py",
        str(config.artifact_root),
        config.change_id,
    )


def validate_artifact_schema(
    config: WorkflowConfig,
    artifact_type: str,
    artifact_path: Path,
) -> tuple[bool, str]:
    """Validate an artifact against its schema before evaluation."""

    validation_script = (
        config.workflow_assets_root / "scripts" / "validate-artifact-schema.py"
    )
    if not validation_script.is_file():
        log("WARN", f"Schema validation script not found: {validation_script}")
        return True, ""

    result = run_command(
        [
            sys.executable,
            str(validation_script),
            "--type",
            artifact_type,
            str(artifact_path),
        ],
        cwd=config.repo_root,
        timeout_seconds=30,
    )

    if result.exit_code == 0:
        return True, ""

    try:
        output = json.loads(result.stdout) if result.stdout else {}
        issues = output.get("issues", [])
        error_msg = "Schema validation failed:\n"
        for issue in issues:
            error_msg += f"  - {issue.get('path', '?')}: {issue.get('issue', '?')}\n"
        return False, error_msg.strip()
    except Exception:
        return False, f"Schema validation failed (exit code {result.exit_code})"


def evaluation_signature(payload: dict[str, Any]) -> str:
    """Build a stable signature for plateau detection across evaluator attempts."""

    normalized = dict(payload)
    normalized.pop("evaluation_id", None)
    normalized.pop("attempt_number", None)
    normalized.pop("score", None)
    normalized.pop("notes", None)
    serialized = json.dumps(normalized, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def read_evaluation_result(path: Path) -> tuple[bool, dict[str, Any]]:
    """Return the evaluation payload and whether it passed."""

    payload = read_json_file(path)
    return payload.get("overall_result") == "pass", payload


def extract_feedback_summary(payload: dict[str, Any]) -> str:
    """Convert evaluator output into prompt-friendly feedback text."""

    fixes = payload.get("actionable_fixes_summary") or []
    issues = payload.get("issues") or []
    lines: list[str] = []
    for item in fixes:
        lines.append(f"- {item}")
    if not lines:
        for issue in issues:
            description = issue.get("description", "unspecified issue")
            action = issue.get("actionable_fix", "no actionable fix provided")
            location = issue.get("location", "unknown location")
            lines.append(f"- {location}: {description} | Fix: {action}")
    if not lines:
        lines.append("- Evaluator reported failure without actionable details.")
    return "\n".join(lines)


def extract_command_failure_summary(result: CommandResult) -> str:
    """Convert a failed agent command into retry guidance."""

    lines = [f"- Previous attempt exited with code {result.exit_code}."]
    stderr_excerpt = result.stderr.strip()
    stdout_excerpt = strip_agent_bullet_prefix(result.stdout).strip()
    if stderr_excerpt:
        lines.append(f"- stderr: {stderr_excerpt}")
    if stdout_excerpt:
        lines.append(f"- stdout: {stdout_excerpt}")
    return "\n".join(lines)


def invoke_agent(
    config: WorkflowConfig,
    agent: AgentSpec,
    prompt: str,
    stage_key: str,
    attempt: int,
    uow_id: str | None = None,
    raise_on_error: bool = True,
    early_exit_paths: list[Path] | None = None,
) -> CommandResult:
    """Invoke an agent or synthesize its outputs in dry-run mode."""

    uow_label = f"  uow={uow_id}" if uow_id else ""
    log(
        "AGENT",
        f"Dispatching agent '{agent.key}'  stage={stage_key}  attempt={attempt}{uow_label}",
    )

    dispatch_payload = {
        "stage_key": stage_key,
        "attempt": attempt,
        "uow_id": uow_id,
        "agent": {"key": agent.key, "name": agent.name, "path": str(agent.path)},
    }
    write_runner_log(config, "agent_dispatch", dispatch_payload)
    record_observability_event(config, "agent_dispatch", **dispatch_payload)

    if config.dry_run:
        log("INFO", f"[dry-run] Materializing synthetic artifacts for stage={stage_key}{uow_label}")
        dry_run_artifacts = materialize_dry_run_artifacts(
            config,
            stage_key,
            attempt,
            uow_id=uow_id,
        )
        log("OK", f"[dry-run] Created {len(dry_run_artifacts)} artifact(s)")
        for artifact in dry_run_artifacts:
            log("INFO", f"  → {artifact}")
        write_runner_log(
            config,
            "dry_run_stage_materialized",
            {
                "stage_key": stage_key,
                "attempt": attempt,
                "uow_id": uow_id,
                "artifacts": [str(path) for path in dry_run_artifacts],
            },
        )
        result = CommandResult(
            command=[config.cli_bin, "--dry-run", agent.key],
            exit_code=0,
            stdout=f"dry-run completed for {agent.key}",
            stderr="",
        )
        record_observability_event(
            config,
            "agent_result",
            stage_key=stage_key,
            attempt=attempt,
            uow_id=uow_id,
            agent=agent.key,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            command=result.command,
        )
        return result

    command = build_agent_command(config, agent, prompt)
    log("INFO", f"Prompt preview: {prompt[:200].strip().replace(chr(10), ' ')!r}…")
    result = run_command(
        command,
        cwd=config.repo_root,
        timeout_seconds=config.timeout_seconds,
        early_exit_paths=early_exit_paths,
    )

    print_agent_output(result, agent.key)

    result_payload = {
        "stage_key": stage_key,
        "attempt": attempt,
        "uow_id": uow_id,
        "agent": agent.key,
        "exit_code": result.exit_code,
        "stdout": strip_agent_bullet_prefix(result.stdout),
        "stderr": result.stderr,
        "command": command,
    }
    write_runner_log(config, "agent_result", result_payload)
    record_observability_event(config, "agent_result", **result_payload)

    if result.exit_code != 0:
        log(
            "ERROR",
            f"Agent '{agent.key}' failed for stage '{stage_key}' attempt {attempt} "
            f"(exit={result.exit_code})",
        )
        if raise_on_error:
            raise WorkflowError(
                f"Agent '{agent.key}' failed for stage '{stage_key}' attempt {attempt}.\n"
                f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
            )
        return result

    log(
        "OK",
        f"Agent '{agent.key}' completed successfully for stage={stage_key} attempt={attempt}{uow_label}",
    )
    return result
