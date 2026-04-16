#!/usr/bin/env python3
"""Unit tests for the headless runner modules."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

RUNNER_DIR = Path(__file__).resolve().parent
if str(RUNNER_DIR) not in sys.path:
    sys.path.insert(0, str(RUNNER_DIR))

from agent_runner import models
from agent_runner.cli import headless as headless_cli
from agent_runner.integrations import git_worktrees

_FAKE_REPO = Path("/fake/repo")


def _cp(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class TestMakeWorktreeName(unittest.TestCase):
    def test_format(self) -> None:
        name = git_worktrees._make_worktree_name("WI-4461550")
        parts = name.split("-")
        self.assertGreaterEqual(len(parts), 3)
        self.assertEqual(parts[0], "4461550")

    def test_uniqueness(self) -> None:
        name1 = git_worktrees._make_worktree_name("WI-100")
        name2 = git_worktrees._make_worktree_name("WI-100")
        self.assertNotEqual(name1, name2)

    def test_bare_number(self) -> None:
        name = git_worktrees._make_worktree_name("9999")
        self.assertTrue(name.startswith("9999-"))

    def test_non_ascii_stripped(self) -> None:
        name = git_worktrees._make_worktree_name("WI-123")
        self.assertRegex(name, r"^[a-z0-9\-_]+$")


class TestResolveBaseRef(unittest.TestCase):
    def _patch_run_git(self, side_effect):
        return mock.patch.object(git_worktrees, "_run_git", side_effect=side_effect)

    def test_origin_head_found_immediately(self) -> None:
        calls = []

        def fake_run_git(repo_root, args, **kwargs):
            calls.append(args)
            return _cp(returncode=0, stdout="abc123")

        with self._patch_run_git(fake_run_git):
            ref = git_worktrees._resolve_base_ref(_FAKE_REPO)

        self.assertEqual(ref, "origin/HEAD")
        self.assertIn(["rev-parse", "--verify", "origin/HEAD"], calls)

    def test_set_head_called_when_origin_head_missing(self) -> None:
        call_log = []

        def fake_run_git(repo_root, args, **kwargs):
            call_log.append(list(args))
            if args[0] == "rev-parse" and args[-1] == "origin/HEAD" and len(call_log) == 1:
                return _cp(returncode=128, stderr="not a valid object")
            if args == ["remote", "set-head", "origin", "-a"]:
                return _cp(returncode=0)
            return _cp(returncode=0, stdout="def456")

        with self._patch_run_git(fake_run_git):
            ref = git_worktrees._resolve_base_ref(_FAKE_REPO)

        self.assertEqual(ref, "origin/HEAD")
        self.assertIn(["remote", "set-head", "origin", "-a"], call_log)

    def test_fallback_to_origin_main(self) -> None:
        def fake_run_git(repo_root, args, **kwargs):
            if "origin/HEAD" in args:
                return _cp(returncode=128)
            if args == ["remote", "set-head", "origin", "-a"]:
                return _cp(returncode=0)
            if "origin/main" in args:
                return _cp(returncode=0, stdout="abc")
            return _cp(returncode=128)

        with self._patch_run_git(fake_run_git):
            ref = git_worktrees._resolve_base_ref(_FAKE_REPO)

        self.assertEqual(ref, "origin/main")

    def test_raises_when_all_refs_fail(self) -> None:
        def fake_run_git(repo_root, args, **kwargs):
            return _cp(returncode=128)

        with self._patch_run_git(fake_run_git):
            with self.assertRaises(models.WorkflowError):
                git_worktrees._resolve_base_ref(_FAKE_REPO)


class TestCreateFreshWorktree(unittest.TestCase):
    def test_correct_command_sequence(self) -> None:
        issued = []

        def fake_run_git(repo_root, args, **kwargs):
            issued.append(list(args))
            if args == ["rev-parse", "--is-inside-work-tree"]:
                return _cp(returncode=0, stdout="true")
            if args[0] == "rev-parse" and "origin/HEAD" in args:
                return _cp(returncode=0, stdout="abc123")
            if args[0] == "worktree":
                return _cp(returncode=0)
            return _cp(returncode=0)

        with (
            mock.patch.object(git_worktrees, "_run_git", side_effect=fake_run_git),
            mock.patch.object(Path, "mkdir"),
            mock.patch.object(Path, "exists", return_value=False),
        ):
            info = git_worktrees.create_fresh_worktree(_FAKE_REPO, "WI-999")

        self.assertEqual(issued[0], ["rev-parse", "--is-inside-work-tree"])
        worktree_add_calls = [c for c in issued if c[:2] == ["worktree", "add"]]
        self.assertEqual(len(worktree_add_calls), 1)
        wt_cmd = worktree_add_calls[0]
        self.assertIn("-b", wt_cmd)
        self.assertIn("origin/HEAD", wt_cmd)
        self.assertTrue(info.branch.startswith("worktree-"))
        self.assertEqual(info.base_ref, "origin/HEAD")
        self.assertIn(".claude", str(info.path))
        self.assertIn("worktrees", str(info.path))

    def test_raises_when_not_git_repo(self) -> None:
        def fake_run_git(repo_root, args, **kwargs):
            return _cp(returncode=128, stdout="", stderr="not a git repo")

        with mock.patch.object(git_worktrees, "_run_git", side_effect=fake_run_git):
            with self.assertRaises(models.WorkflowError):
                git_worktrees.create_fresh_worktree(_FAKE_REPO, "WI-1")


class TestCleanupWorktree(unittest.TestCase):
    def _make_info(self) -> git_worktrees.WorktreeInfo:
        return git_worktrees.WorktreeInfo(
            path=Path("/fake/repo/.claude/worktrees/test-name"),
            name="test-name",
            branch="worktree-test-name",
            base_ref="origin/HEAD",
        )

    def test_calls_worktree_remove_and_branch_delete(self) -> None:
        issued = []

        def fake_run_git(repo_root, args, **kwargs):
            issued.append(list(args))
            return _cp(returncode=0)

        info = self._make_info()
        with mock.patch.object(git_worktrees, "_run_git", side_effect=fake_run_git):
            git_worktrees.cleanup_worktree(_FAKE_REPO, info)

        remove_calls = [c for c in issued if c[:2] == ["worktree", "remove"]]
        branch_delete_calls = [c for c in issued if c[:2] == ["branch", "-D"]]
        self.assertEqual(len(remove_calls), 1)
        self.assertEqual(len(branch_delete_calls), 1)
        self.assertIn(str(info.path), remove_calls[0])
        self.assertIn(info.branch, branch_delete_calls[0])

    def test_errors_are_logged_not_raised(self) -> None:
        def fake_run_git(repo_root, args, **kwargs):
            raise models.WorkflowError("simulated failure")

        info = self._make_info()
        with mock.patch.object(git_worktrees, "_run_git", side_effect=fake_run_git):
            git_worktrees.cleanup_worktree(_FAKE_REPO, info)


class HeadlessConfigTests(unittest.TestCase):
    def test_build_headless_config_reuses_existing_intake(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            intake_root = repo_root / "agent-context" / "WI-123" / "intake"
            intake_root.mkdir(parents=True)
            for name in ("story.yaml", "config.yaml", "constraints.md"):
                (intake_root / name).write_text("placeholder\n", encoding="utf-8")

            with mock.patch.object(
                headless_cli,
                "detect_available_backends",
                return_value=[
                    models.BackendSpec(
                        key="copilot",
                        label="GitHub Copilot",
                        command="copilot",
                    )
                ],
            ):
                config = headless_cli.build_headless_config("WI-123", repo_root)

        self.assertTrue(config.reuse_existing_intake)
        self.assertEqual(config.context, "")

    def test_build_headless_config_falls_back_to_minimal_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            with (
                mock.patch.object(
                    headless_cli,
                    "detect_available_backends",
                    return_value=[
                        models.BackendSpec(
                            key="copilot",
                            label="GitHub Copilot",
                            command="copilot",
                        )
                    ],
                ),
                mock.patch.object(
                    headless_cli,
                    "resolve_ado_defaults",
                    return_value=("https://dev.azure.com/mclm", "Mayo"),
                ),
                mock.patch.object(
                    headless_cli,
                    "fetch_ado_context",
                    side_effect=models.WorkflowError("ado unavailable"),
                ),
            ):
                config = headless_cli.build_headless_config("WI-123", repo_root)

        self.assertFalse(config.reuse_existing_intake)
        self.assertEqual(config.context, "Work item: WI-123")


if __name__ == "__main__":
    unittest.main()
