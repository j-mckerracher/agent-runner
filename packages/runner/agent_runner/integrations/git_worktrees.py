from __future__ import annotations

import re
import secrets
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..artifacts import normalize_change_id
from ..console import log
from ..models import WorkflowError


@dataclass(frozen=True)
class WorktreeInfo:
    """Metadata for a freshly-created Git worktree."""

    path: Path
    name: str
    branch: str
    base_ref: str


def _run_git(
    repo_root: Path,
    args: list[str],
    *,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess:
    """Run a git command under *repo_root* and raise on failure when requested."""

    cmd = ["git", "-C", str(repo_root)] + args
    log("INFO", f"git: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        timeout=30,
        check=False,
    )
    if check and result.returncode != 0:
        stderr = result.stderr.strip()
        raise WorkflowError(
            f"git command failed (exit {result.returncode}): {' '.join(args)}"
            + (f"\n  {stderr}" if stderr else "")
        )
    return result


def _resolve_base_ref(repo_root: Path) -> str:
    """Return a valid base ref for creating the worktree."""

    result = _run_git(repo_root, ["rev-parse", "--verify", "origin/HEAD"], check=False)
    if result.returncode == 0:
        log("INFO", "Base ref resolved: origin/HEAD")
        return "origin/HEAD"

    log("INFO", "origin/HEAD not found; running 'git remote set-head origin -a'")
    _run_git(repo_root, ["remote", "set-head", "origin", "-a"], check=False)

    result = _run_git(repo_root, ["rev-parse", "--verify", "origin/HEAD"], check=False)
    if result.returncode == 0:
        log("INFO", "Base ref resolved after set-head: origin/HEAD")
        return "origin/HEAD"

    for fallback in ("origin/main", "origin/master"):
        result = _run_git(repo_root, ["rev-parse", "--verify", fallback], check=False)
        if result.returncode == 0:
            log("INFO", f"Base ref resolved via fallback: {fallback}")
            return fallback

    raise WorkflowError(
        "Cannot determine a base ref for the worktree. Run 'git remote set-head "
        "origin -a' or ensure origin/main or origin/master exists."
    )


def _make_worktree_name(change_id: str) -> str:
    """Generate a unique worktree name from a change-id."""

    normalized = normalize_change_id(change_id)
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower().removeprefix("wi-")).strip("-")
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    rand = secrets.token_hex(3)
    return f"{slug}-{ts}-{rand}"


def create_fresh_worktree(repo_root: Path, change_id: str) -> WorktreeInfo:
    """Create a new Git worktree under ``<repo_root>/.claude/worktrees/<name>``."""

    result = _run_git(repo_root, ["rev-parse", "--is-inside-work-tree"], check=False)
    if result.returncode != 0 or result.stdout.strip() != "true":
        raise WorkflowError(f"'{repo_root}' does not appear to be inside a Git repository.")

    log("INFO", f"Base repo root: {repo_root}")
    base_ref = _resolve_base_ref(repo_root)

    worktrees_root = repo_root / ".claude" / "worktrees"
    worktrees_root.mkdir(parents=True, exist_ok=True)

    for _ in range(2):
        name = _make_worktree_name(change_id)
        worktree_path = worktrees_root / name
        if not worktree_path.exists():
            break
    else:
        raise WorkflowError("Could not generate a unique worktree name after 2 attempts.")

    branch = f"worktree-{name}"
    _run_git(repo_root, ["worktree", "add", "-b", branch, str(worktree_path), base_ref])

    log("INFO", f"Worktree created: {worktree_path}")
    log("INFO", f"Worktree branch:  {branch}")

    return WorktreeInfo(
        path=worktree_path,
        name=name,
        branch=branch,
        base_ref=base_ref,
    )


def cleanup_worktree(repo_root: Path, info: WorktreeInfo) -> None:
    """Remove the worktree and its branch (best-effort; errors are logged only)."""

    try:
        _run_git(repo_root, ["worktree", "remove", "--force", str(info.path)])
        log("INFO", f"Worktree removed: {info.path}")
    except (WorkflowError, subprocess.SubprocessError, OSError) as exc:
        log("WARN", f"Failed to remove worktree '{info.path}': {exc}")

    try:
        _run_git(repo_root, ["branch", "-D", info.branch])
        log("INFO", f"Worktree branch deleted: {info.branch}")
    except (WorkflowError, subprocess.SubprocessError, OSError) as exc:
        log("WARN", f"Failed to delete branch '{info.branch}': {exc}")
