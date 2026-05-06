import unittest

from opik_integration import _sdk_trace_metadata


class OpikIntegrationTests(unittest.TestCase):
    def test_sdk_trace_metadata_ignores_runner_specific_extra_kwargs(self):
        metadata = _sdk_trace_metadata(
            "Read story context from agent-context/CHG-123/intake/story.yaml.",
            "task-plan-evaluator",
            model="gpt-5-mini",
            runner="copilot",
            copilot_effort="high",
        )

        self.assertEqual(
            metadata,
            {
                "agent": "task-plan-evaluator",
                "change_id": "CHG-123",
                "runner": "copilot",
                "model": "gpt-5-mini",
            },
        )


if __name__ == "__main__":
    unittest.main()
