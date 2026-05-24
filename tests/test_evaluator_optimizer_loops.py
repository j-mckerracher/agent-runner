import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class EvaluatorOptimizerLoopTelemetryTests(unittest.TestCase):
    def test_medium__eval_optimizer_loop_emits_explicit_loop_events(self):
        from core.evaluator_optimizer_loops import run_eval_optimizer_loop
        from server import events
        from server.events import read_all

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as fh:
            path = fh.name
        events._default = None

        def producer(prompt: str, **_kwargs) -> str:
            return f"produced:{prompt}"

        def evaluator(_prompt: str, **_kwargs) -> str:
            return "PASS"

        try:
            with patch.dict(os.environ, {"AGENT_RUNNER_EVENT_LOG": path}, clear=False):
                output, evaluation = run_eval_optimizer_loop(
                    producer,
                    "agent-context/LOOP-1/planning/task.yaml",
                    evaluator,
                    "agent-context/LOOP-1/qa/eval.yaml",
                    iter_count=3,
                    runner="claude",
                    runner_model="claude-sonnet",
                )

            self.assertEqual(output, "produced:agent-context/LOOP-1/planning/task.yaml")
            self.assertEqual(evaluation, "PASS")
            loop_events = [event for event in read_all(path) if event["type"].startswith("loop.")]
            self.assertEqual([event["type"] for event in loop_events], [
                "loop.start",
                "loop.iteration.start",
                "loop.iteration.end",
                "loop.end",
            ])
            self.assertEqual(loop_events[0]["loop_name"], "eval-optimizer")
            self.assertEqual(loop_events[-1]["actual_iterations"], 1)
            self.assertTrue(loop_events[-1]["stopped_early"])
            self.assertFalse(loop_events[-1]["exhausted"])
        finally:
            events._default = None
            Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
