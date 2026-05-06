import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import bootstrap


class BootstrapHelpersTests(unittest.TestCase):
    def test_medium__candidate_dashboard_urls_prefers_detected_localhost_urls(self):
        output = """
        Started services successfully.
        Dashboard: http://localhost:5188
        Reusing http://localhost:5188
        """.strip()

        urls = bootstrap._candidate_dashboard_urls(output)

        self.assertEqual(urls[0], "http://localhost:5188")
        self.assertIn("http://localhost:5173", urls)

    def test_medium__parse_opik_project_url_extracts_dashboard_workspace_and_project_id(self):
        parsed = bootstrap._parse_opik_project_url(
            "http://localhost:5173/workspaceGuard/default/projects/1234-5678"
        )

        self.assertEqual(
            parsed,
            {
                "dashboard_url": "http://localhost:5173",
                "workspace_name": "default",
                "project_id": "1234-5678",
            },
        )

    def test_medium__server_env_contains_local_opik_runtime_settings(self):
        env = bootstrap._server_env(
            {
                "api_url": "http://localhost:5173/api",
                "dashboard_url": "http://localhost:5173",
                "project_id": "abc123",
                "project_name": "agent-runner",
                "workspace_name": "default",
            }
        )

        self.assertEqual(env["OPIK_URL_OVERRIDE"], "http://localhost:5173/api")
        self.assertEqual(env["OPIK_DASHBOARD_URL"], "http://localhost:5173")
        self.assertEqual(env["OPIK_PROJECT_ID"], "abc123")
        self.assertEqual(env["OPIK_PROJECT_NAME"], "agent-runner")
        self.assertEqual(env["OPIK_WORKSPACE"], "default")

    def test_medium__opik_start_command_uses_powershell_on_windows(self):
        with (
            patch.object(bootstrap, "_is_windows", return_value=True),
            patch.object(bootstrap, "_require_command", return_value="powershell.exe"),
        ):
            cmd = bootstrap._opik_start_command(Path("C:/tmp/opik"))

        self.assertEqual(
            cmd,
            [
                "powershell.exe",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(Path("C:/tmp/opik") / "opik.ps1"),
            ],
        )


class BootstrapConfigPersistenceTests(unittest.TestCase):
    def test_medium__save_opik_config_persists_dashboard_metadata_in_runner_config(self):
        with tempfile.TemporaryDirectory(prefix="agentrunner-bootstrap-") as tmpdir:
            with patch.dict(os.environ, {"AGENT_RUNNER_DATA_DIR": tmpdir}, clear=False):
                saved = bootstrap._save_opik_config(
                    {
                        "dashboard_url": "http://localhost:5173",
                        "workspace_name": "default",
                        "project_id": "1234-5678",
                        "project_name": "agent-runner",
                    }
                )

                self.assertEqual(saved["opik"]["dashboard_url"], "http://localhost:5173")
                self.assertEqual(saved["opik"]["workspace_name"], "default")
                self.assertEqual(saved["opik"]["project_id"], "1234-5678")
                self.assertEqual(saved["opik"]["project_name"], "agent-runner")


if __name__ == "__main__":
    unittest.main()
