from __future__ import annotations

import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from core.repo_prep import build_feature_branch_name, prepare_repo_branch


class BuildFeatureBranchNameTests(unittest.TestCase):
    def test_easy__uses_short_description_fallback_when_title_is_missing(self) -> None:
        self.assertEqual(
            build_feature_branch_name("WI-123456", None),
            "feature/wi-123456-requested-changes",
        )


class PrepareRepoBranchTests(unittest.TestCase):
    def test_easy__returns_when_repo_is_already_on_feature_branch(self) -> None:
        repo = Path("/tmp/repo")
        feature_branch = "feature/5001016-requested-changes"

        def fake_run_git_command(_repo: Path, *args: str) -> CompletedProcess[str]:
            if args == ("rev-parse", "--is-inside-work-tree"):
                return CompletedProcess(["git", *args], 0, stdout="true\n")
            if args == ("branch", "--show-current"):
                return CompletedProcess(["git", *args], 0, stdout=f"{feature_branch}\n")
            self.fail(f"unexpected git command: {args}")

        with patch("core.repo_prep._run_git_command", side_effect=fake_run_git_command), \
             patch("core.repo_prep._git_ref_exists") as git_ref_exists_mock:
            self.assertEqual(
                prepare_repo_branch(
                    repo=repo,
                    change_id="5001016",
                    description_source=None,
                ),
                feature_branch,
            )

        git_ref_exists_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
