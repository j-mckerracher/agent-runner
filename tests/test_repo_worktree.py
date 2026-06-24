from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from core.repo_prep import (
    build_feature_branch_name,
    prepare_repo_worktree,
    remove_worktree,
    worktree_path_for,
)
from core.runtime_paths import worktrees_root


def _git(repo: str | Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(repo), check=True, capture_output=True, text=True
    ).stdout.strip()


def _make_origin(tmp: str) -> Path:
    """Create a bare origin repo with a develop branch + initial commit."""
    origin = Path(tmp) / "origin"
    origin.mkdir()
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)

    # Clone to a scratch repo to populate the origin
    scratch = Path(tmp) / "scratch"
    subprocess.run(["git", "clone", str(origin), str(scratch)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(scratch), "config", "user.email", "t@t.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(scratch), "config", "user.name", "T"], check=True, capture_output=True)
    (scratch / "README.md").write_text("init")
    subprocess.run(["git", "-C", str(scratch), "checkout", "-b", "develop"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(scratch), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(scratch), "commit", "-m", "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(scratch), "push", "-u", "origin", "develop"], check=True, capture_output=True)
    return origin


def _make_clone(tmp: str, origin: Path) -> Path:
    clone = Path(tmp) / "clone"
    subprocess.run(["git", "clone", str(origin), str(clone)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(clone), "config", "user.email", "t@t.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(clone), "config", "user.name", "T"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(clone), "checkout", "develop"], check=True, capture_output=True)
    return clone


class WorktreePathForTests(unittest.TestCase):
    def test_safe_change_id(self):
        p = worktree_path_for("WI-123456")
        self.assertTrue(p.is_relative_to(worktrees_root()) or str(p).startswith(str(worktrees_root())))

    def test_path_traversal_change_id(self):
        p = worktree_path_for("../../evil")
        self.assertNotIn("..", p.name)

    def test_slash_in_change_id(self):
        p = worktree_path_for("a/b/c")
        self.assertNotIn("/", p.name[p.name.index("-") + 1:] if "-" in p.name else p.name)
        # name must not contain path separators
        self.assertEqual(p.parent, worktrees_root(create=False) or p.parent)

    def test_space_in_change_id(self):
        p = worktree_path_for("my change id")
        self.assertNotIn(" ", p.name)

    def test_two_different_ids_different_paths(self):
        self.assertNotEqual(worktree_path_for("WI-001"), worktree_path_for("WI-002"))

    def test_same_id_stable(self):
        self.assertEqual(worktree_path_for("WI-001"), worktree_path_for("WI-001"))


class PrepareRepoWorktreeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        origin = _make_origin(self.tmp)
        self.repo = _make_clone(self.tmp, origin)

    def test_new_branch_creates_worktree(self):
        wt, branch = prepare_repo_worktree(
            repo=self.repo, change_id="WI-999", description_source="my feature"
        )
        self.assertTrue(wt.exists())
        actual_branch = _git(wt, "branch", "--show-current")
        self.assertEqual(actual_branch, branch)
        expected = build_feature_branch_name("WI-999", "my feature")
        self.assertEqual(branch, expected)

    def test_existing_local_branch_attaches(self):
        branch = build_feature_branch_name("WI-998", "existing")
        subprocess.run(
            ["git", "-C", str(self.repo), "checkout", "-b", branch],
            check=True, capture_output=True
        )
        subprocess.run(["git", "-C", str(self.repo), "checkout", "develop"],
                       check=True, capture_output=True)

        wt, result_branch = prepare_repo_worktree(
            repo=self.repo, change_id="WI-998", description_source="existing"
        )
        self.assertEqual(result_branch, branch)
        self.assertEqual(_git(wt, "branch", "--show-current"), branch)

    def test_remote_only_branch_tracks_upstream(self):
        branch = build_feature_branch_name("WI-997", "remote feat")
        # Create branch on origin via scratch-like setup
        subprocess.run(
            ["git", "-C", str(self.repo), "checkout", "-b", branch],
            check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "push", "-u", "origin", branch],
            check=True, capture_output=True
        )
        # Delete local branch so only remote exists
        subprocess.run(["git", "-C", str(self.repo), "checkout", "develop"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.repo), "branch", "-d", branch],
                       check=True, capture_output=True)

        wt, result_branch = prepare_repo_worktree(
            repo=self.repo, change_id="WI-997", description_source="remote feat"
        )
        self.assertEqual(result_branch, branch)
        # upstream should be set
        upstream = _git(wt, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        self.assertEqual(upstream, f"origin/{branch}")

    def test_remove_worktree_cleans_dir_and_registry(self):
        wt, _ = prepare_repo_worktree(
            repo=self.repo, change_id="WI-996", description_source="to remove"
        )
        self.assertTrue(wt.exists())
        remove_worktree(repo=self.repo, worktree_path=wt)
        self.assertFalse(wt.exists())
        worktree_list = _git(self.repo, "worktree", "list", "--porcelain")
        self.assertNotIn(str(wt), worktree_list)

    def test_two_change_ids_coexist(self):
        wt1, b1 = prepare_repo_worktree(
            repo=self.repo, change_id="WI-111", description_source="alpha"
        )
        wt2, b2 = prepare_repo_worktree(
            repo=self.repo, change_id="WI-222", description_source="beta"
        )
        self.assertTrue(wt1.exists())
        self.assertTrue(wt2.exists())
        self.assertNotEqual(b1, b2)
        self.assertNotEqual(wt1, wt2)

    def test_stale_dir_reclaimed(self):
        wt_path = worktree_path_for("WI-stale")
        wt_path.mkdir(parents=True, exist_ok=True)
        # Not in git worktree registry, so it's stale
        wt, branch = prepare_repo_worktree(
            repo=self.repo, change_id="WI-stale", description_source="reclaim"
        )
        self.assertTrue(wt.exists())
        self.assertEqual(_git(wt, "branch", "--show-current"), branch)


if __name__ == "__main__":
    unittest.main()
