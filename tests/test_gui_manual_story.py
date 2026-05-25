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

    def test_easy__runs_tab_feedback_filters_and_idle_copy_are_clear(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        html = (repo_root / "gui" / "index.html").read_text(encoding="utf-8")

        self.assertIn("Submit a run to see live logs here", html)
        self.assertNotIn("Start by pasting a story manually. Azure DevOps integration is optional.", html)
        self.assertIn('role="radiogroup" aria-label="Story input mode"', html)
        self.assertIn('role="radio" aria-checked="true" aria-controls="story-source-manual"', html)
        self.assertIn('id="story-source-ado" hidden', html)
        self.assertIn('id="story-source-story_file" hidden', html)
        self.assertIn('id="f-change-help"', html)
        self.assertIn("Auto-generate when blank", html)
        self.assertIn('id="submit-feedback"', html)
        self.assertIn('role="status" aria-live="polite"', html)
        self.assertIn('data-history-status="active"', html)
        self.assertIn('data-history-range="today"', html)
        self.assertIn('data-history-range="24h"', html)
        self.assertIn('data-history-range="30d"', html)
        self.assertIn('data-history-status="queued"', html)
        self.assertIn('id="history-runner-filter"', html)
        self.assertIn('data-history-density="compact"', html)
        self.assertIn('timeZoneName:"short"', html)
        self.assertIn('toast("Submitted "+r.job_id, false, "success")', html)


if __name__ == "__main__":
    unittest.main()
