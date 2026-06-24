"""Cross-process file locks for serializing git operations on a target repo."""
from __future__ import annotations

import errno
import fcntl
import hashlib
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from core.runtime_paths import locks_root

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_LOCK_SUFFIX_WORKING = "working"
_LOCK_SUFFIX_ADMIN = "admin"
_LOCK_SUFFIX_CHANGE = "change"


def _git_common_dir(repo: str | Path) -> Path:
    """Return the absolute path to the git common dir for *repo*.

    For a normal checkout this is the .git directory; for a worktree it is the
    shared .git dir of the parent repo.  Using the common dir ensures all
    worktrees of one repo map to the same lock files.
    """
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(result.stdout.strip())


def _lock_file_path(key: str, suffix: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")[:60] or "repo"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    return locks_root(create=True) / f"{slug}-{digest}-{suffix}.lock"


def _repo_lock_path(repo: str | Path, suffix: str) -> Path:
    try:
        common_dir = _git_common_dir(repo)
        key = str(common_dir)
    except Exception:
        # Fallback for non-git paths (e.g. unit tests with fake repo dirs).
        # Less precise about worktree deduplication but never raises.
        key = str(Path(repo).expanduser().resolve())
    return _lock_file_path(key, suffix)


class FileLock:
    """Advisory cross-process file lock backed by fcntl.flock."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fd: int | None = None

    @property
    def lock_path(self) -> Path:
        return self._path

    def acquire(self, *, blocking: bool = True) -> bool:
        """Acquire the lock.  Returns True on success, False if non-blocking and contended."""
        flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, flags)
        except OSError as exc:
            os.close(fd)
            if not blocking and exc.errno in (errno.EACCES, errno.EAGAIN):
                return False
            raise
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> "FileLock":
        self.acquire(blocking=True)
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


def working_tree_lock(repo: str | Path) -> FileLock:
    """Non-blocking lock held for the entire run.

    Acquired → in-place execution.  Contended → worktree fallback.
    """
    return FileLock(_repo_lock_path(repo, _LOCK_SUFFIX_WORKING))


def git_admin_lock(repo: str | Path) -> FileLock:
    """Blocking lock wrapping every git-metadata mutation (fetch, branch, worktree add/remove).

    Serializes setup/cleanup so concurrent runs never race on refs or the worktree registry.
    """
    return FileLock(_repo_lock_path(repo, _LOCK_SUFFIX_ADMIN))


def change_run_lock(change_id: str) -> FileLock:
    """Non-blocking lock keyed on change_id.

    Prevents two simultaneous runs of the same change_id, which would race on
    the same feature branch and worktree directory.
    """
    return FileLock(_lock_file_path(change_id, _LOCK_SUFFIX_CHANGE))
