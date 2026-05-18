import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import bootstrap

RUNNER_ROOT = Path(__file__).resolve().parent.parent


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

    def test_medium__server_env_without_opik_clears_runtime_settings(self):
        with patch.dict(
            os.environ,
            {
                "OPIK_BASE_URL": "http://stale/api",
                "OPIK_DASHBOARD_URL": "http://stale",
                "OPIK_PROJECT_ID": "stale-id",
                "OPIK_PROJECT_NAME": "stale-name",
                "OPIK_URL_OVERRIDE": "http://stale/api",
                "OPIK_WORKSPACE": "stale-space",
            },
            clear=False,
        ):
            env = bootstrap._server_env(None)

        self.assertNotIn("OPIK_BASE_URL", env)
        self.assertNotIn("OPIK_DASHBOARD_URL", env)
        self.assertNotIn("OPIK_PROJECT_ID", env)
        self.assertNotIn("OPIK_PROJECT_NAME", env)
        self.assertNotIn("OPIK_URL_OVERRIDE", env)
        self.assertNotIn("OPIK_WORKSPACE", env)

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

    def test_medium__bootstrap_entrypoint_prefers_original_argv0(self):
        expected = (RUNNER_ROOT / "bootstrap.py").resolve()

        with patch.object(bootstrap.sys, "argv", [str(expected), "--reload"]):
            entrypoint = bootstrap._bootstrap_entrypoint()

        self.assertEqual(entrypoint, expected)

    def test_medium__reexec_bootstrap_uses_subprocess_on_windows(self):
        target_python = Path("C:/tmp/.venv/Scripts/python.exe")
        env = {"AGENT_RUNNER_BOOTSTRAP_REEXEC": "1"}

        with (
            patch.object(bootstrap, "_is_windows", return_value=True),
            patch.object(bootstrap, "_bootstrap_entrypoint", return_value=RUNNER_ROOT / "bootstrap.py"),
            patch.object(bootstrap.sys, "argv", ["bootstrap.py", "--reload"]),
            patch.object(bootstrap.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run_mock,
            patch.object(bootstrap.os, "execve") as execve_mock,
        ):
            with self.assertRaises(SystemExit) as raised:
                bootstrap._reexec_bootstrap(target_python, env)

        self.assertEqual(raised.exception.code, 0)
        run_mock.assert_called_once_with(
            [str(target_python), str(RUNNER_ROOT / "bootstrap.py"), "--reload"],
            env=env,
            text=True,
        )
        execve_mock.assert_not_called()

    def test_medium__check_docker_retries_until_probe_succeeds(self):
        timeout_error = subprocess.TimeoutExpired(["docker", "info"], timeout=15)

        with (
            patch.object(bootstrap, "_require_command", return_value="docker"),
            patch.object(bootstrap, "DOCKER_READY_TIMEOUT_SECONDS", 30),
            patch.object(bootstrap, "DOCKER_PROBE_TIMEOUT_SECONDS", 15),
            patch.object(bootstrap, "DOCKER_PROBE_DELAY_SECONDS", 0),
            patch.object(bootstrap, "_docker_info_probe", side_effect=[timeout_error, None]) as probe_mock,
            patch.object(bootstrap.time, "sleep") as sleep_mock,
        ):
            bootstrap._check_docker()

        self.assertEqual(probe_mock.call_count, 2)
        sleep_mock.assert_called_once_with(0)

    def test_medium__check_docker_times_out_after_retry_window(self):
        timeout_error = subprocess.TimeoutExpired(["docker", "info"], timeout=15)

        with (
            patch.object(bootstrap, "_require_command", return_value="docker"),
            patch.object(bootstrap, "DOCKER_READY_TIMEOUT_SECONDS", 1),
            patch.object(bootstrap, "DOCKER_PROBE_TIMEOUT_SECONDS", 15),
            patch.object(bootstrap, "DOCKER_PROBE_DELAY_SECONDS", 0),
            patch.object(bootstrap, "_docker_info_probe", side_effect=timeout_error),
            patch.object(bootstrap.time, "monotonic", side_effect=[0, 0, 2]),
            patch.object(bootstrap.time, "sleep") as sleep_mock,
        ):
            with self.assertRaises(bootstrap.BootstrapError) as raised:
                bootstrap._check_docker()

        self.assertIn("Docker did not respond after 1 seconds.", str(raised.exception))
        sleep_mock.assert_not_called()

    def test_medium__docker_info_probe_kills_hung_process_tree_on_windows(self):
        proc = SimpleNamespace(
            pid=4242,
            args=["docker", "info"],
            wait=unittest.mock.Mock(side_effect=[subprocess.TimeoutExpired(["docker", "info"], timeout=15), 0]),
            poll=unittest.mock.Mock(return_value=None),
            kill=unittest.mock.Mock(),
        )

        with (
            patch.object(bootstrap, "_is_windows", return_value=True),
            patch.object(bootstrap.subprocess, "Popen", return_value=proc),
            patch.object(bootstrap.subprocess, "run") as run_mock,
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                bootstrap._docker_info_probe(timeout=15)

        run_mock.assert_called_once_with(
            ["taskkill", "/PID", "4242", "/T", "/F"],
            stdout=bootstrap.subprocess.DEVNULL,
            stderr=bootstrap.subprocess.DEVNULL,
            check=False,
            text=True,
        )
        proc.wait.assert_called_with(timeout=5)


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


class BootstrapMainFlowTests(unittest.TestCase):
    def test_medium__main_continues_when_docker_is_unavailable(self):
        args = SimpleNamespace(host="127.0.0.1", port=8742, reload=False)

        with (
            patch.object(bootstrap, "parse_args", return_value=args),
            patch.object(bootstrap, "_ensure_virtualenv"),
            patch.object(bootstrap, "_warn_if_no_ai_backend"),
            patch.object(bootstrap, "_check_ztk"),
            patch.object(bootstrap, "_install_requirements"),
            patch.object(bootstrap, "_materialize_agents"),
            patch.object(bootstrap, "_prompt_user_config"),
            patch.object(bootstrap, "_check_docker", side_effect=bootstrap.BootstrapError("docker down")),
            patch.object(bootstrap, "_opik_repo_dir") as opik_repo_dir_mock,
            patch.object(bootstrap, "_sync_opik_repo") as sync_opik_repo_mock,
            patch.object(bootstrap, "_start_local_opik") as start_local_opik_mock,
            patch.object(bootstrap, "_save_opik_config") as save_opik_config_mock,
            patch.object(bootstrap, "_start_server") as start_server_mock,
        ):
            result = bootstrap.main()

        self.assertEqual(result, 0)
        opik_repo_dir_mock.assert_not_called()
        sync_opik_repo_mock.assert_not_called()
        start_local_opik_mock.assert_not_called()
        save_opik_config_mock.assert_not_called()
        start_server_mock.assert_called_once_with(
            host="127.0.0.1",
            port=8742,
            reload=False,
            opik_settings=None,
        )


class BootstrapWrapperTests(unittest.TestCase):
    def test_medium__bootstrap_sh_invokes_scripts_bootstrap_py(self):
        content = (RUNNER_ROOT / "bootstrap.sh").read_text(encoding="utf-8")

        self.assertIn('"$ROOT_DIR/scripts/bootstrap.py"', content)

    def test_medium__bootstrap_py_delegates_to_scripts_bootstrap_main(self):
        content = (RUNNER_ROOT / "bootstrap.py").read_text(encoding="utf-8")

        self.assertIn("from scripts.bootstrap import main", content)
        self.assertIn("raise SystemExit(main())", content)

    def test_medium__bootstrap_ps1_invokes_repo_bootstrap_py(self):
        content = (RUNNER_ROOT / "bootstrap.ps1").read_text(encoding="utf-8")

        self.assertIn('$BootstrapScript = Join-Path $RootDir "bootstrap.py"', content)
        self.assertIn("& py -3 $BootstrapScript", content)
        self.assertIn("& python $BootstrapScript", content)


if __name__ == "__main__":
    unittest.main()
