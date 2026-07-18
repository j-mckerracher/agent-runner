import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml


class EvaluatorOptimizerLoopTelemetryTests(unittest.TestCase):
    def test_easy__eval_optimizer_loop_omitted_runner_model_propagates_none(self):
        from core.evaluator_optimizer_loops import run_eval_optimizer_loop

        producer_calls = []
        evaluator_calls = []

        def producer(prompt: str, **kwargs) -> str:
            producer_calls.append((prompt, kwargs))
            return f"produced:{prompt}"

        def evaluator(prompt: str, **kwargs) -> str:
            evaluator_calls.append((prompt, kwargs))
            return "PASS"

        output, evaluation = run_eval_optimizer_loop(
            producer,
            "agent-context/LOOP-2/planning/task.yaml",
            evaluator,
            "agent-context/LOOP-2/qa/eval.yaml",
            iter_count=1,
            runner="claude",
        )

        self.assertEqual(output, "produced:agent-context/LOOP-2/planning/task.yaml")
        self.assertEqual(evaluation, "PASS")
        self.assertIsNone(producer_calls[0][1]["runner_model"])
        self.assertIsNone(evaluator_calls[0][1]["runner_model"])

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

    def test_medium__eval_optimizer_loop_retries_when_producer_artifact_missing(self):
        from core.evaluator_optimizer_loops import run_eval_optimizer_loop

        with tempfile.TemporaryDirectory() as tmpdir:
            artifact = Path(tmpdir) / "planning" / "tasks.yaml"
            producer_calls = []
            evaluator_calls = []

            def producer(prompt, **_kwargs):
                producer_calls.append(prompt)
                if len(producer_calls) >= 2:
                    artifact.parent.mkdir(parents=True, exist_ok=True)
                    artifact.write_text("tasks: []\n", encoding="utf-8")
                return "produced"

            def evaluator(_prompt, **_kwargs):
                evaluator_calls.append(True)
                return "PASS"

            output, evaluation = run_eval_optimizer_loop(
                producer,
                "agent-context/LOOP-1/planning/task.yaml",
                evaluator,
                "agent-context/LOOP-1/planning/eval.yaml",
                iter_count=3,
                runner="claude",
                runner_model="claude-sonnet",
                producer_artifact=artifact,
            )

            # iter 1: artifact missing -> evaluator skipped, deterministic retry.
            # iter 2: artifact written -> evaluator runs and passes.
            self.assertEqual(len(producer_calls), 2)
            self.assertEqual(len(evaluator_calls), 1)
            self.assertEqual(evaluation, "PASS")
            # The retry prompt carried the corrective missing-artifact feedback.
            self.assertIn("was not produced", producer_calls[1])

    def test_medium__eval_optimizer_loop_without_producer_artifact_runs_evaluator(self):
        from core.evaluator_optimizer_loops import run_eval_optimizer_loop

        evaluator_calls = []

        def producer(prompt, **_kwargs):
            return "produced"

        def evaluator(_prompt, **_kwargs):
            evaluator_calls.append(True)
            return "PASS"

        _output, evaluation = run_eval_optimizer_loop(
            producer,
            "agent-context/LOOP-1/planning/task.yaml",
            evaluator,
            "agent-context/LOOP-1/planning/eval.yaml",
            iter_count=3,
            runner="claude",
            runner_model="claude-sonnet",
        )

        # producer_artifact defaults to None -> guard inactive, evaluator runs.
        self.assertEqual(len(evaluator_calls), 1)
        self.assertEqual(evaluation, "PASS")


        from core.evaluator_optimizer_loops import run_uow_eval_loop

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "agent-context"
            uow_dir = root / "CHANGE-1" / "execution" / "UOW-001"
            uow_dir.mkdir(parents=True)
            (uow_dir / "uow_spec.yaml").write_text(
                yaml.safe_dump(
                    {
                        "uow_id": "UOW-001",
                        "title": "Implement alpha ordering helper",
                        "implementation_hints": ["libs/orders/alpha-order/alpha-order-helper.ts"],
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            evaluator_calls = []

            def software_engineer(**_kwargs) -> str:
                (uow_dir / "impl_report.yaml").write_text(
                    "\n".join(
                        [
                            'change_id: "CHANGE-1"',
                            'uow_id: "UOW-001"',
                            'status: "complete"',
                            "implementation_summary: Alpha order preserved: helper verified",
                            "definition_of_done_status:",
                            "  - item: Output format preserved: status remains visible",
                            "    met: true",
                            "    evidence: alpha order helper verified",
                        ]
                    ),
                    encoding="utf-8",
                )
                return "implemented"

            def evaluator(**_kwargs) -> str:
                evaluator_calls.append(True)
                return "PASS"

            with patch("core.evaluator_optimizer_loops.steps.AGENT_CONTEXT_ROOT", root), patch(
                "core.evaluator_optimizer_loops.steps.step_software_engineer", side_effect=software_engineer
            ), patch("core.evaluator_optimizer_loops.steps.step_software_engineer_evaluator", side_effect=evaluator):
                producer_out, evaluator_out = run_uow_eval_loop(
                    "UOW-001",
                    "CHANGE-1",
                    repo="/tmp/repo",
                    iter_count=1,
                    runner="claude",
                    runner_model="claude-sonnet",
                )

            self.assertEqual(producer_out, "implemented")
            self.assertEqual(evaluator_out, "PASS")
            self.assertEqual(evaluator_calls, [True])
            normalized = yaml.safe_load((uow_dir / "impl_report.yaml").read_text(encoding="utf-8"))
            self.assertEqual(normalized["implementation_summary"], "Alpha order preserved: helper verified")
            snapshot = uow_dir / "attempts" / "attempt-001" / "impl_report.yaml"
            self.assertTrue(snapshot.is_file())
            self.assertEqual(
                yaml.safe_load(snapshot.read_text(encoding="utf-8"))["definition_of_done_status"][0]["item"],
                "Output format preserved: status remains visible",
            )
            eval_path = uow_dir / "eval_impl_1.json"
            self.assertTrue(eval_path.is_file())
            self.assertEqual(json.loads(eval_path.read_text(encoding="utf-8"))["raw_response"], "PASS")

    def test_medium__uow_eval_loop_persists_feedback_and_passes_path_to_next_iteration(self):
        from core.evaluator_optimizer_loops import run_uow_eval_loop

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "agent-context"
            uow_dir = root / "CHANGE-1" / "execution" / "UOW-001"
            uow_dir.mkdir(parents=True)
            (uow_dir / "uow_spec.yaml").write_text(
                yaml.safe_dump(
                    {
                        "uow_id": "UOW-001",
                        "title": "Implement alpha ordering helper",
                        "implementation_hints": ["libs/orders/alpha-order/alpha-order-helper.ts"],
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            software_engineer_calls = []
            evaluator_outputs = [
                json.dumps(
                    {
                        "artifact_evaluated": "impl_report.yaml",
                        "status": "FAIL",
                        "issues": [{"description": "Missing alpha verification"}],
                    }
                ),
                "PASS",
            ]

            def software_engineer(**kwargs) -> str:
                software_engineer_calls.append(kwargs)
                (uow_dir / "impl_report.yaml").write_text(
                    yaml.safe_dump(
                        {
                            "change_id": "CHANGE-1",
                            "uow_id": "UOW-001",
                            "status": "complete",
                            "implementation_summary": "Alpha order helper verified",
                            "definition_of_done_status": [
                                {
                                    "item": "Alpha order preserved",
                                    "met": True,
                                    "evidence": "alpha order helper verified",
                                }
                            ],
                        },
                        sort_keys=False,
                    ),
                    encoding="utf-8",
                )
                return "implemented"

            def evaluator(**_kwargs) -> str:
                return evaluator_outputs.pop(0)

            with patch("core.evaluator_optimizer_loops.steps.AGENT_CONTEXT_ROOT", root), patch(
                "core.evaluator_optimizer_loops.steps.step_software_engineer", side_effect=software_engineer
            ), patch("core.evaluator_optimizer_loops.steps.step_software_engineer_evaluator", side_effect=evaluator):
                run_uow_eval_loop(
                    "UOW-001",
                    "CHANGE-1",
                    repo="/tmp/repo",
                    iter_count=2,
                    runner="claude",
                    runner_model="claude-sonnet",
                )

            eval_path = uow_dir / "eval_impl_1.json"
            self.assertTrue(eval_path.is_file())
            self.assertEqual(json.loads(eval_path.read_text(encoding="utf-8"))["status"], "FAIL")
            self.assertEqual(software_engineer_calls[0]["evaluator_feedback"], "")
            self.assertEqual(software_engineer_calls[0]["evaluator_feedback_path"], "")
            self.assertIn("Missing alpha verification", software_engineer_calls[1]["evaluator_feedback"])
            self.assertEqual(software_engineer_calls[1]["evaluator_feedback_path"], str(eval_path))

    def test_medium__uow_eval_loop_retries_when_impl_report_is_missing(self):
        from core.evaluator_optimizer_loops import run_uow_eval_loop

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "agent-context"
            uow_dir = root / "CHANGE-1" / "execution" / "UOW-001"
            uow_dir.mkdir(parents=True)
            (uow_dir / "uow_spec.yaml").write_text(
                yaml.safe_dump(
                    {
                        "uow_id": "UOW-001",
                        "title": "Implement alpha ordering helper",
                        "implementation_hints": ["libs/orders/alpha-order/alpha-order-helper.ts"],
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            software_engineer_calls = []
            evaluator_calls = []

            def software_engineer(**kwargs) -> str:
                software_engineer_calls.append(kwargs)
                if len(software_engineer_calls) == 2:
                    (uow_dir / "impl_report.yaml").write_text(
                        yaml.safe_dump(
                            {
                                "change_id": "CHANGE-1",
                                "uow_id": "UOW-001",
                                "status": "complete",
                                "implementation_summary": "Alpha order helper verified",
                                "definition_of_done_status": [
                                    {"item": "Alpha order preserved", "met": True, "evidence": "alpha verified"}
                                ],
                            },
                            sort_keys=False,
                        ),
                        encoding="utf-8",
                    )
                return "implemented"

            def evaluator(**_kwargs) -> str:
                evaluator_calls.append(True)
                return "PASS"

            with patch("core.evaluator_optimizer_loops.steps.AGENT_CONTEXT_ROOT", root), patch(
                "core.evaluator_optimizer_loops.steps.step_software_engineer", side_effect=software_engineer
            ), patch("core.evaluator_optimizer_loops.steps.step_software_engineer_evaluator", side_effect=evaluator):
                producer_out, evaluator_out = run_uow_eval_loop(
                    "UOW-001",
                    "CHANGE-1",
                    repo="/tmp/repo",
                    iter_count=2,
                    runner="claude",
                    runner_model="claude-sonnet",
                )

            self.assertEqual(producer_out, "implemented")
            self.assertEqual(evaluator_out, "PASS")
            self.assertEqual(len(software_engineer_calls), 2)
            self.assertEqual(evaluator_calls, [True])
            eval_path = uow_dir / "eval_impl_1.json"
            payload = json.loads(eval_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "FAIL")
            self.assertIn("implementation report", payload["summary"])
            self.assertIn("Write the required implementation report", software_engineer_calls[1]["evaluator_feedback"])
            self.assertEqual(software_engineer_calls[1]["evaluator_feedback_path"], str(eval_path))

    def test_medium__uow_eval_loop_raises_after_repeated_missing_impl_report(self):
        from core.artifact_utils import ImplReportValidationError
        from core.evaluator_optimizer_loops import run_uow_eval_loop

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "agent-context"
            uow_dir = root / "CHANGE-1" / "execution" / "UOW-001"
            uow_dir.mkdir(parents=True)
            (uow_dir / "uow_spec.yaml").write_text(
                yaml.safe_dump(
                    {
                        "uow_id": "UOW-001",
                        "title": "Implement alpha ordering helper",
                        "implementation_hints": ["libs/orders/alpha-order/alpha-order-helper.ts"],
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            software_engineer_calls = []
            evaluator_calls = []

            def software_engineer(**kwargs) -> str:
                software_engineer_calls.append(kwargs)
                return "implemented"

            def evaluator(**_kwargs) -> str:
                evaluator_calls.append(True)
                return "PASS"

            with patch("core.evaluator_optimizer_loops.steps.AGENT_CONTEXT_ROOT", root), patch(
                "core.evaluator_optimizer_loops.steps.step_software_engineer", side_effect=software_engineer
            ), patch("core.evaluator_optimizer_loops.steps.step_software_engineer_evaluator", side_effect=evaluator):
                with self.assertRaisesRegex(ImplReportValidationError, "implementation report"):
                    run_uow_eval_loop(
                        "UOW-001",
                        "CHANGE-1",
                        repo="/tmp/repo",
                        iter_count=2,
                        runner="claude",
                        runner_model="claude-sonnet",
                    )

            self.assertEqual(len(software_engineer_calls), 2)
            self.assertEqual(evaluator_calls, [])
            for iteration in (1, 2):
                payload = json.loads((uow_dir / f"eval_impl_{iteration}.json").read_text(encoding="utf-8"))
                self.assertEqual(payload["status"], "FAIL")
                self.assertEqual(payload["artifact_evaluated"], "impl_report.yaml")

    def test_medium__uow_eval_loop_hard_fails_on_missing_uow_spec(self):
        # Missing/invalid uow_spec.yaml is upstream corruption (task-assigner
        # output), not a recoverable agent stumble — it must fail fast on the
        # first iteration without burning the retry budget.
        from core.artifact_utils import ImplReportValidationError
        from core.evaluator_optimizer_loops import run_uow_eval_loop

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "agent-context"
            uow_dir = root / "CHANGE-1" / "execution" / "UOW-001"
            uow_dir.mkdir(parents=True)
            # Note: no uow_spec.yaml written.
            software_engineer_calls = []
            evaluator_calls = []

            def software_engineer(**kwargs) -> str:
                software_engineer_calls.append(kwargs)
                return "implemented"

            def evaluator(**_kwargs) -> str:
                evaluator_calls.append(True)
                return "PASS"

            with patch("core.evaluator_optimizer_loops.steps.AGENT_CONTEXT_ROOT", root), patch(
                "core.evaluator_optimizer_loops.steps.step_software_engineer", side_effect=software_engineer
            ), patch("core.evaluator_optimizer_loops.steps.step_software_engineer_evaluator", side_effect=evaluator):
                with self.assertRaisesRegex(ImplReportValidationError, "UoW spec"):
                    run_uow_eval_loop(
                        "UOW-001",
                        "CHANGE-1",
                        repo="/tmp/repo",
                        iter_count=3,
                        runner="claude",
                        runner_model="claude-sonnet",
                    )

            # Fails fast: one attempt, no retries, evaluator never reached.
            self.assertEqual(len(software_engineer_calls), 1)
            self.assertEqual(evaluator_calls, [])

        from core.evaluator_optimizer_loops import run_uow_eval_loop

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "agent-context"
            uow_dir = root / "CHANGE-1" / "execution" / "UOW-001"
            uow_dir.mkdir(parents=True)
            (uow_dir / "uow_spec.yaml").write_text(
                yaml.safe_dump(
                    {
                        "uow_id": "UOW-001",
                        "title": "Implement alpha ordering helper",
                        "implementation_hints": ["libs/orders/alpha-order/alpha-order-helper.ts"],
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            software_engineer_calls = []
            evaluator_calls = []

            def software_engineer(**kwargs) -> str:
                software_engineer_calls.append(kwargs)
                if len(software_engineer_calls) == 1:
                    summary = "Implemented beta billing helper."
                    item = "Beta billing complete"
                    evidence = "beta billing helper verified"
                else:
                    summary = "Alpha order helper verified."
                    item = "Alpha order preserved"
                    evidence = "alpha order helper verified"
                (uow_dir / "impl_report.yaml").write_text(
                    yaml.safe_dump(
                        {
                            "change_id": "CHANGE-1",
                            "uow_id": "UOW-001",
                            "status": "complete",
                            "implementation_summary": summary,
                            "definition_of_done_status": [
                                {"item": item, "met": True, "evidence": evidence}
                            ],
                        },
                        sort_keys=False,
                    ),
                    encoding="utf-8",
                )
                return "implemented"

            def evaluator(**_kwargs) -> str:
                evaluator_calls.append(True)
                return "PASS"

            with patch("core.evaluator_optimizer_loops.steps.AGENT_CONTEXT_ROOT", root), patch(
                "core.evaluator_optimizer_loops.steps.step_software_engineer", side_effect=software_engineer
            ), patch("core.evaluator_optimizer_loops.steps.step_software_engineer_evaluator", side_effect=evaluator):
                run_uow_eval_loop(
                    "UOW-001",
                    "CHANGE-1",
                    repo="/tmp/repo",
                    iter_count=2,
                    runner="claude",
                    runner_model="claude-sonnet",
                )

            self.assertEqual(len(software_engineer_calls), 2)
            self.assertEqual(evaluator_calls, [True])
            self.assertIn("impl_report domain", software_engineer_calls[1]["evaluator_feedback"])
            self.assertTrue((uow_dir / "attempts" / "attempt-001" / "impl_report.yaml").is_file())

    def test_medium__uow_eval_loop_saves_fenced_json_feedback_as_object(self):
        from core.evaluator_optimizer_loops import run_uow_eval_loop

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "agent-context"
            uow_dir = root / "CHANGE-1" / "execution" / "UOW-001"
            uow_dir.mkdir(parents=True)
            (uow_dir / "uow_spec.yaml").write_text(
                yaml.safe_dump(
                    {
                        "uow_id": "UOW-001",
                        "title": "Implement alpha ordering helper",
                        "implementation_hints": ["libs/orders/alpha-order/alpha-order-helper.ts"],
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )

            def software_engineer(**_kwargs) -> str:
                (uow_dir / "impl_report.yaml").write_text(
                    yaml.safe_dump(
                        {
                            "change_id": "CHANGE-1",
                            "uow_id": "UOW-001",
                            "status": "complete",
                            "implementation_summary": "Alpha order helper verified",
                            "definition_of_done_status": [
                                {"item": "Alpha order preserved", "met": True, "evidence": "alpha verified"}
                            ],
                        },
                        sort_keys=False,
                    ),
                    encoding="utf-8",
                )
                return "implemented"

            def evaluator(**_kwargs) -> str:
                return '```json\n{"status":"PASS","summary":"alpha verified"}\n```'

            with patch("core.evaluator_optimizer_loops.steps.AGENT_CONTEXT_ROOT", root), patch(
                "core.evaluator_optimizer_loops.steps.step_software_engineer", side_effect=software_engineer
            ), patch("core.evaluator_optimizer_loops.steps.step_software_engineer_evaluator", side_effect=evaluator):
                run_uow_eval_loop(
                    "UOW-001",
                    "CHANGE-1",
                    repo="/tmp/repo",
                    iter_count=1,
                    runner="claude",
                    runner_model="claude-sonnet",
                )

            payload = json.loads((uow_dir / "eval_impl_1.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "PASS")
            self.assertEqual(payload["summary"], "alpha verified")
            self.assertEqual(payload["artifact_evaluated"], "impl_report.yaml")

    def test_easy__uow_eval_loop_omitted_runner_model_propagates_none(self):
        from core.evaluator_optimizer_loops import run_uow_eval_loop

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "agent-context"
            uow_dir = root / "CHANGE-2" / "execution" / "UOW-001"
            uow_dir.mkdir(parents=True)
            (uow_dir / "uow_spec.yaml").write_text(
                yaml.safe_dump(
                    {
                        "uow_id": "UOW-001",
                        "title": "Implement beta ordering helper",
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            software_engineer_calls = []
            evaluator_calls = []

            def software_engineer(**kwargs) -> str:
                software_engineer_calls.append(kwargs)
                (uow_dir / "impl_report.yaml").write_text(
                    yaml.safe_dump(
                        {
                            "change_id": "CHANGE-2",
                            "uow_id": "UOW-001",
                            "status": "complete",
                            "implementation_summary": "Beta helper verified",
                            "definition_of_done_status": [
                                {"item": "Beta order preserved", "met": True, "evidence": "beta verified"}
                            ],
                        },
                        sort_keys=False,
                    ),
                    encoding="utf-8",
                )
                return "implemented"

            def evaluator(**kwargs) -> str:
                evaluator_calls.append(kwargs)
                return "PASS"

            with patch("core.evaluator_optimizer_loops.steps.AGENT_CONTEXT_ROOT", root), patch(
                "core.evaluator_optimizer_loops.steps.step_software_engineer", side_effect=software_engineer
            ), patch("core.evaluator_optimizer_loops.steps.step_software_engineer_evaluator", side_effect=evaluator):
                run_uow_eval_loop(
                    "UOW-001",
                    "CHANGE-2",
                    repo="/tmp/repo",
                    iter_count=1,
                    runner="claude",
                )

        self.assertIsNone(software_engineer_calls[0]["runner_model"])
        self.assertIsNone(evaluator_calls[0]["runner_model"])


    def test_medium__eval_optimizer_loop_sanitizes_verbose_evaluator_feedback(self):
        from core.evaluator_optimizer_loops import run_eval_optimizer_loop

        producer_prompts = []

        def producer(prompt: str, **_kwargs) -> str:
            producer_prompts.append(prompt)
            return "produced"

        evaluator_outputs = [
            """
I'll evaluate the task plan.

<tool_call>
{"name": "write_file", "parameters": {"path": "eval_tasks_1.json", "content": "{\\"programmatic_gates\\": {"}}
</tool_call>
<tool_response>
partial write failed
</tool_response>

Full rubric analysis (working notes):
- AC3b is uncovered and must be mapped to the remove-button task.
- AC7b is covered in prose but missing from acceptance_criteria_coverage.

Now let me write the full evaluation:
{"name": "write_file", "parameters": {"content": "large partial json"}}
""",
            "PASS",
        ]

        def evaluator(_prompt: str, **_kwargs) -> str:
            return evaluator_outputs.pop(0)

        output, evaluation = run_eval_optimizer_loop(
            producer,
            "Generate task plan for change 5001029.",
            evaluator,
            "Evaluate task plan for change 5001029.",
            iter_count=2,
            runner="claude",
            runner_model="claude-sonnet",
        )

        self.assertEqual(output, "produced")
        self.assertEqual(evaluation, "PASS")
        retry_prompt = producer_prompts[1]
        self.assertIn("AC3b is uncovered", retry_prompt)
        self.assertIn("AC7b is covered", retry_prompt)
        self.assertNotIn("<tool_call>", retry_prompt)
        self.assertNotIn("<tool_response>", retry_prompt)
        self.assertNotIn("write_file", retry_prompt)
        self.assertNotIn("large partial json", retry_prompt)


class EvalOptimizerLoopExhaustionHookTests(unittest.TestCase):
    def test_easy__eval_optimizer_loop_calls_on_exhausted_once_after_terminal_failure(self):
        from core.evaluator_optimizer_loops import run_eval_optimizer_loop

        exhausted = []

        def producer(prompt: str, **_kwargs) -> str:
            return f"produced:{prompt}"

        def evaluator(_prompt: str, **_kwargs) -> str:
            return "FAIL"

        output, evaluation = run_eval_optimizer_loop(
            producer,
            "agent-context/LOOP-3/planning/task.yaml",
            evaluator,
            "agent-context/LOOP-3/qa/eval.yaml",
            iter_count=2,
            runner="claude",
            on_exhausted=exhausted.append,
        )

        self.assertTrue(output.startswith("produced:agent-context/LOOP-3/planning/task.yaml"))
        self.assertEqual(evaluation, "FAIL")
        self.assertEqual(exhausted, ["LOOP-3"])


if __name__ == "__main__":
    unittest.main()
