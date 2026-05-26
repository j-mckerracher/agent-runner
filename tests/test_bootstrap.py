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
    def test_medium__ensure_virtualenv_reexecs_when_venv_python_resolves_to_base_interpreter(self):
        with tempfile.TemporaryDirectory(prefix="agentrunner-bootstrap-") as tmpdir:
            tmp_path = Path(tmpdir)
            venv_dir = tmp_path / ".venv"
            venv_python = venv_dir / "bin" / "python"
            venv_python.parent.mkdir(parents=True)
            venv_python.write_text("", encoding="utf-8")

            current_python = tmp_path / "homebrew" / "bin" / "python3.14"
            shared_python = tmp_path / "cellar" / "bin" / "python3.14"
            current_prefix = tmp_path / "homebrew" / "Frameworks" / "Python.framework" / "Versions" / "3.14"

            real_resolve = Path.resolve

            def fake_resolve(path_self: Path, *args, **kwargs) -> Path:
                if path_self == current_python:
                    return shared_python
                if path_self == venv_python:
                    return shared_python
                if path_self == venv_dir:
                    return venv_dir
                if path_self == current_prefix:
                    return current_prefix
                return real_resolve(path_self, *args, **kwargs)

            with (
                patch.object(bootstrap, "VENV_DIR", venv_dir),
                patch.object(bootstrap.sys, "argv", ["bootstrap.py", "--no-opik"]),
                patch.object(bootstrap.sys, "executable", str(current_python)),
                patch.object(bootstrap.sys, "prefix", str(current_prefix)),
                patch.dict(bootstrap.os.environ, {}, clear=True),
                patch.object(Path, "resolve", autospec=True, side_effect=fake_resolve),
                patch.object(bootstrap.os, "execve") as execve_mock,
            ):
                bootstrap._ensure_virtualenv()

            execve_mock.assert_called_once()
            exec_args = execve_mock.call_args.args
            self.assertEqual(exec_args[0], str(venv_python))
            self.assertEqual(exec_args[1], [str(venv_python), str(RUNNER_ROOT / "bootstrap.py"), "--no-opik"])
            self.assertEqual(exec_args[2][bootstrap.BOOTSTRAP_REEXEC_ENV], "1")

    def test_medium__ensure_virtualenv_creates_missing_venv_before_reexec(self):
        with tempfile.TemporaryDirectory(prefix="agentrunner-bootstrap-") as tmpdir:
            tmp_path = Path(tmpdir)
            venv_dir = tmp_path / ".venv"
            venv_python = venv_dir / "bin" / "python"
            current_python = tmp_path / "python3.14"

            def fake_run(cmd, **kwargs):
                self.assertEqual(cmd, [str(current_python), "-m", "venv", str(venv_dir)])
                venv_python.parent.mkdir(parents=True)
                venv_python.write_text("", encoding="utf-8")
                return SimpleNamespace(returncode=0)

            with (
                patch.object(bootstrap, "VENV_DIR", venv_dir),
                patch.object(bootstrap.sys, "argv", ["bootstrap.py", "--no-opik"]),
                patch.object(bootstrap.sys, "executable", str(current_python)),
                patch.object(bootstrap, "_running_in_runner_venv", return_value=False),
                patch.object(bootstrap, "_run", side_effect=fake_run) as run_mock,
                patch.dict(bootstrap.os.environ, {}, clear=True),
                patch.object(bootstrap.os, "execve") as execve_mock,
            ):
                bootstrap._ensure_virtualenv()

            run_mock.assert_called_once()
            execve_mock.assert_called_once_with(
                str(venv_python),
                [str(venv_python), str(RUNNER_ROOT / "bootstrap.py"), "--no-opik"],
                {bootstrap.BOOTSTRAP_REEXEC_ENV: "1"},
            )

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

    def test_medium__check_docker_runs_docker_info_probe(self):
        with (
            patch.object(bootstrap, "_require_command", return_value="docker"),
            patch.object(bootstrap.subprocess, "run") as run_mock,
        ):
            bootstrap._check_docker()

        run_mock.assert_called_once_with(
            ["docker", "info"],
            stdout=bootstrap.subprocess.DEVNULL,
            stderr=bootstrap.subprocess.DEVNULL,
            check=True,
            text=True,
        )

    def test_medium__check_docker_raises_when_daemon_is_unavailable(self):
        with (
            patch.object(bootstrap, "_require_command", return_value="docker"),
            patch.object(
                bootstrap.subprocess,
                "run",
                side_effect=subprocess.CalledProcessError(returncode=1, cmd=["docker", "info"]),
            ),
        ):
            with self.assertRaises(bootstrap.BootstrapError) as raised:
                bootstrap._check_docker()

        self.assertIn("Docker is not running or not accessible.", str(raised.exception))


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


class BootstrapServerStartupTests(unittest.TestCase):
    def test_medium__start_server_uses_repo_server_main_wrapper(self):
        with (
            patch.object(bootstrap, "_echo_step"),
            patch.object(bootstrap, "_server_env", return_value={"EXAMPLE": "1"}),
            patch.object(bootstrap, "_run") as run_mock,
        ):
            bootstrap._start_server(host="127.0.0.1", port=8742, reload=False, opik_settings=None)

        run_mock.assert_called_once_with(
            [
                bootstrap.sys.executable,
                str(RUNNER_ROOT / "server_main.py"),
                "--host",
                "127.0.0.1",
                "--port",
                "8742",
            ],
            cwd=RUNNER_ROOT,
            env={"EXAMPLE": "1"},
            echo=True,
        )


class BootstrapMainFlowTests(unittest.TestCase):
    def test_medium__main_defaults_to_prompt_flow_when_opik_flags_are_missing(self):
        args = SimpleNamespace(
            host="127.0.0.1",
            port=8742,
            reload=False,
            generate_eval_benchmarks=False,
            skip_eval_benchmarks=False,
            eval_target_repo=None,
            eval_target_sha=None,
            eval_runner=None,
            eval_model=None,
            force_eval_benchmarks=False,
            no_verify_eval_gold_fails=False,
            verify_eval_gold_fails=False,
        )

        with (
            patch.object(bootstrap, "parse_args", return_value=args),
            patch.object(bootstrap, "_ensure_virtualenv"),
            patch.object(bootstrap, "_warn_if_no_ai_backend"),
            patch.object(bootstrap, "_check_rtk"),
            patch.object(bootstrap, "_install_requirements"),
            patch.object(bootstrap, "_materialize_agents") as materialize_mock,
            patch.object(bootstrap, "_prompt_user_config"),
            patch.object(bootstrap, "_prompt_for_opik", return_value=False),
            patch.object(bootstrap, "_check_docker") as check_docker_mock,
            patch.object(bootstrap, "_opik_repo_dir") as opik_repo_dir_mock,
            patch.object(bootstrap, "_sync_opik_repo") as sync_opik_repo_mock,
            patch.object(bootstrap, "_start_local_opik") as start_local_opik_mock,
            patch.object(bootstrap, "_save_opik_config") as save_opik_config_mock,
            patch.object(bootstrap, "_start_server") as start_server_mock,
        ):
            result = bootstrap.main()

        self.assertEqual(result, 0)
        materialize_mock.assert_not_called()
        check_docker_mock.assert_not_called()
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

    def test_medium__main_materializes_only_when_explicitly_requested(self):
        args = SimpleNamespace(
            host="127.0.0.1",
            port=8742,
            reload=False,
            materialize=True,
            generate_eval_benchmarks=False,
            skip_eval_benchmarks=True,
            eval_target_repo=None,
            eval_target_sha=None,
            eval_runner=None,
            eval_model=None,
            force_eval_benchmarks=False,
            no_verify_eval_gold_fails=False,
            verify_eval_gold_fails=False,
            with_opik=False,
            no_opik=True,
        )

        with (
            patch.object(bootstrap, "parse_args", return_value=args),
            patch.object(bootstrap, "_ensure_virtualenv"),
            patch.object(bootstrap, "_warn_if_no_ai_backend"),
            patch.object(bootstrap, "_check_rtk"),
            patch.object(bootstrap, "_install_requirements"),
            patch.object(bootstrap, "_materialize_agents") as materialize_mock,
            patch.object(bootstrap, "_prompt_user_config"),
            patch.object(bootstrap, "_generate_eval_benchmarks"),
            patch.object(bootstrap, "_start_server"),
        ):
            result = bootstrap.main()

        self.assertEqual(result, 0)
        materialize_mock.assert_called_once_with()

    def test_medium__generate_eval_benchmarks_passes_gold_verification_opt_out(self):
        config = {
            "generate": True,
            "repo": "/tmp/target",
            "sha": "abc123",
            "runner": "claude",
            "model": "",
        }
        args = SimpleNamespace(force_eval_benchmarks=False, no_verify_eval_gold_fails=True)

        with (
            patch.object(bootstrap, "_echo_step"),
            patch.object(bootstrap, "_run") as run_mock,
        ):
            bootstrap._generate_eval_benchmarks(config, args)

        command = run_mock.call_args.args[0]
        self.assertIn("--no-verify-gold-fails", command)


class BootstrapWrapperTests(unittest.TestCase):
    def test_medium__bootstrap_sh_invokes_repo_bootstrap_py(self):
        content = (RUNNER_ROOT / "bootstrap.sh").read_text(encoding="utf-8")

        self.assertIn('"$ROOT_DIR/bootstrap.py"', content)

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
