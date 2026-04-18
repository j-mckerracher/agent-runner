from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from ..agents import detect_available_backends
from ..artifacts import intake_artifacts_exist, normalize_change_id
from ..console import emit_event, log
from ..materialize import maybe_materialize
from ..integrations.ado import (
    create_pull_request,
    fetch_ado_context,
    parse_work_item_reference,
    resolve_ado_defaults,
)
from ..integrations.git_worktrees import (
    WorktreeInfo,
    cleanup_worktree,
    create_fresh_worktree,
)
from ..integrations.observability import build_observability_sink_from_env
from ..models import WORKFLOW_ASSETS_ROOT, WorkflowConfig, WorkflowError
from ..repo import resolve_repo_root
from ..workflow.engine import format_summary, run_workflow


def build_headless_config(
    change_id: str,
    repo_root: Path,
    backend_key: str | None = None,
    model: str | None = None,
) -> WorkflowConfig:
    """Build a WorkflowConfig without interactive prompts."""

    resolved_repo_root = repo_root.resolve()
    resolved_artifact_root = (resolved_repo_root / "agent-context").resolve()

    backends = detect_available_backends()
    if backend_key:
        backend = next((b for b in backends if b.key == backend_key), None)
        if not backend:
            available = [b.key for b in backends]
            raise WorkflowError(
                f"Backend '{backend_key}' is not available. Available: {available}"
            )
    else:
        backend = backends[0]

    log("INFO", f"Backend: {backend.label} ({backend.command})")

    base_config: dict = {
        "repo_root": resolved_repo_root,
        "workflow_assets_root": WORKFLOW_ASSETS_ROOT,
        "artifact_root": resolved_artifact_root,
        "cli_backend": backend.key,
        "cli_bin": backend.command,
        "model": model if model is not None else backend.default_model,
        "observability_sink": build_observability_sink_from_env(),
    }

    normalized_id = normalize_change_id(change_id)
    if intake_artifacts_exist(resolved_artifact_root, normalized_id):
        log("INFO", f"Reusing existing intake artifacts for {normalized_id}")
        return WorkflowConfig(
            change_id=normalized_id,
            context="",
            reuse_existing_intake=True,
            **base_config,
        )

    try:
        org_url, project = resolve_ado_defaults(resolved_repo_root)
        work_item_id = normalized_id.removeprefix("WI-")
        reference = parse_work_item_reference(
            work_item_id,
            default_organization=org_url,
            default_project=project,
        )
        context = fetch_ado_context(reference, resolved_repo_root)
        log("INFO", f"ADO context fetched for {normalized_id}")
    except WorkflowError as exc:
        log("WARN", f"Could not fetch ADO context: {exc} — using minimal context")
        context = f"Work item: {normalized_id}"

    return WorkflowConfig(change_id=normalized_id, context=context, **base_config)


def _write_error_json(
    output_json: str | None,
    error: str,
    *,
    worktree_info: WorktreeInfo | None,
) -> None:
    if not output_json:
        return
    payload: dict = {"status": "fail", "error": error, "stages": []}
    if worktree_info:
        payload["worktree"] = {
            "path": str(worktree_info.path),
            "branch": worktree_info.branch,
            "name": worktree_info.name,
            "base_ref": worktree_info.base_ref,
        }
    Path(output_json).parent.mkdir(parents=True, exist_ok=True)
    Path(output_json).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Non-interactive agent workflow launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--change-id",
        required=True,
        metavar="WI-XXXX",
        help="Work item ID (e.g. WI-4461550 or bare 4461550)",
    )
    parser.add_argument(
        "--repo",
        metavar="PATH",
        help="Absolute path to the repository root (default: git root of cwd)",
    )
    parser.add_argument(
        "--backend",
        choices=["copilot", "claude"],
        help="AI backend to use (auto-detected if not specified)",
    )
    parser.add_argument(
        "--output-json",
        metavar="PATH",
        help="Write a JSON summary to this path on completion",
    )
    parser.add_argument(
        "--cleanup-worktree",
        action="store_true",
        default=False,
        help="Remove the worktree and its branch after the workflow completes "
        "(even on failure)",
    )
    parser.add_argument(
        "--no-worktree",
        action="store_true",
        default=False,
        help="Skip worktree creation and run directly in the main repo "
        "(legacy behaviour)",
    )
    mat_group = parser.add_mutually_exclusive_group()
    mat_group.add_argument(
        "--materialize",
        dest="materialize",
        action="store_true",
        default=None,
        help="Force materialization of agents from agent-sources/ before run",
    )
    mat_group.add_argument(
        "--no-materialize",
        dest="materialize",
        action="store_false",
        help="Skip auto-materialization even if agent-sources/ exists",
    )
    parser.add_argument(
        "--agents-dir",
        metavar="PATH",
        help="Explicit agents directory (harness-managed; skips auto-materialize)",
    )
    parser.add_argument(
        "--model",
        metavar="MODEL",
        help="Override the AI model for all agent invocations (e.g. gpt-5-mini)",
    )
    args = parser.parse_args(argv)

    main_repo_root = Path(args.repo).resolve() if args.repo else resolve_repo_root()

    # Auto-materialize unless --no-materialize or harness-managed (--agents-dir)
    explicit_agents_dir = Path(args.agents_dir).resolve() if args.agents_dir else None
    if getattr(args, "materialize", None) is not False:
        maybe_materialize(
            main_repo_root,
            agents_dir=explicit_agents_dir,
        )

    worktree_info: WorktreeInfo | None = None
    if not args.no_worktree:
        try:
            worktree_info = create_fresh_worktree(main_repo_root, args.change_id)
        except WorkflowError as exc:
            log("ERROR", f"Worktree creation failed: {exc}")
            _write_error_json(args.output_json, str(exc), worktree_info=None)
            return 1

    effective_repo_root = worktree_info.path if worktree_info else main_repo_root

    # When an explicit agents dir is provided, copy agents into the worktree's
    # .claude/agents/ so the AI CLI (copilot/claude) can discover them.
    if explicit_agents_dir is not None and explicit_agents_dir.is_dir():
        dest = effective_repo_root / ".claude" / "agents"
        dest.mkdir(parents=True, exist_ok=True)
        count = sum(
            1
            for src in explicit_agents_dir.glob("*.agent.md")
            if shutil.copy2(src, dest / src.name) or True
        )
        log("INFO", f"Materialized {count} agent(s) from {explicit_agents_dir} → {dest}")

    try:
        try:
            config = build_headless_config(
                args.change_id,
                effective_repo_root,
                backend_key=args.backend,
                model=getattr(args, "model", None),
            )
        except WorkflowError as exc:
            log("ERROR", f"Config error: {exc}")
            emit_event("workflow_error", error=str(exc))
            _write_error_json(args.output_json, str(exc), worktree_info=worktree_info)
            return 1

        try:
            results = run_workflow(config)
        except WorkflowError as exc:
            log("ERROR", f"Workflow failed: {exc}")
            emit_event("workflow_error", error=str(exc))
            _write_error_json(args.output_json, str(exc), worktree_info=worktree_info)
            return 1

        summary = format_summary(results)
        if worktree_info:
            summary["worktree"] = {
                "path": str(worktree_info.path),
                "branch": worktree_info.branch,
                "name": worktree_info.name,
                "base_ref": worktree_info.base_ref,
            }

        if args.output_json:
            Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output_json).write_text(
                json.dumps(summary, indent=2),
                encoding="utf-8",
            )

        overall = summary["status"]
        log(
            "OK" if overall == "pass" else "ERROR",
            f"Workflow status: {overall.upper()}",
        )

        if overall == "pass" and worktree_info:
            try:
                org_url, project = resolve_ado_defaults(main_repo_root)
                create_pull_request(
                    main_repo_root,
                    source_branch=worktree_info.branch,
                    base_ref=worktree_info.base_ref,
                    change_id=args.change_id,
                    org_url=org_url,
                    project=project,
                    worktree_path=worktree_info.path,
                )
            except (WorkflowError, subprocess.SubprocessError, OSError) as exc:
                log("WARN", f"PR creation failed (non-fatal): {exc}")

        return 0 if overall == "pass" else 1
    finally:
        if worktree_info and args.cleanup_worktree:
            cleanup_worktree(main_repo_root, worktree_info)

