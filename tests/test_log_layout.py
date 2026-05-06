from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import run
from server.paths import RUNNER_ROOT, events_path_for
from server.runner_proc import prepare_job_paths


class LogLayoutTests(unittest.TestCase):
    def _load_script_module(self, relative_path: str, module_name: str):
        path = RUNNER_ROOT / relative_path
        spec = importlib.util.spec_from_file_location(module_name, path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_easy__events_path_for_routes_to_top_level_logs_directory(self):
        path = events_path_for("TEST-LOG-001")
        self.assertEqual(path, RUNNER_ROOT / "logs" / "TEST-LOG-001" / "events.jsonl")

    def test_easy__prepare_job_paths_returns_top_level_event_log_path(self):
        events_path, cassette_path = prepare_job_paths("TEST-LOG-002", "live")
        self.assertEqual(events_path, str(RUNNER_ROOT / "logs" / "TEST-LOG-002" / "events.jsonl"))
        self.assertIsNone(cassette_path)

    def test_medium__clean_workspace_removes_artifacts_and_logs_for_change_id_and_run_variants(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            agent_context_root = tmp_root / "agent-context"
            logs_root = tmp_root / "logs"
            for path in (
                agent_context_root / "TEST-LOG-003",
                agent_context_root / "TEST-LOG-003-RUN-01",
                logs_root / "TEST-LOG-003",
                logs_root / "TEST-LOG-003-RUN-01",
            ):
                path.mkdir(parents=True, exist_ok=True)
                (path / "sentinel.txt").write_text("x", encoding="utf-8")

            with patch.object(run, "AGENT_CONTEXT_ROOT", agent_context_root), patch.object(run, "LOGS_ROOT", logs_root):
                run.clean_workspace("TEST-LOG-003")

            self.assertFalse((agent_context_root / "TEST-LOG-003").exists())
            self.assertFalse((agent_context_root / "TEST-LOG-003-RUN-01").exists())
            self.assertFalse((logs_root / "TEST-LOG-003").exists())
            self.assertFalse((logs_root / "TEST-LOG-003-RUN-01").exists())

    def test_medium__init_session_log_writes_to_top_level_logs_sibling_of_agent_context(self):
        module = self._load_script_module("agent-script-source/init-session-log.py", "init_session_log_test")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            artifact_root = tmp_root / "agent-context"
            artifact_root.mkdir(parents=True, exist_ok=True)
            argv = [
                "init-session-log.py",
                str(artifact_root),
                "TEST-LOG-004",
                "qa",
                "session",
                "2",
            ]
            with patch.object(sys, "argv", argv):
                rc = module.main()

            self.assertEqual(rc, 0)
            log_dir = tmp_root / "logs" / "TEST-LOG-004" / "qa"
            log_files = list(log_dir.glob("*_session.json"))
            self.assertEqual(len(log_files), 1)
            payload = json.loads(log_files[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["change_id"], "TEST-LOG-004")
            self.assertEqual(payload["iteration"], 2)
            self.assertFalse((artifact_root / "TEST-LOG-004" / "logs").exists())

    def test_medium__init_artifact_dirs_splits_artifacts_and_logs(self):
        module = self._load_script_module("agent-script-source/init-artifact-dirs.py", "init_artifact_dirs_test")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            artifact_root = tmp_root / "agent-context"
            artifact_root.mkdir(parents=True, exist_ok=True)
            argv = ["init-artifact-dirs.py", str(artifact_root), "TEST-LOG-005"]
            with patch.object(sys, "argv", argv):
                rc = module.main()

            self.assertEqual(rc, 0)
            self.assertTrue((artifact_root / "TEST-LOG-005" / "intake").is_dir())
            self.assertTrue((artifact_root / "TEST-LOG-005" / "qa" / "evidence" / "logs").is_dir())
            self.assertTrue((tmp_root / "logs" / "TEST-LOG-005" / "software_engineer").is_dir())
            self.assertTrue((tmp_root / "logs" / "TEST-LOG-005" / "qa_evaluator").is_dir())
            self.assertFalse((artifact_root / "TEST-LOG-005" / "logs").exists())

    def test_medium__generate_obsidian_archive_discovers_top_level_logs_with_legacy_fallback(self):
        module = self._load_script_module("agent-script-source/generate-obsidian-archive.py", "generate_obsidian_archive_test")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            artifact_dir = tmp_root / "agent-context" / "TEST-LOG-006"
            artifact_dir.mkdir(parents=True, exist_ok=True)

            current_log = tmp_root / "logs" / "TEST-LOG-006" / "task_generator"
            current_log.mkdir(parents=True, exist_ok=True)
            (current_log / "20260506_120000_session.json").write_text("{}\n", encoding="utf-8")

            discovered = module.discover_agent_logs(str(artifact_dir))
            self.assertIn("task_generator", discovered)

            legacy_dir = tmp_root / "legacy-root" / "TEST-LOG-007"
            (legacy_dir / "logs" / "qa").mkdir(parents=True, exist_ok=True)
            ((legacy_dir / "logs" / "qa") / "20260506_120000_session.json").write_text("{}\n", encoding="utf-8")
            discovered_legacy = module.discover_agent_logs(str(legacy_dir))
            self.assertIn("qa", discovered_legacy)


if __name__ == "__main__":
    unittest.main()

