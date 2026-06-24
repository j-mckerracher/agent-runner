"""Integration tests for the git-worktree isolation path in run.py main()."""
from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call, patch

import core
import run


def _workflow_input(repo: str = "/tmp/repo", change_id: str = "WI-001") -> SimpleNamespace:
    return SimpleNamespace(
        repo=repo,
        change_id=change_id,
        intake_mode="synthetic",
        intake_source="/tmp/story.json",
        branch_description_source="test feature",
    )


def _install_fake_workflow_modules(stack: ExitStack):
    steps_module = types.ModuleType("core.steps")
    steps_module.step_intake = Mock()
    steps_module.step_pr_review = Mock(return_value="/tmp/pr_review.md")
    steps_module.step_lessons_optimizer = Mock()
    steps_module.step_task_gen_producer = Mock()
    steps_module.step_task_gen_evaluator = Mock()
    steps_module.step_task_assigner = Mock()
    steps_module.step_assignment_evaluator = Mock()
    steps_module.step_qa_engineer = Mock()
    steps_module.step_qa_evaluator = Mock()

    loops_module = types.ModuleType("core.evaluator_optimizer_loops")
    loops_module.run_eval_optimizer_loop = Mock()
    loops_module.run_uow_eval_loop = Mock()

    stack.enter_context(
        patch.dict(sys.modules, {
            "core.steps": steps_module,
            "core.evaluator_optimizer_loops": loops_module,
        })
    )
    stack.enter_context(patch.object(core, "steps", steps_module, create=True))
    stack.enter_context(patch.object(core, "evaluator_optimizer_loops", loops_module, create=True))
    return steps_module, loops_module


def _base_patches(stack: ExitStack, tmpdir: str, workflow_input: SimpleNamespace):
    """Install patches common to all worktree integration tests."""
    stack.enter_context(patch.object(run, "AGENT_CONTEXT_ROOT", Path(tmpdir) / "agent-context"))
    stack.enter_context(patch.object(run, "resolve_workflow_input", return_value=workflow_input))
    stack.enter_context(patch.object(run, "use_runner_root"))
    stack.enter_context(patch.object(run, "clean_workspace"))
    stack.enter_context(patch.object(run, "_load_runner_config", return_value={}))
    stack.enter_context(patch.object(run, "_emit"))
    stack.enter_context(patch.object(run, "_write_workflow_status"))
    stack.enter_context(patch.object(run, "_require_file"))
    stack.enter_context(patch.object(run, "_require_dir"))
    stack.enter_context(patch("core.opik_tracing.opik.configure"))
    stack.enter_context(patch("core.opik_tracing.opik.Opik", return_value=Mock()))
    stack.enter_context(patch("signal.signal"))
    stack.enter_context(patch("core.materialize.run_materialization"))
    stack.enter_context(patch("run.load_assignments", return_value={"batches": []}))


def _make_mock_lock(acquired: bool) -> Mock:
    lock = MagicMock()
    lock.acquire.return_value = acquired
    lock.__enter__ = Mock(return_value=lock)
    lock.__exit__ = Mock(return_value=False)
    return lock


class WorktreeIsolationInPlaceTests(unittest.TestCase):
    """First run acquires working_tree_lock → in-place path."""

    def _run_main(self, stack: ExitStack, tmpdir: str, workflow_input: SimpleNamespace):
        _install_fake_workflow_modules(stack)
        _base_patches(stack, tmpdir, workflow_input)

        change_lock = _make_mock_lock(True)
        working_lock = _make_mock_lock(True)
        admin_lock = _make_mock_lock(True)

        stack.enter_context(patch.object(run, "change_run_lock", return_value=change_lock))
        stack.enter_context(patch.object(run, "working_tree_lock", return_value=working_lock))
        stack.enter_context(patch.object(run, "git_admin_lock", return_value=admin_lock))
        prepare_branch = stack.enter_context(
            patch.object(run, "prepare_repo_branch", return_value="feature/wi-001-test-feature")
        )
        prepare_worktree = stack.enter_context(patch.object(run, "prepare_repo_worktree"))
        graphify = stack.enter_context(patch.object(run, "ensure_graphify_index"))
        remove_wt = stack.enter_context(patch.object(run, "remove_worktree"))

        run.main(repo="/tmp/repo", story_file="/tmp/story.json", skip_materialize=True)
        return prepare_branch, prepare_worktree, graphify, remove_wt, change_lock, working_lock

    def test_in_place_calls_prepare_repo_branch(self):
        wi = _workflow_input()
        with tempfile.TemporaryDirectory() as tmpdir, ExitStack() as stack:
            prepare_branch, prepare_worktree, *_ = self._run_main(stack, tmpdir, wi)

        prepare_branch.assert_called_once()
        prepare_worktree.assert_not_called()

    def test_in_place_calls_graphify_with_base_repo(self):
        wi = _workflow_input(repo="/tmp/repo")
        with tempfile.TemporaryDirectory() as tmpdir, ExitStack() as stack:
            _, _, graphify, *_ = self._run_main(stack, tmpdir, wi)

        graphify.assert_called_once_with("/tmp/repo")

    def test_in_place_releases_working_lock_in_finally(self):
        wi = _workflow_input()
        with tempfile.TemporaryDirectory() as tmpdir, ExitStack() as stack:
            *_, working_lock = self._run_main(stack, tmpdir, wi)[:6]

        working_lock.release.assert_called()

    def test_in_place_no_remove_worktree(self):
        wi = _workflow_input()
        with tempfile.TemporaryDirectory() as tmpdir, ExitStack() as stack:
            _, _, _, remove_wt, *_ = self._run_main(stack, tmpdir, wi)

        remove_wt.assert_not_called()

    def test_in_place_change_lock_released_in_finally(self):
        wi = _workflow_input()
        with tempfile.TemporaryDirectory() as tmpdir, ExitStack() as stack:
            *_, change_lock, _ = self._run_main(stack, tmpdir, wi)[:6]

        change_lock.release.assert_called()

    def test_observability_uses_base_repo_not_stage_repo(self):
        wi = _workflow_input(repo="/tmp/base-repo")
        with tempfile.TemporaryDirectory() as tmpdir, ExitStack() as stack:
            _install_fake_workflow_modules(stack)
            _base_patches(stack, tmpdir, wi)

            stack.enter_context(patch.object(run, "change_run_lock", return_value=_make_mock_lock(True)))
            stack.enter_context(patch.object(run, "working_tree_lock", return_value=_make_mock_lock(True)))
            stack.enter_context(patch.object(run, "git_admin_lock", return_value=_make_mock_lock(True)))
            stack.enter_context(patch.object(run, "prepare_repo_branch", return_value="feature/x"))
            stack.enter_context(patch.object(run, "ensure_graphify_index"))
            emit_mock = stack.enter_context(patch.object(run, "_emit"))
            stack.enter_context(patch.object(run, "_write_workflow_status"))

            run.main(repo="/tmp/base-repo", story_file="/tmp/story.json", skip_materialize=True)

        job_start_calls = [c for c in emit_mock.call_args_list if c.args and c.args[0] == "job.start"]
        for c in job_start_calls:
            self.assertEqual(c.kwargs.get("repo"), "/tmp/base-repo")


class WorktreeIsolationFallbackTests(unittest.TestCase):
    """Second run — working_tree_lock contended → worktree fallback."""

    def _run_main_worktree_path(self, stack: ExitStack, tmpdir: str, workflow_input: SimpleNamespace):
        _install_fake_workflow_modules(stack)
        _base_patches(stack, tmpdir, workflow_input)

        change_lock = _make_mock_lock(True)
        working_lock = _make_mock_lock(False)   # contended
        admin_lock = _make_mock_lock(True)

        stack.enter_context(patch.object(run, "change_run_lock", return_value=change_lock))
        stack.enter_context(patch.object(run, "working_tree_lock", return_value=working_lock))
        stack.enter_context(patch.object(run, "git_admin_lock", return_value=admin_lock))

        fake_wt_path = Path(tmpdir) / "worktrees" / "wi-001"
        fake_wt_path.mkdir(parents=True, exist_ok=True)
        prepare_worktree = stack.enter_context(
            patch.object(run, "prepare_repo_worktree", return_value=(fake_wt_path, "feature/wi-001-test-feature"))
        )
        prepare_branch = stack.enter_context(patch.object(run, "prepare_repo_branch"))
        graphify = stack.enter_context(patch.object(run, "ensure_graphify_index"))
        remove_wt = stack.enter_context(patch.object(run, "remove_worktree"))

        # Capture AGENT_RUNNER_REPO seen by stages via step_intake kwargs
        captured_repos: list[str] = []
        orig_step_intake = sys.modules["core.steps"].step_intake

        def capturing_step_intake(**kwargs):
            captured_repos.append(os.environ.get("AGENT_RUNNER_REPO", ""))
            return orig_step_intake(**kwargs)

        sys.modules["core.steps"].step_intake = capturing_step_intake

        run.main(repo="/tmp/repo", story_file="/tmp/story.json", skip_materialize=True)

        return prepare_worktree, prepare_branch, graphify, remove_wt, fake_wt_path, captured_repos, change_lock

    def test_worktree_path_calls_prepare_repo_worktree_not_branch(self):
        wi = _workflow_input()
        with tempfile.TemporaryDirectory() as tmpdir, ExitStack() as stack:
            prepare_worktree, prepare_branch, *_ = self._run_main_worktree_path(stack, tmpdir, wi)

        prepare_worktree.assert_called_once()
        prepare_branch.assert_not_called()

    def test_worktree_path_skips_graphify(self):
        wi = _workflow_input()
        with tempfile.TemporaryDirectory() as tmpdir, ExitStack() as stack:
            _, _, graphify, *_ = self._run_main_worktree_path(stack, tmpdir, wi)

        graphify.assert_not_called()

    def test_worktree_path_removes_worktree_in_finally(self):
        wi = _workflow_input()
        with tempfile.TemporaryDirectory() as tmpdir, ExitStack() as stack:
            _, _, _, remove_wt, fake_wt_path, *_ = self._run_main_worktree_path(stack, tmpdir, wi)

        remove_wt.assert_called_once()
        call_kwargs = remove_wt.call_args.kwargs
        self.assertEqual(Path(call_kwargs["worktree_path"]), fake_wt_path)

    def test_worktree_path_repoints_env(self):
        wi = _workflow_input()
        with tempfile.TemporaryDirectory() as tmpdir, ExitStack() as stack:
            _, _, _, _, fake_wt_path, captured_repos, _ = self._run_main_worktree_path(stack, tmpdir, wi)

        self.assertTrue(any(str(fake_wt_path) in r for r in captured_repos),
                        f"Expected worktree path in captured repos: {captured_repos}")

    def test_worktree_path_change_lock_released(self):
        wi = _workflow_input()
        with tempfile.TemporaryDirectory() as tmpdir, ExitStack() as stack:
            *_, change_lock = self._run_main_worktree_path(stack, tmpdir, wi)

        change_lock.release.assert_called()

    def test_env_restored_after_run(self):
        original = os.environ.get("AGENT_RUNNER_REPO")
        wi = _workflow_input()
        with tempfile.TemporaryDirectory() as tmpdir, ExitStack() as stack:
            self._run_main_worktree_path(stack, tmpdir, wi)

        self.assertEqual(os.environ.get("AGENT_RUNNER_REPO"), original)


class DuplicateChangeIdTests(unittest.TestCase):
    """change_run_lock contended → fail fast."""

    def test_duplicate_change_id_raises(self):
        wi = _workflow_input(change_id="WI-DUP")
        with tempfile.TemporaryDirectory() as tmpdir, ExitStack() as stack:
            _install_fake_workflow_modules(stack)
            _base_patches(stack, tmpdir, wi)

            change_lock = _make_mock_lock(False)  # contended
            stack.enter_context(patch.object(run, "change_run_lock", return_value=change_lock))
            stack.enter_context(patch.object(run, "working_tree_lock", return_value=_make_mock_lock(True)))
            stack.enter_context(patch.object(run, "git_admin_lock", return_value=_make_mock_lock(True)))
            stack.enter_context(patch.object(run, "prepare_repo_branch"))

            with self.assertRaises(RuntimeError) as cm:
                run.main(repo="/tmp/repo", story_file="/tmp/story.json", skip_materialize=True)

        self.assertIn("WI-DUP", str(cm.exception))
        self.assertIn("already active", str(cm.exception).lower())


class WorktreeCleanupOnFailureTests(unittest.TestCase):
    """Worktree is removed even when a stage raises."""

    def test_remove_worktree_called_on_stage_failure(self):
        wi = _workflow_input()
        with tempfile.TemporaryDirectory() as tmpdir, ExitStack() as stack:
            steps_module, _ = _install_fake_workflow_modules(stack)
            _base_patches(stack, tmpdir, wi)

            steps_module.step_intake.side_effect = RuntimeError("stage blew up")

            change_lock = _make_mock_lock(True)
            working_lock = _make_mock_lock(False)  # worktree path
            admin_lock = _make_mock_lock(True)
            stack.enter_context(patch.object(run, "change_run_lock", return_value=change_lock))
            stack.enter_context(patch.object(run, "working_tree_lock", return_value=working_lock))
            stack.enter_context(patch.object(run, "git_admin_lock", return_value=admin_lock))

            fake_wt_path = Path(tmpdir) / "worktrees" / "wi-fail"
            fake_wt_path.mkdir(parents=True, exist_ok=True)
            stack.enter_context(
                patch.object(run, "prepare_repo_worktree",
                             return_value=(fake_wt_path, "feature/wi-001-test-feature"))
            )
            remove_wt = stack.enter_context(patch.object(run, "remove_worktree"))

            with self.assertRaises(RuntimeError):
                run.main(repo="/tmp/repo", story_file="/tmp/story.json", skip_materialize=True)

        remove_wt.assert_called_once()
        change_lock.release.assert_called()


if __name__ == "__main__":
    unittest.main()
