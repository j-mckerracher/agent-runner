from __future__ import annotations

import unittest

from core.repo_prep import build_feature_branch_name


class BuildFeatureBranchNameTests(unittest.TestCase):
    def test_easy__uses_short_description_fallback_when_title_is_missing(self) -> None:
        self.assertEqual(
            build_feature_branch_name("WI-123456", None),
            "feature/wi-123456-short-description",
        )


if __name__ == "__main__":
    unittest.main()
