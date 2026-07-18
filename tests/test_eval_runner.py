import subprocess
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch
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
        benchmarks = {path.name: path for path in eval_runner.discover_benchmarks(eval_runner.DEFAULT_BENCHMARKS, [])}
        for name in ("easy", "medium", "hard"):
            with self.subTest(name=name):
                eval_runner.validate_benchmark(benchmarks[name])

    def test_medium_benchmark_contract_is_runtime_oriented(self):
        medium = {path.name: path for path in eval_runner.discover_benchmarks(eval_runner.DEFAULT_BENCHMARKS, [])}["medium"]
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
                reports_dir=report_root,
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

            eval_runner.write_report(results, args)
            payload = json.loads((report_root / "latest.json").read_text(encoding="utf-8"))

        warnings = payload["summary"]["comparison_context"]["warnings"]
        self.assertIn("Current runner/model differs from baseline.", warnings)
        self.assertIn("Current target SHA differs from baseline.", warnings)
        self.assertIn("Current benchmark set differs from baseline.", warnings)


class NormalizeWorkflowResultTests(unittest.TestCase):
    def test_normalizes_completed_process(self):
        cp = subprocess.CompletedProcess(args=["x"], returncode=0, stdout="out", stderr="err")
        normalized = eval_runner._normalize_workflow_result(cp)
        self.assertEqual(normalized, {"stdout": "out", "stderr": "err", "returncode": 0})

    def test_normalizes_dict(self):
        normalized = eval_runner._normalize_workflow_result({"stdout": "a", "stderr": "b", "returncode": 1})
        self.assertEqual(normalized, {"stdout": "a", "stderr": "b", "returncode": 1})

    def test_normalizes_arbitrary_object_via_getattr(self):
        class Fake:
            stdout = "obj-out"
            stderr = "obj-err"
            returncode = 2

        normalized = eval_runner._normalize_workflow_result(Fake())
        self.assertEqual(normalized, {"stdout": "obj-out", "stderr": "obj-err", "returncode": 2})

    def test_normalizes_none(self):
        normalized = eval_runner._normalize_workflow_result(None)
        self.assertEqual(normalized, {"stdout": "", "stderr": "", "returncode": None})


class EvidenceCaptureTests(unittest.TestCase):
    def test_capture_workflow_evidence_writes_logs_even_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence_dir = Path(tmp)
            evidence: dict = {}
            workflow = subprocess.CompletedProcess(args=["x"], returncode=1, stdout="boom-out", stderr="boom-err")
            eval_runner._capture_workflow_evidence(evidence_dir, workflow, evidence)
            self.assertEqual((evidence_dir / "workflow.stdout.log").read_text(encoding="utf-8"), "boom-out")
            self.assertEqual((evidence_dir / "workflow.stderr.log").read_text(encoding="utf-8"), "boom-err")
            self.assertIn("workflow_stdout_ref", evidence)
            self.assertIn("workflow_result_ref", evidence)

    def test_capture_workflow_evidence_noop_without_evidence_dir(self):
        evidence: dict = {}
        eval_runner._capture_workflow_evidence(None, subprocess.CompletedProcess([], 0, "", ""), evidence)
        self.assertEqual(evidence, {})

    def test_capture_hidden_test_evidence_tolerates_missing_junit_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence_dir = Path(tmp) / "evidence"
            evidence_dir.mkdir()
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            evidence: dict = {}
            eval_runner._capture_hidden_test_evidence(evidence_dir, workspace, evidence)
            self.assertNotIn("hidden_tests_xml_ref", evidence)

    def test_capture_hidden_test_evidence_copies_junit_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence_dir = Path(tmp) / "evidence"
            evidence_dir.mkdir()
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            (workspace / ".awb-hidden-tests.xml").write_text("<testsuite></testsuite>", encoding="utf-8")
            evidence: dict = {}
            eval_runner._capture_hidden_test_evidence(evidence_dir, workspace, evidence)
            self.assertTrue(Path(evidence["hidden_tests_xml_ref"]).exists())

    def test_capture_final_diff_tolerates_nonexistent_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence_dir = Path(tmp) / "evidence"
            evidence_dir.mkdir()
            evidence: dict = {}
            # workspace path does not exist; helper must not raise.
            eval_runner._capture_final_diff(evidence_dir, Path(tmp) / "nope", evidence)
            self.assertNotIn("diff_ref", evidence)


class RunOneEndToEndTests(unittest.TestCase):
    """End-to-end `run_one` under the standard patched seam (no real LLM/git
    clone/network): `invoke_workflow`/`run_hidden_tests`/`prepare_workspace`
    are the only pieces stubbed out, exercising the real evidence-capture and
    trace-emission wiring around them."""

    def _hidden_test_summary(self, *, all_pass: bool):
        from eval.result_schema import TestSummary

        ac_tests = {
            "test_ac1_renders_final_recap_fields": "AC1",
            "test_ac2_omits_verbose_and_sensitive_content_when_final_recap_exists": "AC2",
            "test_ac3_empty_final_recap_lists_render_clear_none_placeholder": "AC3",
            "test_ac4_preserves_legacy_session_summary_decisions_and_issues": "AC4",
        }
        cases = []
        for name, ac_id in ac_tests.items():
            failing = not all_pass and ac_id == "AC2"
            cases.append({"classname": "hidden_tests", "name": name, "time": 0.01, "status": "failed" if failing else "passed", "message": "boom" if failing else ""})
        failed = sum(1 for c in cases if c["status"] == "failed")
        return TestSummary(total=len(cases), passed=len(cases) - failed, failed=failed, skipped=0, errors=0, cases=cases)

    def _base_args(self, tmp: Path) -> Namespace:
        return Namespace(
            keep_sandbox=False,
            repo="/repo",
            sha="deadbeef",
            no_verify_gold_fails=True,
            allow_hidden_skips=False,
            test_timeout=60,
            project_test_command=None,
            workflow_timeout=900,
            include_lessons=False,
            reports_dir=tmp,
            eval_run_id="run-e2e-test",
        )

    def test_passing_run_populates_evidence_and_emits_terminal_run_completed(self):
        path = eval_runner.LEGACY_BENCHMARKS / "easy"
        with tempfile.TemporaryDirectory() as tmp:
            args = self._base_args(Path(tmp))
            with patch.object(eval_runner, "prepare_workspace", return_value=None), \
                 patch.object(eval_runner, "invoke_workflow", return_value=subprocess.CompletedProcess([], 0, "ok", "")), \
                 patch.object(eval_runner, "run_hidden_tests", return_value=(
                     subprocess.CompletedProcess([], 0, "", ""), self._hidden_test_summary(all_pass=True),
                 )):
                result = eval_runner.run_one(path, args, trial_index=1)

            self.assertEqual(result["status"], "PASS")
            evidence = result["evidence"]
            self.assertTrue(Path(evidence["dir"]).is_dir())
            self.assertTrue(Path(evidence["story_ref"]).exists())
            self.assertTrue(Path(evidence["workflow_stdout_ref"]).exists())
            trace_lines = Path(evidence["trace_ref"]).read_text(encoding="utf-8").strip().splitlines()
            self.assertGreaterEqual(len(trace_lines), 2)
            events = [json.loads(line) for line in trace_lines]
            self.assertEqual(events[0]["event_type"], "run.started")
            self.assertEqual(events[-1]["event_type"], "run.completed")

            # The trial slots straight into the v0.2 builder without further
            # massaging — proving `run_one`'s output shape matches what
            # `eval/live_report.py::build_eval_report` expects.
            report = eval_runner.build_eval_report(
                [result],
                Namespace(sha="deadbeef", runner="claude", model="unit-model", repo="/repo", difficulty="easy", compare_to=None),
                created_at="2026-05-22T12:00:00.000Z",
                eval_run_id="run-e2e-test",
            )
            self.assertEqual(eval_runner.validate_report_payload(report.to_dict()) if hasattr(eval_runner, "validate_report_payload") else [], [])

    def test_hidden_test_failure_leaves_unresolved_acs_unknown_not_pass(self):
        path = eval_runner.LEGACY_BENCHMARKS / "easy"
        with tempfile.TemporaryDirectory() as tmp:
            args = self._base_args(Path(tmp))
            with patch.object(eval_runner, "prepare_workspace", return_value=None), \
                 patch.object(eval_runner, "invoke_workflow", return_value=subprocess.CompletedProcess([], 0, "ok", "")), \
                 patch.object(eval_runner, "run_hidden_tests", return_value=(
                     subprocess.CompletedProcess([], 1, "", ""), self._hidden_test_summary(all_pass=False),
                 )):
                result = eval_runner.run_one(path, args, trial_index=1)

            self.assertEqual(result["status"], "FAIL")
            self.assertEqual(result["error"], "hidden tests failed")
            trace_lines = Path(result["evidence"]["trace_ref"]).read_text(encoding="utf-8").strip().splitlines()
            events = [json.loads(line) for line in trace_lines]
            self.assertEqual(events[-1]["event_type"], "run.failed")

            report = eval_runner.build_eval_report(
                [result],
                Namespace(sha="deadbeef", runner="claude", model="unit-model", repo="/repo", difficulty="easy", compare_to=None),
                created_at="2026-05-22T12:00:00.000Z",
                eval_run_id="run-e2e-test",
            )
            acs = {ac.ac_id: ac.status for ac in report.benchmark_case_results[0].acceptance_criteria_results}
            # AC2 positively failed; AC1/3/4 positively passed (real hidden-test
            # evidence exists for them) — none is inferred-pass from a workflow
            # that never ran, because it did run here.
            from eval.report_schema import AcStatus

            self.assertEqual(acs["AC2"], AcStatus.FAIL)
            self.assertIn(acs["AC1"], (AcStatus.PASS,))

    def test_workflow_failure_never_reaches_hidden_tests_leaves_all_acs_unknown(self):
        """Workflow itself fails (nonzero exit) -> hidden tests never invoked.
        Every AC must come back `unknown`, never `pass` — proving ACs are
        never inferred-pass from evidence that doesn't exist."""
        path = eval_runner.LEGACY_BENCHMARKS / "easy"
        with tempfile.TemporaryDirectory() as tmp:
            args = self._base_args(Path(tmp))
            hidden_tests_mock = MagicMock()
            with patch.object(eval_runner, "prepare_workspace", return_value=None), \
                 patch.object(eval_runner, "invoke_workflow", return_value=subprocess.CompletedProcess([], 1, "", "boom")), \
                 patch.object(eval_runner, "run_hidden_tests", hidden_tests_mock):
                result = eval_runner.run_one(path, args, trial_index=1)

            self.assertEqual(result["status"], "FAIL")
            hidden_tests_mock.assert_not_called()
            trace_lines = Path(result["evidence"]["trace_ref"]).read_text(encoding="utf-8").strip().splitlines()
            events = [json.loads(line) for line in trace_lines]
            self.assertEqual(events[-1]["event_type"], "run.failed")

            report = eval_runner.build_eval_report(
                [result],
                Namespace(sha="deadbeef", runner="claude", model="unit-model", repo="/repo", difficulty="easy", compare_to=None),
                created_at="2026-05-22T12:00:00.000Z",
                eval_run_id="run-e2e-test",
            )
            from eval.report_schema import AcStatus

            acs = {ac.ac_id: ac.status for ac in report.benchmark_case_results[0].acceptance_criteria_results}
            self.assertTrue(acs)
            for ac_id, status in acs.items():
                self.assertEqual(status, AcStatus.UNKNOWN, f"{ac_id} should be unknown, not inferred-pass")

    def test_two_trials_of_same_benchmark_group_into_one_case(self):
        """Two `run_one` calls (trial_index=1, 2) against the same benchmark
        must land in a single `BenchmarkCaseResult` with 2 `trial_results` —
        multi-trial grouping exercised against `run_one`'s real output shape,
        not a hand-built stand-in."""
        path = eval_runner.LEGACY_BENCHMARKS / "easy"
        with tempfile.TemporaryDirectory() as tmp:
            args = self._base_args(Path(tmp))
            results = []
            for trial_index in (1, 2):
                with patch.object(eval_runner, "prepare_workspace", return_value=None), \
                     patch.object(eval_runner, "invoke_workflow", return_value=subprocess.CompletedProcess([], 0, "ok", "")), \
                     patch.object(eval_runner, "run_hidden_tests", return_value=(
                         subprocess.CompletedProcess([], 0, "", ""), self._hidden_test_summary(all_pass=True),
                     )):
                    results.append(eval_runner.run_one(path, args, trial_index=trial_index))

            report = eval_runner.build_eval_report(
                results,
                Namespace(sha="deadbeef", runner="claude", model="unit-model", repo="/repo", difficulty="easy", compare_to=None),
                created_at="2026-05-22T12:00:00.000Z",
                eval_run_id="run-e2e-test",
            )
            self.assertEqual(len(report.benchmark_case_results), 1)
            self.assertEqual(len(report.benchmark_case_results[0].trial_results), 2)


if __name__ == "__main__":
    unittest.main()
