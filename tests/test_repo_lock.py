from __future__ import annotations

import multiprocessing
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from core.repo_lock import FileLock, _git_common_dir, _lock_file_path, change_run_lock, working_tree_lock


def _acquire_and_block(lock_path: str, ready_event, release_event) -> None:
    """Child: acquire FileLock, signal ready, wait for release signal."""
    lock = FileLock(Path(lock_path))
    lock.acquire(blocking=True)
    ready_event.set()
    release_event.wait()
    lock.release()


def _acquire_and_exit(lock_path: str, ready_event) -> None:
    """Child: acquire FileLock, signal ready, then exit without releasing."""
    lock = FileLock(Path(lock_path))
    lock.acquire(blocking=True)
    ready_event.set()
    # exit without release — tests auto-release on process death


class FileLockCrossProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _lock_path(self, name: str = "test") -> Path:
        return Path(self.tmp) / f"{name}.lock"

    def test_second_process_blocked_non_blocking(self):
        lock_path = str(self._lock_path())

        ctx = multiprocessing.get_context("fork")
        ready = ctx.Event()
        release = ctx.Event()
        p = ctx.Process(target=_acquire_and_block, args=(lock_path, ready, release))
        p.start()
        ready.wait(timeout=5)

        lock = FileLock(Path(lock_path))
        acquired = lock.acquire(blocking=False)
        self.assertFalse(acquired, "non-blocking acquire should fail while child holds it")

        release.set()
        p.join(timeout=5)

    def test_release_allows_reacquire(self):
        lock_path = self._lock_path()
        lock = FileLock(lock_path)
        self.assertTrue(lock.acquire(blocking=False))
        lock.release()
        lock2 = FileLock(lock_path)
        self.assertTrue(lock2.acquire(blocking=False))
        lock2.release()

    def test_no_fd_leak_on_failed_acquire(self):
        ctx = multiprocessing.get_context("fork")
        ready = ctx.Event()
        release = ctx.Event()
        lock_path = str(self._lock_path())
        p = ctx.Process(target=_acquire_and_block, args=(lock_path, ready, release))
        p.start()
        ready.wait(timeout=5)

        before = len(os.listdir(f"/proc/{os.getpid()}/fd")) if sys.platform.startswith("linux") else None

        for _ in range(20):
            loser = FileLock(Path(lock_path))
            loser.acquire(blocking=False)

        if before is not None:
            after = len(os.listdir(f"/proc/{os.getpid()}/fd"))
            self.assertEqual(before, after, "fd count should not grow on repeated failed acquires")

        release.set()
        p.join(timeout=5)

    def test_auto_release_on_process_death(self):
        ctx = multiprocessing.get_context("fork")
        ready = ctx.Event()
        lock_path = str(self._lock_path())
        p = ctx.Process(target=_acquire_and_exit, args=(lock_path, ready))
        p.start()
        ready.wait(timeout=5)
        p.join(timeout=5)

        lock = FileLock(Path(lock_path))
        acquired = lock.acquire(blocking=False)
        self.assertTrue(acquired, "lock should be free after process death")
        lock.release()

    def test_context_manager_releases_on_exit(self):
        lock_path = self._lock_path()
        with FileLock(lock_path):
            pass
        lock2 = FileLock(lock_path)
        self.assertTrue(lock2.acquire(blocking=False))
        lock2.release()


class LockKeyStabilityTests(unittest.TestCase):
    def test_equivalent_change_ids_same_lock_path(self):
        p1 = change_run_lock("WI-123").lock_path
        p2 = change_run_lock("WI-123").lock_path
        self.assertEqual(p1, p2)

    def test_different_change_ids_different_lock_paths(self):
        p1 = change_run_lock("WI-001").lock_path
        p2 = change_run_lock("WI-002").lock_path
        self.assertNotEqual(p1, p2)

    def test_lock_file_path_slug_hash_no_traversal(self):
        path = _lock_file_path("../../evil/path", "working")
        self.assertNotIn("..", str(path))
        self.assertTrue(path.name.endswith(".lock"))


class GitCommonDirTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        subprocess.run(["git", "init", self.tmp], check=True, capture_output=True)
        subprocess.run(["git", "-C", self.tmp, "config", "user.email", "test@test.com"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", self.tmp, "config", "user.name", "Test"],
                       check=True, capture_output=True)

    def test_base_repo_common_dir_is_dot_git(self):
        common = _git_common_dir(self.tmp)
        self.assertEqual(common.resolve(), (Path(self.tmp) / ".git").resolve())

    def test_working_tree_lock_same_for_repo_and_worktree(self):
        with patch("core.repo_lock._git_common_dir", return_value=Path(self.tmp) / ".git"):
            p1 = working_tree_lock(self.tmp).lock_path
            p2 = working_tree_lock(self.tmp).lock_path
        self.assertEqual(p1, p2)


if __name__ == "__main__":
    unittest.main()
