import unittest
from unittest.mock import Mock, patch

from server.events import EventBus
from server.runner_proc import JobProcess, _format_failure_summary


class RunnerProcFailureSummaryTests(unittest.TestCase):
    def test_medium__prefers_structured_command_failure_log(self):
        events = [
            {"type": "stage.start", "stage": "execution"},
            {
                "type": "log",
                "level": "error",
                "kind": "command_failed",
                "stage": "execution",
                "msg": "software-engineer command failed (exit 1): missing file",
            },
            {"type": "job.end", "status": "failed", "exit_code": 1},
        ]

        summary = _format_failure_summary(events, "Traceback...", 1)

        self.assertEqual(summary, "execution: software-engineer command failed (exit 1): missing file")

    def test_medium__falls_back_to_stdout_tail_when_no_events_exist(self):
        summary = _format_failure_summary([], "traceback\nValueError: fixture mismatch\n", 1)

        self.assertEqual(summary, "ValueError: fixture mismatch")

    def test_medium__uses_failed_stage_when_command_failure_lacks_stage(self):
        events = [
            {"type": "stage.start", "stage": "qa"},
            {
                "type": "log",
                "level": "error",
                "kind": "command_failed",
                "msg": "qa-engineer command failed (exit 1): qa cli failed",
            },
            {"type": "stage.end", "stage": "qa", "status": "error"},
            {"type": "job.end", "status": "failed", "exit_code": 1},
        ]

        summary = _format_failure_summary(events, "", 1)

        self.assertEqual(summary, "qa: qa-engineer command failed (exit 1): qa cli failed")


class RunnerProcBuildCmdTests(unittest.TestCase):
    def test_easy__includes_log_level_when_present(self):
        job = {
            "id": "job_test",
            "repo": "/tmp/repo",
            "change_id": "TEST-LOG-001",
            "runner": "claude",
            "model": "claude-haiku-4-5-20251001",
            "log_level": "debug",
            "extra_context": "Notes",
            "events_path": "/tmp/events.jsonl",
        }

        cmd = JobProcess(job, EventBus(), None)._build_cmd()

        self.assertIn("--log-level", cmd)
        self.assertEqual(cmd[cmd.index("--log-level") + 1], "debug")

    def test_medium__start_precleans_before_creating_event_log(self):
        job = {
            "id": "job_test",
            "repo": "/tmp/repo",
            "change_id": "TEST-LOG-START-001",
            "runner": "claude",
            "model": "claude-haiku-4-5-20251001",
            "log_level": "debug",
            "events_path": "/tmp/events.jsonl",
        }
        order: list[str] = []

        with patch("server.runner_proc.clean_change_workspace", side_effect=lambda *args, **kwargs: order.append("clean")), \
             patch("server.runner_proc.Path.write_text", side_effect=lambda *args, **kwargs: order.append("truncate")), \
             patch("server.runner_proc.FileTailer") as tailer_cls, \
             patch("server.runner_proc.subprocess.Popen", return_value=Mock(pid=12345)), \
             patch("server.runner_proc.db.update_job"):
            tailer_cls.return_value.start.side_effect = lambda: order.append("tailer")
            JobProcess(job, EventBus(), None).start()

        self.assertEqual(order[:3], ["clean", "truncate", "tailer"])


if __name__ == "__main__":
    unittest.main()
