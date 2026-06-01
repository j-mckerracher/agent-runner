import subprocess
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch
import json

from eval import runner as eval_runner


class EvalRunnerModelOverrideTests(unittest.TestCase):
    def test_inherits_compatible_env_model_for_same_runner(self):
        model = eval_runner.resolve_model_override(
            "claude",
            None,
            {"EVAL_MODEL": "claude-sonnet-4-6"},
        )

        self.assertEqual(model, "claude-sonnet-4-6")

    def test_drops_incompatible_env_model_when_runner_changes(self):
        model = eval_runner.resolve_model_override(
            "copilot",
            None,
            {"EVAL_MODEL": "claude-sonnet-4-6"},
        )

        self.assertIsNone(model)

    def test_copilot_alias_uses_copilot_model_choices(self):
        inherited = eval_runner.RUNNER_MODEL_CHOICES["copilot"][0]
        with patch.dict("os.environ", {}, clear=True):
            model = eval_runner.resolve_model_override(
                "copilot-enterprise",
                None,
                {"EVAL_MODEL": inherited},
            )

        self.assertEqual(model, inherited)

    def test_codex_inherits_arbitrary_env_model(self):
        model = eval_runner.resolve_model_override(
            "codex",
            None,
            {"EVAL_MODEL": "future-codex-model"},
        )

        self.assertEqual(model, "future-codex-model")

    def test_explicit_model_is_preserved_for_downstream_validation(self):
        model = eval_runner.resolve_model_override(
            "copilot",
            "claude-sonnet-4-6",
            {"EVAL_MODEL": "gpt-5-mini"},
        )

        self.assertEqual(model, "claude-sonnet-4-6")

    def test_build_workflow_command_omits_model_when_no_override_is_resolved(self):
        args = Namespace(
            runner="copilot",
            model=None,
            log_level="warning",
            include_lessons=False,
            workflow_timeout=900,
        )
        command = eval_runner.build_workflow_command(Path("/tmp/bench"), Path("/tmp/workspace"), args)
        self.assertNotIn("--model", command)


class EvalRunnerProgressTests(unittest.TestCase):
    def test_workflow_env_adds_event_log_when_requested(self):
        env = eval_runner.workflow_env(Path("/tmp/events.jsonl"))

        self.assertEqual(env["AGENT_RUNNER_EVENT_LOG"], "/tmp/events.jsonl")
        self.assertEqual(env["AGENT_RUNNER_HEADLESS"], "1")
        self.assertEqual(env["AGENT_RUNNER_EVALUATION_RUN"], "1")

    def test_progress_tracks_stage_completion_without_lessons(self):
        progress = eval_runner.WorkflowProgress(stages=eval_runner.workflow_stage_names(include_lessons=False))

        changed = eval_runner.apply_workflow_event(progress, {"seq": 1, "type": "stage.start", "stage": "materialize"})
        self.assertTrue(changed)
        self.assertEqual(progress.percent(), 0)
        self.assertEqual(progress.render(), "Workflow progress: 0% — materialize (stage 1/6)")

        changed = eval_runner.apply_workflow_event(progress, {"seq": 2, "type": "stage.end", "stage": "materialize", "status": "ok"})
        self.assertTrue(changed)
        self.assertEqual(progress.percent(), 16)

        eval_runner.apply_workflow_event(progress, {"seq": 3, "type": "stage.start", "stage": "intake"})
        self.assertEqual(progress.render(), "Workflow progress: 16% — intake (stage 2/6)")

    def test_progress_tracks_execution_uow_completion(self):
        progress = eval_runner.WorkflowProgress(stages=eval_runner.workflow_stage_names(include_lessons=False))
        for seq, stage in enumerate(("materialize", "intake", "task-generation", "task-assignment"), start=1):
            eval_runner.apply_workflow_event(progress, {"seq": seq * 2 - 1, "type": "stage.start", "stage": stage})
            eval_runner.apply_workflow_event(progress, {"seq": seq * 2, "type": "stage.end", "stage": stage, "status": "ok"})

        eval_runner.apply_workflow_event(progress, {"seq": 9, "type": "stage.start", "stage": "execution"})
        eval_runner.apply_workflow_event(progress, {"seq": 10, "type": "workflow.plan", "total_uows": 4})
        self.assertEqual(progress.percent(), 66)
        self.assertEqual(progress.render(), "Workflow progress: 66% — execution (0/4 UoWs complete)")

        eval_runner.apply_workflow_event(progress, {"seq": 11, "type": "uow.end", "uow_id": "UOW-1", "status": "ok"})
        self.assertEqual(progress.percent(), 70)

        eval_runner.apply_workflow_event(progress, {"seq": 12, "type": "uow.end", "uow_id": "UOW-2", "status": "ok"})
        self.assertEqual(progress.percent(), 75)

        eval_runner.apply_workflow_event(progress, {"seq": 13, "type": "uow.end", "uow_id": "UOW-2", "status": "ok"})
        self.assertEqual(progress.percent(), 75)


class EvalRunnerStructuredResultTests(unittest.TestCase):
    def test_hydrate_local_dependency_tree_links_node_modules(self):
        with tempfile.TemporaryDirectory(prefix="eval-source-") as source_dir, tempfile.TemporaryDirectory(prefix="eval-workspace-") as workspace_dir:
            source = Path(source_dir)
            workspace = Path(workspace_dir)
            (source / "node_modules" / ".bin").mkdir(parents=True)
            (source / "node_modules" / ".bin" / "tsx").write_text("#!/bin/sh\n", encoding="utf-8")

            eval_runner.hydrate_local_dependency_tree(str(source), workspace)

            self.assertTrue((workspace / "node_modules").is_symlink())
            self.assertTrue((workspace / "node_modules" / ".bin" / "tsx").exists())

    def test_hydrate_local_dependency_tree_ignores_remote_repo(self):
        with tempfile.TemporaryDirectory(prefix="eval-workspace-") as workspace_dir:
            workspace = Path(workspace_dir)

            eval_runner.hydrate_local_dependency_tree("https://example.com/repo.git", workspace)

            self.assertFalse((workspace / "node_modules").exists())

    def test_gold_master_angular_jit_output_is_setup_failure(self):
        completed = subprocess.CompletedProcess(
            ["pytest"],
            1,
            stdout="",
            stderr=(
                "The injectable 'PlatformLocation' needs to be compiled using the JIT compiler, "
                "but '@angular/compiler' is not available."
            ),
        )
        summary = eval_runner.TestSummary()

        reason = eval_runner.hidden_test_setup_failure_reason(completed, summary)

        self.assertEqual(reason, "angular jit compiler unavailable")

    def test_gold_master_missing_tsx_junit_message_is_setup_failure(self):
        completed = subprocess.CompletedProcess(["pytest"], 1, stdout="", stderr="")
        summary = eval_runner.TestSummary(
            total=1,
            failed=1,
            cases=[
                {
                    "classname": "hidden_tests",
                    "name": "test_ac1_behavior",
                    "status": "failed",
                    "message": "Missing local TypeScript runner at node_modules/.bin/tsx. Install dependencies.",
                }
            ],
        )

        reason = eval_runner.hidden_test_setup_failure_reason(completed, summary)

        self.assertEqual(reason, "missing local TypeScript runner")

    def test_gold_master_behavioral_assertion_failure_is_not_setup_failure(self):
        completed = subprocess.CompletedProcess(
            ["pytest"],
            1,
            stdout="assert classification.category == ErrorCategory.AUTHENTICATION",
            stderr="",
        )
        summary = eval_runner.TestSummary(
            total=1,
            failed=1,
            cases=[
                {
                    "classname": "hidden_tests",
                    "name": "test_ac1_behavior",
                    "status": "failed",
                    "message": "expected a dedicated auth/access category, not api",
                }
            ],
        )

        reason = eval_runner.hidden_test_setup_failure_reason(completed, summary)

        self.assertIsNone(reason)

    def test_gold_master_echoed_hidden_test_source_is_not_setup_failure(self):
        completed = subprocess.CompletedProcess(
            ["pytest"],
            1,
            stdout=(
                "E       AssertionError: TypeScript behavioral assertions failed.\n"
                "E       >       assert tsx.exists(), (\n"
                "E                   \"Missing local TypeScript runner at node_modules/.bin/tsx. \"\n"
            ),
            stderr="",
        )
        summary = eval_runner.TestSummary(
            total=1,
            failed=1,
            cases=[
                {
                    "classname": "hidden_tests",
                    "name": "test_ac1_behavior",
                    "status": "failed",
                    "message": "TypeScript behavioral assertions failed.",
                }
            ],
        )

        reason = eval_runner.hidden_test_setup_failure_reason(completed, summary)

        self.assertIsNone(reason)

    def test_gold_master_behavioral_failure_allows_workflow_to_continue(self):
        with tempfile.TemporaryDirectory(prefix="eval-runner-bench-") as tmpdir:
            bench = Path(tmpdir)
            (bench / "story.json").write_text(
                json.dumps(
                    {
                        "change_id": "TEST-1",
                        "title": "Test benchmark",
                        "description": "Exercise runner flow.",
                        "acceptance_criteria": ["AC1: Passes"],
                    }
                ),
                encoding="utf-8",
            )
            (bench / "hidden_tests.py").write_text(
                'AC_TEST_MAP = {"AC1": ["test_ac1_pass"]}\n\ndef test_ac1_pass():\n    assert True\n',
                encoding="utf-8",
            )
            args = Namespace(
                keep_sandbox=False,
                repo="/repo",
                sha="sha",
                test_timeout=30,
                no_verify_gold_fails=False,
                allow_hidden_skips=False,
                project_test_command=None,
            )
            gold_completed = subprocess.CompletedProcess(["pytest"], 1, stdout="assert auth category", stderr="")
            gold_summary = eval_runner.TestSummary(
                total=1,
                failed=1,
                cases=[{"classname": "hidden_tests", "name": "test_ac1_pass", "status": "failed", "message": "wrong category"}],
            )
            hidden_completed = subprocess.CompletedProcess(["pytest"], 0, stdout="", stderr="")
            hidden_summary = eval_runner.TestSummary(
                total=1,
                passed=1,
                cases=[{"classname": "hidden_tests", "name": "test_ac1_pass", "status": "passed", "message": ""}],
            )

            with (
                patch.object(eval_runner, "prepare_workspace"),
                patch.object(eval_runner, "run_hidden_tests", side_effect=[(gold_completed, gold_summary), (hidden_completed, hidden_summary)]),
                patch.object(eval_runner, "invoke_workflow", return_value=subprocess.CompletedProcess(["run.py"], 0, stdout="", stderr="")) as workflow,
                patch.object(eval_runner, "collect_session_metrics", return_value={}),
            ):
                result = eval_runner.run_one(bench, args)

        self.assertEqual(result["status"], "PASS")
        workflow.assert_called_once()

    def test_gold_master_setup_failure_stops_before_workflow(self):
        with tempfile.TemporaryDirectory(prefix="eval-runner-bench-") as tmpdir:
            bench = Path(tmpdir)
            (bench / "story.json").write_text(
                json.dumps(
                    {
                        "change_id": "TEST-1",
                        "title": "Test benchmark",
                        "description": "Exercise runner flow.",
                        "acceptance_criteria": ["AC1: Passes"],
                    }
                ),
                encoding="utf-8",
            )
            (bench / "hidden_tests.py").write_text(
                'AC_TEST_MAP = {"AC1": ["test_ac1_pass"]}\n\ndef test_ac1_pass():\n    assert True\n',
                encoding="utf-8",
            )
            args = Namespace(
                keep_sandbox=False,
                repo="/repo",
                sha="sha",
                test_timeout=30,
                no_verify_gold_fails=False,
                allow_hidden_skips=False,
                project_test_command=None,
            )
            gold_completed = subprocess.CompletedProcess(["pytest"], 1, stdout="", stderr="")
            gold_summary = eval_runner.TestSummary(
                total=1,
                failed=1,
                cases=[
                    {
                        "classname": "hidden_tests",
                        "name": "test_ac1_pass",
                        "status": "failed",
                        "message": "Missing local TypeScript runner at node_modules/.bin/tsx.",
                    }
                ],
            )

            with (
                patch.object(eval_runner, "prepare_workspace"),
                patch.object(eval_runner, "run_hidden_tests", return_value=(gold_completed, gold_summary)),
                patch.object(eval_runner, "invoke_workflow") as workflow,
                patch.object(eval_runner, "collect_session_metrics", return_value={}),
            ):
                result = eval_runner.run_one(bench, args)

        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["error"], "hidden tests errored on gold master: missing local TypeScript runner")
        workflow.assert_not_called()

    def test_parse_junit_maps_ac_results_and_skips(self):
        with tempfile.TemporaryDirectory(prefix="eval-runner-junit-") as tmpdir:
            junit = Path(tmpdir) / "hidden.xml"
            junit.write_text(
                """
                <testsuites>
                  <testsuite tests="3" failures="1" errors="0" skipped="1">
                    <testcase classname="hidden_tests" name="test_ac1_pass" time="0.1" />
                    <testcase classname="hidden_tests" name="test_ac2_fail" time="0.1"><failure message="bad" /></testcase>
                    <testcase classname="hidden_tests" name="test_ac3_skip" time="0.1"><skipped message="missing" /></testcase>
                  </testsuite>
                </testsuites>
                """.strip(),
                encoding="utf-8",
            )

            summary = eval_runner.parse_junit(junit)
            ac_results = eval_runner.map_ac_results(
                summary,
                {
                    "AC1": ["test_ac1_pass"],
                    "AC2": ["test_ac2_fail"],
                    "AC3": ["test_ac3_skip"],
                },
                required_ids=["AC1", "AC2", "AC3"],
            )

        self.assertEqual(summary.total, 3)
        self.assertEqual(summary.skipped, 1)
        self.assertTrue(ac_results["AC1"]["passed"])
        self.assertTrue(ac_results["AC2"]["failed"])
        self.assertTrue(ac_results["AC3"]["skipped"])

    def test_validate_official_benchmarks_enforces_ac_maps(self):
        for name in ("easy", "medium", "hard"):
            with self.subTest(name=name):
                eval_runner.validate_benchmark(eval_runner.DEFAULT_BENCHMARKS / name)

    def test_medium_benchmark_contract_is_runtime_oriented(self):
        medium = eval_runner.DEFAULT_BENCHMARKS / "medium"
        hidden_tests = (medium / "hidden_tests.py").read_text(encoding="utf-8")
        ac_map = eval_runner.benchmark_ac_test_map(medium)

        self.assertEqual(set(ac_map), {"AC1", "AC2", "AC3", "AC4"})
        self.assertIn("subprocess.run", hidden_tests)
        self.assertIn("node", hidden_tests)
        self.assertNotIn("pytest.skip", hidden_tests)
        self.assertNotIn("pytest.xfail", hidden_tests)
        self.assertNotIn("test_ac3_no_bad_a_z_ranges_remain_in_target_validators", hidden_tests)

    def test_aggregate_results_reports_quality_reliability_and_efficiency(self):
        results = [
            {
                "name": "easy",
                "status": "PASS",
                "quality": {"weighted_score": 1.0, "ac_passed": 3, "ac_total": 3, "critical_ac_failed": 0, "hidden_tests_skipped": 0},
                "metrics": {"wall_seconds": 10.0, "tokens_total": 100},
            },
            {
                "name": "easy",
                "status": "FAIL",
                "error": "hidden tests failed",
                "quality": {"weighted_score": 0.5, "ac_passed": 1, "ac_total": 2, "critical_ac_failed": 1, "hidden_tests_skipped": 0},
                "metrics": {"wall_seconds": 20.0, "tokens_total": 200},
            },
        ]

        summary = eval_runner.aggregate_results(results)

        self.assertEqual(summary["reliability"]["runs"], 2)
        self.assertEqual(summary["reliability"]["pass_rate"], 0.5)
        self.assertEqual(summary["quality"]["weighted_score"], 0.75)
        self.assertEqual(summary["efficiency"]["wall_seconds_mean"], 15.0)

    def test_classify_trend_detects_quality_regression(self):
        trend = eval_runner.classify_trend(
            {"quality": {"weighted_score": 0.7}, "efficiency": {"wall_seconds_mean": 10, "tokens_total_mean": 100}},
            {"quality": {"weighted_score": 0.9}, "efficiency": {"wall_seconds_mean": 10, "tokens_total_mean": 100}},
        )

        self.assertEqual(trend, "decreased")

    def test_write_report_warns_when_baseline_is_not_comparable(self):
        with tempfile.TemporaryDirectory(prefix="eval-runner-report-") as tmpdir:
            report_root = Path(tmpdir) / "reports"
            baseline = Path(tmpdir) / "baseline.json"
            baseline.write_text(
                json.dumps(
                    {
                        "sha": "old-sha",
                        "runner": "copilot",
                        "model": "gpt-5-mini",
                        "summary": {
                            "quality": {"weighted_score": 1.0},
                            "efficiency": {"wall_seconds_mean": 10, "tokens_total_mean": 100},
                        },
                        "results": [{"name": "easy"}],
                    }
                ),
                encoding="utf-8",
            )
            args = Namespace(
                write_report=True,
                compare_to=baseline,
                update_baseline=False,
                regression_quality_pp=5.0,
                regression_efficiency_pct=15.0,
                repo="/repo",
                sha="new-sha",
                runner="claude",
                model="claude-sonnet-4-6",
                runs=1,
            )
            results = [
                {
                    "name": "medium",
                    "status": "PASS",
                    "quality": {"weighted_score": 1.0, "ac_passed": 1, "ac_total": 1, "critical_ac_failed": 0, "hidden_tests_skipped": 0},
                    "metrics": {"wall_seconds": 10.0, "tokens_total": 100},
                }
            ]

            with patch.object(eval_runner, "DEFAULT_REPORTS", report_root):
                eval_runner.write_report(results, args)
                payload = json.loads((report_root / "latest.json").read_text(encoding="utf-8"))

        self.assertIn("Current runner/model differs from baseline.", payload["summary"]["warnings"])
        self.assertIn("Current target SHA differs from baseline.", payload["summary"]["warnings"])
        self.assertIn("Current benchmark set differs from baseline.", payload["summary"]["warnings"])


if __name__ == "__main__":
    unittest.main()
