from __future__ import annotations

import subprocess
from pathlib import Path

from .models import WORKFLOW_ASSETS_ROOT, WorkflowError


def resolve_repo_root() -> Path:
    """Resolve the repository root for the interactive launcher."""

    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(Path.cwd()),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except subprocess.TimeoutExpired:
        completed = None

    if completed and completed.returncode == 0 and completed.stdout.strip():
        return Path(completed.stdout.strip()).resolve()
    return WORKFLOW_ASSETS_ROOT.parent.resolve()


def get_repo_name(repo_root: Path) -> str:
    """Extract the repository name from the origin remote URL."""

    result = subprocess.run(
        ["git", "-C", str(repo_root), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        raise WorkflowError(
            "Cannot determine repo name: 'git remote get-url origin' failed"
        )
    url = result.stdout.strip()
    name = url.rstrip("/").rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name

