from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

from ..agents import detect_available_backends
from ..artifacts import (
    intake_artifacts_exist,
    list_resume_candidates,
    normalize_change_id,
)
from ..console import log, print_startup_robot
from ..integrations.ado import fetch_ado_context, parse_work_item_reference, resolve_ado_defaults
from ..integrations.observability import build_observability_sink_from_env
from ..models import BackendSpec, ResumeCandidate, WorkflowConfig, WORKFLOW_ASSETS_ROOT, WorkflowError
from ..repo import resolve_repo_root
from ..workflow.engine import format_summary, run_workflow


def prompt_text(
    prompt: str,
    *,
    input_fn: Callable[[str], str] = input,
    default: str | None = None,
    allow_empty: bool = False,
) -> str:
    """Prompt until a valid string is provided."""

    suffix = f" [{default}]" if default is not None else ""
    while True:
        value = input_fn(f"{prompt}{suffix}: ").strip()
        if not value and default is not None:
            return default
        if value or allow_empty:
            return value
        log("WARN", "A value is required.")


def prompt_yes_no(
    prompt: str,
    *,
    input_fn: Callable[[str], str] = input,
    default: bool = True,
) -> bool:
    """Prompt for a yes/no answer."""

    default_hint = "Y/n" if default else "y/N"
    while True:
        raw = input_fn(f"{prompt} [{default_hint}]: ").strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        log("WARN", "Please answer yes or no.")


def prompt_option(
    title: str,
    options: list[str],
    *,
    input_fn: Callable[[str], str] = input,
    default_index: int = 1,
) -> int:
    """Prompt the user to select a numbered option."""

    print()
    print(title)
    for index, option in enumerate(options, start=1):
        print(f"  {index}. {option}")
    while True:
        raw = prompt_text(
            "Select an option",
            input_fn=input_fn,
            default=str(default_index),
        )
        if raw.isdigit():
            selected = int(raw)
            if 1 <= selected <= len(options):
                return selected
        log("WARN", f"Choose a number from 1 to {len(options)}.")


def prompt_multiline(
    prompt: str,
    *,
    input_fn: Callable[[str], str] = input,
    end_marker: str = "END",
) -> str:
    """Collect multi-line input terminated by a sentinel line."""

    print()
    print(prompt)
    print(f"Finish with a line containing only {end_marker!r}.")
    lines: list[str] = []
    while True:
        line = input_fn("")
        if line.strip() == end_marker:
            break
        lines.append(line)
    content = "\n".join(lines).strip()
    if not content:
        raise WorkflowError("Workflow context cannot be empty.")
    return content


def select_backend(*, input_fn: Callable[[str], str] = input) -> BackendSpec:
    """Choose which AI backend should drive the workflow."""

    available = detect_available_backends()
    if len(available) == 1:
        backend = available[0]
        log("INFO", f"Using {backend.label} ({backend.command})")
        return backend

    selected = prompt_option(
        "Choose the AI backend for this workflow:",
        [f"{backend.label} ({backend.command})" for backend in available],
        input_fn=input_fn,
        default_index=1,
    )
    backend = available[selected - 1]
    log("INFO", f"Using {backend.label} ({backend.command})")
    return backend


def choose_resume_candidate(
    candidates: list[ResumeCandidate],
    *,
    input_fn: Callable[[str], str] = input,
) -> ResumeCandidate:
    """Choose which existing intake artifacts to reuse."""

    if not candidates:
        raise WorkflowError("No reusable intake artifacts were found.")
    if len(candidates) == 1:
        candidate = candidates[0]
        log("INFO", f"Reusing existing intake artifacts for {candidate.change_id}")
        return candidate

    selected = prompt_option(
        "Select a change to resume:",
        [
            f"{candidate.change_id} — updated "
            f"{datetime.fromtimestamp(candidate.updated_at).strftime('%Y-%m-%d %H:%M:%S')}"
            for candidate in candidates
        ],
        input_fn=input_fn,
        default_index=1,
    )
    candidate = candidates[selected - 1]
    log("INFO", f"Reusing existing intake artifacts for {candidate.change_id}")
    return candidate


def collect_interactive_config(
    *,
    input_fn: Callable[[str], str] = input,
    require_tty: bool = True,
    repo_root: Path | None = None,
    artifact_root: Path | None = None,
) -> WorkflowConfig:
    """Prompt the user for any required startup information."""

    if require_tty and (not sys.stdin.isatty() or not sys.stdout.isatty()):
        raise WorkflowError(
            "This runner is interactive only. Run it in a terminal without "
            "CLI arguments or redirected stdin."
        )

    print_startup_robot()
    resolved_repo_root = (repo_root or resolve_repo_root()).resolve()
    resolved_artifact_root = (artifact_root or resolved_repo_root / "agent-context").resolve()
    backend = select_backend(input_fn=input_fn)

    print()
    print(f"Repo root:     {resolved_repo_root}")
    print(f"Artifact root: {resolved_artifact_root}")

    resume_candidates = list_resume_candidates(resolved_artifact_root)
    start_options = ["Start from Azure DevOps work item (recommended)"]
    start_keys = ["ado"]
    if resume_candidates:
        start_options.append("Resume using existing intake artifacts")
        start_keys.append("resume")
    start_options.append("Paste workflow context manually")
    start_keys.append("manual")
    start_mode = start_keys[
        prompt_option(
            "How would you like to start?",
            start_options,
            input_fn=input_fn,
            default_index=1,
        )
        - 1
    ]

    base_config = {
        "repo_root": resolved_repo_root,
        "workflow_assets_root": WORKFLOW_ASSETS_ROOT,
        "artifact_root": resolved_artifact_root,
        "cli_backend": backend.key,
        "cli_bin": backend.command,
        "model": backend.default_model,
        "observability_sink": build_observability_sink_from_env(),
    }

    if start_mode == "resume":
        candidate = choose_resume_candidate(resume_candidates, input_fn=input_fn)
        return WorkflowConfig(
            change_id=candidate.change_id,
            context="",
            reuse_existing_intake=True,
            **base_config,
        )

    if start_mode == "manual":
        change_id = normalize_change_id(
            prompt_text("Change ID (for example WI-4461550)", input_fn=input_fn)
        )
        if intake_artifacts_exist(resolved_artifact_root, change_id) and prompt_yes_no(
            f"Existing intake artifacts were found for {change_id}. "
            "Reuse them and skip intake?",
            input_fn=input_fn,
            default=True,
        ):
            return WorkflowConfig(
                change_id=change_id,
                context="",
                reuse_existing_intake=True,
                **base_config,
            )
        context = prompt_multiline("Paste workflow context.", input_fn=input_fn)
        return WorkflowConfig(change_id=change_id, context=context, **base_config)

    default_organization, default_project = resolve_ado_defaults(resolved_repo_root)
    raw_reference = prompt_text("Azure DevOps work item ID or URL", input_fn=input_fn)
    reference = parse_work_item_reference(
        raw_reference,
        default_organization=default_organization,
        default_project=default_project,
    )
    change_id = normalize_change_id(reference.work_item_id)

    if intake_artifacts_exist(resolved_artifact_root, change_id) and prompt_yes_no(
        f"Existing intake artifacts were found for {change_id}. Reuse them and skip intake?",
        input_fn=input_fn,
        default=True,
    ):
        return WorkflowConfig(
            change_id=change_id,
            context="",
            reuse_existing_intake=True,
            **base_config,
        )

    try:
        context = fetch_ado_context(reference, resolved_repo_root)
    except WorkflowError as exc:
        log("WARN", str(exc))
        if not prompt_yes_no(
            "Paste workflow context manually instead?",
            input_fn=input_fn,
            default=True,
        ):
            raise
        context = prompt_multiline("Paste workflow context.", input_fn=input_fn)

    return WorkflowConfig(change_id=change_id, context=context, **base_config)


def main(argv: list[str] | None = None) -> int:
    """Interactive CLI entry point."""

    provided_args = list(sys.argv[1:] if argv is None else argv)
    if provided_args:
        print(
            "agent_workflow_runner.py is now interactive-only. "
            "Run it without arguments.",
            file=sys.stderr,
        )
        return 2

    try:
        config = collect_interactive_config()
    except KeyboardInterrupt:
        print(file=sys.stderr)
        log("WARN", "Startup cancelled by user.")
        return 130
    except EOFError:
        print(file=sys.stderr)
        log("ERROR", "Startup aborted before all required input was provided.")
        return 1
    except WorkflowError as exc:
        log("ERROR", f"Startup failed: {exc}")
        return 1

    try:
        results = run_workflow(config)
    except KeyboardInterrupt:
        print(file=sys.stderr)
        log("WARN", "Workflow cancelled by user.")
        return 130
    except WorkflowError as exc:
        log("ERROR", f"Workflow failed: {exc}")
        return 1

    summary = format_summary(results)
    print()
    for stage in summary["stages"]:
        status = "✓ PASS" if stage["passed"] else "✗ FAIL"
        log(
            "OK" if stage["passed"] else "ERROR",
            f"{status}  {stage['stage_name']}  (attempts={stage['attempts']})",
        )
    print()
    log(
        "OK" if summary["status"] == "pass" else "ERROR",
        f"Workflow status: {summary['status'].upper()}",
    )
    return 0

