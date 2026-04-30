import unittest

from server.runner_proc import _format_failure_summary


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


if __name__ == "__main__":
    unittest.main()
