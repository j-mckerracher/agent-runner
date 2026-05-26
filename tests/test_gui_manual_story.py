import re
import unittest

from gui_sources import read_gui_markup, read_gui_scripts, read_gui_styles


class GuiManualStorySmokeTests(unittest.TestCase):
    def test_easy__runs_ui_is_manual_first_and_ado_optional(self) -> None:
        html = read_gui_markup()
        compact_html = " ".join(html.split())
        scripts = read_gui_scripts()

        self.assertIn("Paste story manually", html)
        self.assertIn("Manual story entry is available.", compact_html)
        self.assertIn("Azure DevOps integration is optional.", compact_html)
        self.assertIn('id="f-manual-title"', html)
        self.assertIn('id="f-story-file"', html)
        self.assertIn("/integrations/azure-devops/status", scripts)

    def test_easy__runs_tab_feedback_filters_and_idle_copy_are_clear(self) -> None:
        html = read_gui_markup()
        compact_html = " ".join(html.split())
        styles = read_gui_styles()
        scripts = read_gui_scripts()
        compact_scripts = "".join(scripts.split())

        self.assertIn("Submit a run to see live logs here", compact_html)
        self.assertNotIn(
            "Start by pasting a story manually. Azure DevOps integration is optional.",
            compact_html,
        )
        self.assertIn('role="radiogroup" aria-label="Story input mode"', compact_html)
        self.assertIn(
            'role="radio" aria-checked="true" aria-controls="story-source-manual"',
            compact_html,
        )
        self.assertIn('id="story-source-ado" hidden', compact_html)
        self.assertIn('id="story-source-story_file" hidden', compact_html)
        self.assertIn('id="f-change-help"', html)
        self.assertIn("Auto-generate when blank", html)
        self.assertIn('id="submit-feedback"', compact_html)
        self.assertIn('role="status" aria-live="polite"', compact_html)
        self.assertIn('data-history-status="active"', compact_html)
        self.assertIn('data-history-range="today"', compact_html)
        self.assertIn('data-history-range="24h"', compact_html)
        self.assertIn('data-history-range="30d"', compact_html)
        self.assertIn('data-history-status="queued"', compact_html)
        self.assertIn('id="history-runner-filter"', compact_html)
        self.assertIn('id="runs-history-section"', compact_html)
        self.assertIn('id="history-toggle"', compact_html)
        self.assertIn('aria-controls="history-panel"', compact_html)
        self.assertIn('aria-expanded="true"', compact_html)
        self.assertLess(
            html.index('id="runs-history-section"'),
            html.index('class="runs-detail-panel"'),
        )
        self.assertLess(html.index('id="history-panel"'), html.index('id="term"'))
        self.assertIn(".submit-section:hover::-webkit-scrollbar-thumb", styles)
        self.assertIn(".submit-section:hover::after", styles)
        self.assertIn("constRUN_HISTORY_STATE={", compact_scripts)
        self.assertIn('status:"all"', compact_scripts)
        self.assertIn('range:"all"', compact_scripts)
        self.assertIn('runner:"all"', compact_scripts)
        self.assertIn('collapsed:false', compact_scripts)
        self.assertRegex(
            scripts,
            re.compile(
                r'button\.setAttribute\(\s*"aria-label",\s*expanded\s*\?\s*"Collapse Past Runs"\s*:\s*"Expand Past Runs"',
                re.DOTALL,
            ),
        )
        self.assertRegex(
            scripts,
            re.compile(
                r'label\.textContent\s*=\s*expanded\s*\?\s*"Collapse"\s*:\s*"Expand"',
                re.DOTALL,
            ),
        )
        self.assertIn("functionfmtLocalClockTime(isoStr){", compact_scripts)
        self.assertIn("returnfmtLocalClockTime(ts);", compact_scripts)
        self.assertIn('timeZoneName:"short"', compact_scripts)
        self.assertIn('toast("Submitted"+r.job_id,false,"success")', compact_scripts)

    def test_medium__gui_entrypoint_loads_named_assets_by_concern(self) -> None:
        html = read_gui_markup()
        scripts = read_gui_scripts()

        self.assertIn('/static/assets/css/app.css', html)
        for script_name in (
            'app-core.js',
            'telemetry.js',
            'app-shell.js',
            'runs.js',
            'content-views.js',
            'background.js',
        ):
            self.assertIn(f'/static/assets/js/{script_name}', html)

        self.assertNotIn('<style>', html)
        self.assertNotIn('function telemetryActive()', html)
        self.assertIn('Shared GUI state', scripts)
        self.assertIn('Runs view.', scripts)
        self.assertIn('Run Telemetry view.', scripts)


if __name__ == "__main__":
    unittest.main()
