from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import run
import yaml
from server.paths import RUNNER_ROOT, events_path_for, manual_story_file_path_for
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
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"AGENT_RUNNER_DATA_DIR": tmpdir}, clear=False):
                path = events_path_for("TEST-LOG-001")

        self.assertEqual(path, Path(tmpdir) / "logs" / "TEST-LOG-001" / run.ARTIFACT_FILE_EVENTS)

    def test_easy__prepare_job_paths_returns_top_level_event_log_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"AGENT_RUNNER_DATA_DIR": tmpdir}, clear=False):
                events_path, cassette_path = prepare_job_paths("TEST-LOG-002", "live")

        self.assertEqual(events_path, str(Path(tmpdir) / "logs" / "TEST-LOG-002" / run.ARTIFACT_FILE_EVENTS))
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

    def test_medium__clean_workspace_preserves_manual_job_inputs_outside_workspace_roots(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            agent_context_root = tmp_root / "agent-context"
            logs_root = tmp_root / "logs"
            manual_root = tmp_root / "job-inputs"
            manual_path = manual_root / "job_test" / "manual_story.json"
            manual_path.parent.mkdir(parents=True, exist_ok=True)
            manual_path.write_text("{}", encoding="utf-8")
            (agent_context_root / "TEST-LOG-003").mkdir(parents=True, exist_ok=True)
            (logs_root / "TEST-LOG-003").mkdir(parents=True, exist_ok=True)

            with patch.object(run, "AGENT_CONTEXT_ROOT", agent_context_root), patch.object(run, "LOGS_ROOT", logs_root):
                run.clean_workspace("TEST-LOG-003")

            self.assertTrue(manual_path.exists())

    def test_easy__manual_story_file_path_for_routes_to_data_dir_job_inputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("server.paths.data_dir", return_value=Path(tmpdir)):
                path = manual_story_file_path_for("job_test")

        self.assertEqual(path, Path(tmpdir) / "job-inputs" / "job_test" / "manual_story.json")

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
            # Logs go to logs/{agent_name}/ — no change_id subdirectory
            log_dir = tmp_root / "logs" / "qa"
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
            # Logs go to logs/{agent_name}/ — no change_id subdirectory
            self.assertTrue((tmp_root / "logs" / "software_engineer").is_dir())
            self.assertTrue((tmp_root / "logs" / "qa_evaluator").is_dir())
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

    def test_medium__workflow_status_writes_self_contained_run_metrics_from_events(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            agent_context_root = tmp_root / "agent-context"
            logs_root = tmp_root / "logs"
            (agent_context_root / "TEST-METRICS-001" / run.ARTIFACT_DIR_SUMMARY).mkdir(parents=True)
            event_log = logs_root / "TEST-METRICS-001" / run.ARTIFACT_FILE_EVENTS
            event_log.parent.mkdir(parents=True)
            event_log.write_text(
                "\n".join(
                    [
                        f'{{"ts":"2026-05-20T16:00:00.000000Z","type":"{run.EVENT_TYPE_STAGE_START}","stage":"{run.STAGE_EXECUTION}"}}',
                        '{"ts":"2026-05-20T16:00:01.000000Z","type":"uow.start","uow_id":"UOW-001"}',
                        '{"ts":"2026-05-20T16:00:02.000000Z","type":"opik.start","name":"uow-iteration-1","metadata":{"uow_id":"UOW-001"}}',
                        '{"ts":"2026-05-20T16:00:03.000000Z","type":"cli.exit","agent":"software-engineer-hyperagent","duration_ms":2500,"exit_code":0}',
                        '{"ts":"2026-05-20T16:00:03.100000Z","type":"llm.call","agent":"software-engineer-hyperagent","runner":"claude","model":"claude-sonnet","status":"ok","duration_ms":2500,"attempt":1,"max_attempts":1,"prompt_est_tokens":100,"response_est_tokens":25,"tokens_in":90,"tokens_out":20,"cost_usd":0.02,"prompt_sha256":"abc","response_sha256":"def","response_parse_ok":true}',
                        '{"ts":"2026-05-20T16:00:04.000000Z","type":"metrics","tokens_in":10,"tokens_out":5,"cost_usd":0.01}',
                        f'{{"ts":"2026-05-20T16:00:05.000000Z","type":"{run.EVENT_TYPE_UOW_END}","uow_id":"UOW-001","status":"{run.STATUS_OK}"}}',
                        f'{{"ts":"2026-05-20T16:00:06.000000Z","type":"{run.EVENT_TYPE_STAGE_END}","stage":"{run.STAGE_EXECUTION}","status":"{run.STATUS_OK}"}}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with patch.object(run, "AGENT_CONTEXT_ROOT", agent_context_root), patch.object(run, "LOGS_ROOT", logs_root):
                run._write_workflow_status(
                    change_id="TEST-METRICS-001",
                    status=run.STATUS_SUCCEEDED,
                    runner="claude",
                    model="claude-sonnet",
                    repo="/tmp/repo",
                    exit_code=0,
                    last_completed_stage=run.STAGE_QA,
                )

            status = yaml.safe_load(
                (agent_context_root / "TEST-METRICS-001" / run.ARTIFACT_DIR_SUMMARY / run.WORKFLOW_STATUS_FILENAME).read_text(
                    encoding="utf-8"
                )
            )
            metrics = yaml.safe_load(
                (agent_context_root / "TEST-METRICS-001" / run.ARTIFACT_DIR_SUMMARY / run.ARTIFACT_FILE_RUN_METRICS).read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual(status["observability"]["event_log_artifact"], f"{run.ARTIFACT_DIR_SUMMARY}/{run.ARTIFACT_FILE_EVENTS}")
            self.assertTrue((agent_context_root / "TEST-METRICS-001" / run.ARTIFACT_DIR_SUMMARY / run.ARTIFACT_FILE_EVENTS).is_file())
            self.assertEqual(metrics["metrics"]["stage_durations_seconds"][run.STAGE_EXECUTION], 6.0)
            self.assertEqual(metrics["metrics"]["uow_iterations"]["UOW-001"], 1)
            self.assertEqual(metrics["metrics"]["totals"]["source"], run.EVENT_TYPE_LLM_CALL)
            self.assertEqual(metrics["metrics"]["totals"]["cost_usd"], 0.02)
            self.assertEqual(metrics["metrics"]["legacy_metric_totals"]["cost_usd"], 0.01)
            self.assertEqual(metrics["metrics"]["totals"]["llm_calls"], 1)
            self.assertEqual(metrics["metrics"]["llm_latency"]["overall"]["p95_ms"], 2500.0)
            self.assertEqual(
                metrics["metrics"]["llm_calls_by_agent"]["software-engineer-hyperagent"]["tokens_in"],
                90,
            )
            self.assertEqual(
                metrics["metrics"]["answerability_matrix"]["retry_rate_error_categories_and_recovery"]["status"],
                "direct",
            )


if __name__ == "__main__":
    unittest.main()
