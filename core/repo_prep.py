from __future__ import annotations

import hashlib
import logging
import re
import shutil
import subprocess
from pathlib import Path

from core.runtime_paths import worktrees_root

logger = logging.getLogger(__name__)


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
        fallback="requested-changes",
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


def _current_branch(repo: str | Path) -> str:
    result = _run_git_command(repo, "branch", "--show-current")
    return result.stdout.strip()


def prepare_repo_branch(
    *,
    repo: str | Path,
    change_id: str,
    description_source: str | None,
) -> str:
    repo_path = Path(repo)
    feature_branch = build_feature_branch_name(change_id, description_source)

    _run_git_command(repo_path, "rev-parse", "--is-inside-work-tree")

    if _current_branch(repo_path) == feature_branch:
        return feature_branch

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


def worktree_path_for(change_id: str) -> Path:
    """Return the worktree directory path for *change_id* (sanitized + hashed)."""
    slug = re.sub(r"[^a-z0-9]+", "-", change_id.lower()).strip("-")[:48] or "run"
    digest = hashlib.sha256(change_id.encode("utf-8")).hexdigest()[:12]
    return worktrees_root(create=True) / f"{slug}-{digest}"


def _active_worktree_paths(repo: str | Path) -> set[Path]:
    """Return the set of worktree paths currently registered for *repo*."""
    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=str(repo),
        check=False,
        capture_output=True,
        text=True,
    )
    paths: set[Path] = set()
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            paths.add(Path(line[len("worktree "):].strip()))
    return paths


def prepare_repo_worktree(
    *,
    repo: str | Path,
    change_id: str,
    description_source: str | None,
) -> tuple[Path, str]:
    """Create an isolated git worktree for *repo* on the feature branch.

    Caller must hold git_admin_lock(repo) before calling.
    Returns (worktree_path, feature_branch).
    """
    repo_path = Path(repo)
    feature_branch = build_feature_branch_name(change_id, description_source)
    wt_path = worktree_path_for(change_id)
    wt_root = worktrees_root()

    if wt_path.exists():
        active = _active_worktree_paths(repo_path)
        if wt_path.resolve() not in {p.resolve() for p in active}:
            logger.warning("prepare_repo_worktree: reclaiming stale worktree dir %s", wt_path)
            remove_worktree(repo=repo_path, worktree_path=wt_path)
            if wt_path.exists():
                # Guard: only rmtree if it's safely under worktrees_root
                try:
                    wt_path.resolve().relative_to(wt_root.resolve())
                    shutil.rmtree(wt_path, ignore_errors=True)
                except ValueError:
                    raise RuntimeError(
                        f"Refusing to remove {wt_path}: not under worktrees_root {wt_root}"
                    )
        else:
            raise RuntimeError(
                f"Worktree {wt_path} is already registered as active for {repo_path}"
            )

    _run_git_command(repo_path, "fetch", "origin", "--prune")

    if _git_ref_exists(repo_path, "refs/remotes/origin/develop"):
        base_ref = "origin/develop"
    else:
        base_ref = "develop"

    local_feature_ref = f"refs/heads/{feature_branch}"
    remote_feature_ref = f"refs/remotes/origin/{feature_branch}"
    if _git_ref_exists(repo_path, local_feature_ref):
        _run_git_command(repo_path, "worktree", "add", str(wt_path), feature_branch)
    elif _git_ref_exists(repo_path, remote_feature_ref):
        _run_git_command(
            repo_path,
            "worktree", "add", "--track",
            "-b", feature_branch,
            str(wt_path),
            f"origin/{feature_branch}",
        )
    else:
        _run_git_command(repo_path, "worktree", "add", "-b", feature_branch, str(wt_path), base_ref)

    logger.info("prepare_repo_worktree: created worktree %s on branch %s", wt_path, feature_branch)
    return wt_path, feature_branch


def remove_worktree(*, repo: str | Path, worktree_path: str | Path) -> None:
    """Remove a git worktree.  Best-effort — never raises (used in finalize paths).

    Caller must hold git_admin_lock(repo) before calling.
    """
    wt = Path(worktree_path)
    repo_path = Path(repo)
    try:
        _run_git_command(repo_path, "worktree", "remove", "--force", str(wt))
    except Exception as exc:
        logger.warning("remove_worktree: git worktree remove failed for %s: %s", wt, exc)
        try:
            _run_git_command(repo_path, "worktree", "prune")
        except Exception:
            pass
        wt_root = worktrees_root()
        try:
            wt.resolve().relative_to(wt_root.resolve())
            shutil.rmtree(wt, ignore_errors=True)
        except ValueError:
            logger.warning("remove_worktree: skipping rmtree for %s (not under worktrees_root)", wt)
    # Always prune after removal so the registry is consistent
    try:
        _run_git_command(repo_path, "worktree", "prune")
    except Exception:
        pass


def ensure_graphify_index(repo: "str | Path") -> bool:
    """Launch a background graphify index for *repo* if one does not yet exist.

    Returns True if indexing was launched, False if skipped (already indexed,
    CLI not installed, or indexing is already in progress).  Never raises —
    graphify is a best-effort, non-blocking enrichment.
    """
    repo_path = Path(repo).expanduser().resolve()

    # Already indexed: fast-path marker written by graphify on first successful build.
    if (repo_path / "graphify-out" / "graph.json").exists():
        return False

    # Rebuild in progress (lock written by the git hook / prior launch).
    if (repo_path / "graphify-out" / ".rebuild.lock").exists():
        logger.info("ensure_graphify_index: rebuild already in progress for %s; skipping", repo_path)
        return False

    graphify_bin = shutil.which("graphify")
    if graphify_bin is None:
        logger.info("ensure_graphify_index: graphify CLI not found; skipping index for %s", repo_path)
        return False

    log_path = Path.home() / ".cache" / "graphify-index.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    print(
        f"[graphify] No index found for {repo_path}; "
        f"launching background index process (log: {log_path})",
        flush=True,
    )
    log_file = open(log_path, "a")  # noqa: WPS515 — file kept open by subprocess
    subprocess.Popen(
        [graphify_bin, str(repo_path)],
        cwd=str(repo_path),
        stdout=log_file,
        stderr=log_file,
        start_new_session=True,
    )
    return True
