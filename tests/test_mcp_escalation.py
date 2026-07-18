"""Tests for the MCP escalation infrastructure.

Covers:
  - core.mcp_configs: config generation and idempotent registration helpers
  - core.steps: intake prompt trailer content and write-order guarantees
  - core.run_cmds: --mcp-config injection into Claude CLI cmd, escalation protocol injection
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# core.mcp_configs tests
# ---------------------------------------------------------------------------

class WriteClaudioMcpConfigTests(unittest.TestCase):
    def test_easy__writes_valid_json_with_mcp_server_entry(self):
        from core.mcp_configs import write_claude_mcp_config, MCP_SERVER_NAME

        config_path = write_claude_mcp_config("TEST-9999")
        try:
            self.assertTrue(config_path.exists())
            with open(config_path, encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertIn("mcpServers", data)
            self.assertIn(MCP_SERVER_NAME, data["mcpServers"])
            entry = data["mcpServers"][MCP_SERVER_NAME]
            self.assertIn("command", entry)
            self.assertIn("core.escalation_mcp_server", entry["args"])
            self.assertEqual(entry.get("type"), "stdio")
            self.assertEqual(entry["env"]["AGENT_RUNNER_CHANGE_ID"], "TEST-9999")
        finally:
            config_path.unlink(missing_ok=True)

    def test_easy__change_id_embedded_in_env(self):
        from core.mcp_configs import write_claude_mcp_config, MCP_SERVER_NAME

        path = write_claude_mcp_config("CHANGE-42")
        try:
            data = json.loads(path.read_text())
            env = data["mcpServers"][MCP_SERVER_NAME]["env"]
            self.assertEqual(env["AGENT_RUNNER_CHANGE_ID"], "CHANGE-42")
        finally:
            path.unlink(missing_ok=True)

    def test_medium__runner_python_preserves_venv_launcher(self):
        from core.mcp_configs import _RUNNER_ROOT, _runner_python

        expected = _RUNNER_ROOT / ".venv" / "bin" / "python3"
        if expected.exists():
            self.assertEqual(_runner_python(), str(expected))


class EnsureGeminiMcpRegisteredTests(unittest.TestCase):
    def test_medium__adds_entry_to_settings_json(self):
        from core.mcp_configs import ensure_gemini_mcp_registered, MCP_SERVER_NAME

        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = Path(tmpdir) / "settings.json"
            settings_path.write_text(json.dumps({"hooks": {}}), encoding="utf-8")

            with patch("core.mcp_configs._GEMINI_SETTINGS", settings_path):
                newly = ensure_gemini_mcp_registered()

            self.assertTrue(newly)
            data = json.loads(settings_path.read_text())
            self.assertIn(MCP_SERVER_NAME, data["mcpServers"])

    def test_medium__idempotent_on_second_call(self):
        from core.mcp_configs import ensure_gemini_mcp_registered, MCP_SERVER_NAME

        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = Path(tmpdir) / "settings.json"
            settings_path.write_text(json.dumps({"hooks": {}}), encoding="utf-8")

            with patch("core.mcp_configs._GEMINI_SETTINGS", settings_path):
                ensure_gemini_mcp_registered()
                second = ensure_gemini_mcp_registered()

            self.assertFalse(second)

    def test_easy__returns_false_when_settings_missing(self):
        from core.mcp_configs import ensure_gemini_mcp_registered

        nonexistent = Path("/tmp/nonexistent-gemini-settings-12345.json")
        with patch("core.mcp_configs._GEMINI_SETTINGS", nonexistent):
            result = ensure_gemini_mcp_registered()
        self.assertFalse(result)


class EnsureOmpMcpRegisteredTests(unittest.TestCase):
    def test_medium__creates_config_when_absent(self):
        from core.mcp_configs import ensure_omp_mcp_registered, MCP_SERVER_NAME

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / ".omp" / "agent" / "mcp.json"
            with patch("core.mcp_configs._OMP_MCP_CONFIG", cfg_path):
                newly = ensure_omp_mcp_registered()

            self.assertTrue(newly)
            self.assertTrue(cfg_path.exists())
            data = json.loads(cfg_path.read_text())
            self.assertIn(MCP_SERVER_NAME, data["mcpServers"])
            self.assertEqual(data["mcpServers"][MCP_SERVER_NAME]["type"], "stdio")

    def test_medium__preserves_existing_servers(self):
        from core.mcp_configs import ensure_omp_mcp_registered, MCP_SERVER_NAME

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / "mcp.json"
            cfg_path.write_text(
                json.dumps({"mcpServers": {"fs": {"type": "stdio", "command": "npx"}}}),
                encoding="utf-8",
            )
            with patch("core.mcp_configs._OMP_MCP_CONFIG", cfg_path):
                ensure_omp_mcp_registered()

            data = json.loads(cfg_path.read_text())
            self.assertIn("fs", data["mcpServers"])
            self.assertIn(MCP_SERVER_NAME, data["mcpServers"])

    def test_medium__idempotent_on_second_call(self):
        from core.mcp_configs import ensure_omp_mcp_registered

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / "mcp.json"
            with patch("core.mcp_configs._OMP_MCP_CONFIG", cfg_path):
                first = ensure_omp_mcp_registered()
                second = ensure_omp_mcp_registered()

            self.assertTrue(first)
            self.assertFalse(second)


class EnsureCodexMcpRegisteredTests(unittest.TestCase):
    def test_easy__returns_false_when_existing_registration_matches(self):
        from core import mcp_configs

        expected_python = str(mcp_configs._RUNNER_ROOT / ".venv" / "bin" / "python3")
        probe = MagicMock(
            returncode=0,
            stdout=(
                "agent-workbench-escalation\n"
                "  command: "
                + expected_python
                + "\n  args: -m core.escalation_mcp_server\n"
            ),
            stderr="",
        )

        with (
            patch.object(mcp_configs, "_is_codex_available", return_value=True),
            patch.object(mcp_configs, "_runner_python", return_value=expected_python),
            patch.object(mcp_configs.subprocess, "run", return_value=probe) as run_mock,
        ):
            result = mcp_configs.ensure_codex_mcp_registered()

        self.assertFalse(result)
        self.assertEqual(run_mock.call_count, 1)

    def test_medium__refreshes_stale_existing_registration(self):
        from core import mcp_configs

        expected_python = str(mcp_configs._RUNNER_ROOT / ".venv" / "bin" / "python3")
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            if cmd[:3] == ["codex", "mcp", "get"]:
                result.stdout = (
                    "agent-workbench-escalation\n"
                    "  command: /opt/homebrew/bin/python3\n"
                    "  args: -m core.escalation_mcp_server\n"
                )
            else:
                result.stdout = ""
            return result

        with (
            patch.object(mcp_configs, "_is_codex_available", return_value=True),
            patch.object(mcp_configs, "_runner_python", return_value=expected_python),
            patch.object(mcp_configs.subprocess, "run", side_effect=fake_run),
        ):
            result = mcp_configs.ensure_codex_mcp_registered()

        self.assertTrue(result)
        self.assertEqual(calls[0], ["codex", "mcp", "get", mcp_configs.MCP_SERVER_NAME])
        self.assertEqual(calls[1], ["codex", "mcp", "remove", mcp_configs.MCP_SERVER_NAME])
        self.assertEqual(calls[2][0:4], ["codex", "mcp", "add", mcp_configs.MCP_SERVER_NAME])
        self.assertIn(expected_python, calls[2])


class EscalationMcpServerStdioTests(unittest.TestCase):
    def test_medium__initialize_response_is_clean_json_on_stdout(self):
        """The stdio server must not print banners before JSON-RPC responses."""
        from core.mcp_configs import _runner_python

        env = {
            **os.environ,
            "AGENT_RUNNER_CHANGE_ID": "TEST-MCP-STDIO",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        }
        proc = subprocess.Popen(
            [_runner_python(), "-m", "core.escalation_mcp_server"],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            }
            stdout, stderr = proc.communicate(
                input=json.dumps(payload, separators=(",", ":")) + "\n",
                timeout=5,
            )
            lines = [line.strip() for line in stdout.splitlines() if line.strip()]
            self.assertTrue(lines, stderr[:2000])
            line = lines[0]
            self.assertTrue(line.startswith("{"), repr(line[:80]))
            response = json.loads(line)
            self.assertEqual(response["jsonrpc"], "2.0")
            self.assertEqual(response["id"], 1)
            self.assertIn("result", response)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)


# ---------------------------------------------------------------------------
# core.steps intake prompt tests
# ---------------------------------------------------------------------------

class IntakePromptTrailerTests(unittest.TestCase):
    def _build_ado_prompt(self, change_id: str = "5034224") -> str:
        from core.steps import build_intake_prompt
        return build_intake_prompt(
            intake_source="https://dev.azure.com/org/project/_workitems/edit/5034224",
            repo="/repo",
            change_id=change_id,
            intake_mode="ado",
            runner="claude",
        )

    def _build_synthetic_prompt(self, change_id: str = "EVAL-001") -> str:
        from core.steps import build_intake_prompt
        return build_intake_prompt(
            intake_source="/path/to/fixture.json",
            repo="/repo",
            change_id=change_id,
            intake_mode="synthetic",
            runner="claude",
        )

    def test_medium__ado_prompt_requires_write_artifacts_first(self):
        prompt = self._build_ado_prompt()
        self.assertIn("Write all three canonical artifacts FIRST", prompt)
        self.assertIn("story.yaml", prompt)
        self.assertIn("config.yaml", prompt)
        self.assertIn("constraints.md", prompt)

    def test_medium__ado_prompt_requires_request_user_input_tool(self):
        prompt = self._build_ado_prompt()
        self.assertIn("request_user_input", prompt)
        self.assertIn("Do NOT print the question into the chat", prompt)
        self.assertIn("chat output cannot be answered in single-turn runners", prompt)

    def test_medium__ado_prompt_immutable_ac_rule(self):
        prompt = self._build_ado_prompt()
        self.assertIn("immutable", prompt.lower())
        self.assertIn("NEVER edit, reorder, renumber, or delete", prompt)

    def test_medium__ado_prompt_termination_contract(self):
        prompt = self._build_ado_prompt()
        self.assertIn("MUST have written", prompt)
        self.assertIn("Termination contract", prompt)

    def test_medium__synthetic_prompt_same_trailer(self):
        prompt = self._build_synthetic_prompt()
        self.assertIn("Write all three canonical artifacts FIRST", prompt)
        self.assertIn("request_user_input", prompt)
        self.assertIn("immutable", prompt.lower())

    def test_easy__no_longer_contains_old_unconditional_trailer(self):
        prompt = self._build_ado_prompt()
        self.assertNotIn(
            "After gathering and normalizing the intake items, use the interrogate-eng skill to\ninterrogate the story",
            prompt,
        )


# ---------------------------------------------------------------------------
# core.run_cmds: Claude cmd includes --mcp-config
# ---------------------------------------------------------------------------

class ClaudeCmdMcpConfigTests(unittest.TestCase):
    def test_medium__claude_cmd_injects_mcp_config_flag(self):
        """run_claude_cmd should include --mcp-config when AGENT_RUNNER_CHANGE_ID is set."""
        from core import run_cmds

        captured_cmd: list[list[str]] = []

        def fake_run_cli(cmd, **kwargs):
            captured_cmd.append(list(cmd))
            r = MagicMock()
            r.stdout = '{"result": "ok", "total_input_tokens": 10, "total_output_tokens": 5, "cost_usd": 0.0}'
            r.stderr = ""
            r.returncode = 0
            r.start_time = 0.0
            r.end_time = 0.0
            return r

        fake_mcp_path = Path("/tmp/fake-mcp-config.json")
        fake_mcp_path.write_text('{"mcpServers": {}}', encoding="utf-8")

        with (
            patch.object(run_cmds, "_run_cli", side_effect=fake_run_cli),
            patch.dict(os.environ, {"AGENT_RUNNER_CHANGE_ID": "TEST-CLI-MCP"}),
            patch("core.mcp_configs.write_claude_mcp_config", return_value=fake_mcp_path),
        ):
            run_cmds.run_claude_cmd(
                prompt="Hello",
                agent="intake",
                model="claude-sonnet-4-6",
            )

        self.assertTrue(captured_cmd, "run_cli was not called")
        cmd = captured_cmd[0]
        self.assertIn("--mcp-config", cmd)
        idx = cmd.index("--mcp-config")
        self.assertEqual(cmd[idx + 1], str(fake_mcp_path))

        fake_mcp_path.unlink(missing_ok=True)

    def test_easy__claude_cmd_skips_mcp_config_when_no_change_id(self):
        """Without AGENT_RUNNER_CHANGE_ID, --mcp-config should not appear."""
        from core import run_cmds

        captured_cmd: list[list[str]] = []

        def fake_run_cli(cmd, **kwargs):
            captured_cmd.append(list(cmd))
            r = MagicMock()
            r.stdout = '{"result": "ok", "total_input_tokens": 5, "total_output_tokens": 3, "cost_usd": 0.0}'
            r.stderr = ""
            r.returncode = 0
            r.start_time = 0.0
            r.end_time = 0.0
            return r

        env_without_id = {k: v for k, v in os.environ.items() if k != "AGENT_RUNNER_CHANGE_ID"}
        with (
            patch.object(run_cmds, "_run_cli", side_effect=fake_run_cli),
            patch.dict(os.environ, env_without_id, clear=True),
        ):
            run_cmds.run_claude_cmd(
                prompt="Hello",
                agent="intake",
                model="claude-sonnet-4-6",
            )

        if captured_cmd:
            self.assertNotIn("--mcp-config", captured_cmd[0])


# ---------------------------------------------------------------------------
# core.run_cmds: ESCALATION_PROTOCOL in Gemini/Codex prompts
# ---------------------------------------------------------------------------

class EscalationProtocolInjectionTests(unittest.TestCase):
    def test_easy__gemini_prompt_contains_escalation_protocol(self):
        from core.run_cmds import _build_gemini_prompt, _ESCALATION_PROTOCOL

        with patch("core.run_cmds.build_runner_agent_instructions", return_value="[agent-prompt]"):
            result = _build_gemini_prompt(prompt="do intake", agent="intake-agent")
        self.assertIn("request_user_input", result)
        self.assertIn("User escalation protocol", result)

    def test_easy__codex_prompt_contains_escalation_protocol(self):
        from core.run_cmds import _build_codex_prompt

        with patch("core.run_cmds.build_runner_agent_instructions", return_value="[agent-prompt]"):
            result = _build_codex_prompt(prompt="do intake", agent="intake-agent")
        self.assertIn("request_user_input", result)
        self.assertIn("User escalation protocol", result)


if __name__ == "__main__":
    unittest.main()
