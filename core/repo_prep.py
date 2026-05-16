from __future__ import annotations

import re
import subprocess
from pathlib import Path


def _slugify_branch_segment(
    value: str | None,
    *,
    fallback: str,
    max_words: int | None = None,
    limit: int = 48,
) -> str:
    parts = re.findall(r"[a-z0-9]+", (value or "").lower())
    if max_words is not None:
        parts = parts[:max_words]
    slug = "-".join(parts).strip("-")
    if len(slug) > limit:
        slug = slug[:limit].strip("-")
    return slug or fallback


def build_feature_branch_name(change_id: str, description_source: str | None) -> str:
    change_segment = _slugify_branch_segment(change_id, fallback="change", limit=32)
    description_segment = _slugify_branch_segment(
        description_source,
        fallback="requested-update",
        max_words=5,
        limit=48,
    )
    return f"feature/{change_segment}-{description_segment}"


def _run_git_command(repo: str | Path, *args: str) -> subprocess.CompletedProcess[str]:
    command = ["git", *args]
    try:
        return subprocess.run(
            command,
            cwd=str(repo),
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(
            f"{' '.join(command)} failed in {repo}: {details or 'git command failed'}"
        ) from exc


def _git_ref_exists(repo: str | Path, ref: str) -> bool:
    result = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", ref],
        cwd=str(repo),
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def prepare_repo_branch(
    *,
    repo: str | Path,
    change_id: str,
    description_source: str | None,
) -> str:
    repo_path = Path(repo)
    feature_branch = build_feature_branch_name(change_id, description_source)

    _run_git_command(repo_path, "rev-parse", "--is-inside-work-tree")

    if _git_ref_exists(repo_path, "refs/heads/develop"):
        _run_git_command(repo_path, "checkout", "develop")
    elif _git_ref_exists(repo_path, "refs/remotes/origin/develop"):
        _run_git_command(repo_path, "checkout", "-b", "develop", "--track", "origin/develop")
    else:
        raise RuntimeError(
            f"Target repo does not have a local or origin/develop branch: {repo_path}"
        )

    _run_git_command(repo_path, "pull", "--ff-only")

    local_feature_ref = f"refs/heads/{feature_branch}"
    remote_feature_ref = f"refs/remotes/origin/{feature_branch}"
    if _git_ref_exists(repo_path, local_feature_ref):
        _run_git_command(repo_path, "checkout", feature_branch)
    elif _git_ref_exists(repo_path, remote_feature_ref):
        _run_git_command(
            repo_path,
            "checkout",
            "-b",
            feature_branch,
            "--track",
            f"origin/{feature_branch}",
        )
    else:
        _run_git_command(repo_path, "checkout", "-b", feature_branch)

    return feature_branch
