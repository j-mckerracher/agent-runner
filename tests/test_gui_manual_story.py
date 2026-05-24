import unittest
from pathlib import Path


class GuiManualStorySmokeTests(unittest.TestCase):
    def test_easy__runs_ui_is_manual_first_and_ado_optional(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        html = (repo_root / "gui" / "index.html").read_text(encoding="utf-8")

        self.assertIn("Paste story manually", html)
        self.assertIn("Manual story entry is available.", html)
        self.assertIn("Azure DevOps integration is optional.", html)
        self.assertIn('id="f-manual-title"', html)
        self.assertIn('id="f-story-file"', html)
        self.assertIn('/integrations/azure-devops/status', html)


if __name__ == "__main__":
    unittest.main()
