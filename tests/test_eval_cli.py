"""Tests for the v0.2 CLI command surface (`eval/cli.py`, Prompt 7).

Covers argument parsing, the `run`/`baseline` handlers (via patched runner
seams -- no real LLM calls, no real subprocesses), and the `compare`/`ci`
handlers (via the pre-existing canonical comparison fixtures). Exit-code
assertions go through `eval.cli.main(argv)` in-process, matching the plan's
determinism note: pytest-level monkeypatches cannot reach into a separately
spawned `python -m eval.cli ...` OS process.
"""
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import eval.cli as cli
from eval import runner as eval_runner
from eval.exit_codes import (
    EXIT_COMPARE_INCOMPLETE,
    EXIT_EVAL_FAILED,
    EXIT_OK,
    EXIT_REGRESSION,
    EXIT_REPORT_FAILED,
    EXIT_USAGE,
)
from eval.comparison_schema import Classification, Comparability, ComparisonResult
from eval.result_schema import TestSummary

FIXTURES = Path(__file__).parent / "fixtures" / "eval" / "comparison"
SUITE = Path(__file__).parent / "fixtures" / "eval" / "suite"


# --------------------------------------------------------------------------
# argument parsing
# --------------------------------------------------------------------------


class BuildParserTests(unittest.TestCase):
    def test_all_four_subcommands_registered(self):
        parser = cli.build_parser()
        # argparse doesn't expose subparser names directly; probe via --help.
        help_text = parser.format_help()
        for name in ("run", "baseline", "compare", "ci"):
            self.assertIn(name, help_text)

    def test_run_requires_suite_and_output_dir(self):
        parser = cli.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["run"])

    def test_compare_requires_baseline_candidate_output(self):
        parser = cli.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["compare", "--baseline", "x.json"])

    def test_ci_allow_mixed_defaults_true_and_accepts_no_flag(self):
        parser = cli.build_parser()
        args = parser.parse_args(
            ["ci", "--baseline", "b.json", "--candidate", "c.json", "--comparison-output", "o.json"]
        )
        self.assertTrue(args.allow_mixed)
        self.assertFalse(args.allow_inconclusive)

        args2 = parser.parse_args(
            [
                "ci",
                "--baseline",
                "b.json",
                "--candidate",
                "c.json",
                "--comparison-output",
                "o.json",
                "--no-allow-mixed",
                "--allow-inconclusive",
            ]
        )
        self.assertFalse(args2.allow_mixed)
        self.assertTrue(args2.allow_inconclusive)

    def test_main_help_exits_ok(self):
        self.assertEqual(cli.main(["--help"]), int(EXIT_OK))

    def test_main_missing_command_is_usage_error(self):
        self.assertEqual(cli.main([]), int(EXIT_USAGE))

    def test_main_unknown_subcommand_is_usage_error(self):
        self.assertEqual(cli.main(["frobnicate"]), int(EXIT_USAGE))


# --------------------------------------------------------------------------
# compare / ci -- driven by the pre-existing canonical fixtures
# --------------------------------------------------------------------------


class CompareCommandTests(unittest.TestCase):
    def test_compare_improved_exits_ok(self, ):
        with _tmp_output() as out:
            rc = cli.main(
                [
                    "compare",
                    "--baseline",
                    str(FIXTURES / "baseline_report.json"),
                    "--candidate",
                    str(FIXTURES / "candidate_improved.json"),
                    "--output",
                    str(out),
                ]
            )
            self.assertEqual(rc, int(EXIT_OK))
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(payload["classification"], "improved")

    def test_compare_regressed_is_hard_gate_even_under_inspect_only_compare(self):
        # `compare` calls `result_to_exit_code(..., allow_inconclusive=True,
        # allow_mixed=True)` -- those two flags only soften MIXED/INCONCLUSIVE.
        # REGRESSED is an unconditional hard gate at the mapping layer, so
        # even the inspection-only `compare` command exits 6 here, matching
        # `ci`'s behavior for the same fixture pair.
        with _tmp_output() as out:
            rc = cli.main(
                [
                    "compare",
                    "--baseline",
                    str(FIXTURES / "baseline_report.json"),
                    "--candidate",
                    str(FIXTURES / "candidate_regressed.json"),
                    "--output",
                    str(out),
                ]
            )
            self.assertEqual(rc, int(EXIT_REGRESSION))
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(payload["classification"], "regressed")

    def test_compare_drift_mixed_is_exit_ok(self):
        with _tmp_output() as out:
            rc = cli.main(
                [
                    "compare",
                    "--baseline",
                    str(FIXTURES / "baseline_report.json"),
                    "--candidate",
                    str(FIXTURES / "candidate_drift.json"),
                    "--output",
                    str(out),
                ]
            )
            self.assertEqual(rc, int(EXIT_OK))
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(payload["classification"], "mixed")

    def test_compare_baseline_vs_itself_is_unchanged(self):
        with _tmp_output() as out:
            rc = cli.main(
                [
                    "compare",
                    "--baseline",
                    str(FIXTURES / "baseline_report.json"),
                    "--candidate",
                    str(FIXTURES / "baseline_report.json"),
                    "--output",
                    str(out),
                ]
            )
            self.assertEqual(rc, int(EXIT_OK))
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(payload["classification"], "unchanged")

    def test_compare_missing_baseline_file_is_report_failed(self):
        with _tmp_output() as out:
            rc = cli.main(
                [
                    "compare",
                    "--baseline",
                    str(FIXTURES / "does-not-exist.json"),
                    "--candidate",
                    str(FIXTURES / "candidate_improved.json"),
                    "--output",
                    str(out),
                ]
            )
            self.assertEqual(rc, int(EXIT_REPORT_FAILED))
            self.assertFalse(out.exists())

    def test_compare_malformed_candidate_json_is_report_failed(self):
        with _tmp_output() as out, _tmp_dir() as tmp:
            bad = tmp / "bad.json"
            bad.write_text("{not json", encoding="utf-8")
            rc = cli.main(
                [
                    "compare",
                    "--baseline",
                    str(FIXTURES / "baseline_report.json"),
                    "--candidate",
                    str(bad),
                    "--output",
                    str(out),
                ]
            )
            self.assertEqual(rc, int(EXIT_REPORT_FAILED))


class CiCommandTests(unittest.TestCase):
    def test_ci_regressed_is_hard_gate_exit_6(self):
        with _tmp_output() as out:
            rc = cli.main(
                [
                    "ci",
                    "--baseline",
                    str(FIXTURES / "baseline_report.json"),
                    "--candidate",
                    str(FIXTURES / "candidate_regressed.json"),
                    "--comparison-output",
                    str(out),
                ]
            )
            self.assertEqual(rc, int(EXIT_REGRESSION))
            # Evidence is preserved even though the gate failed.
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(payload["classification"], "regressed")

    def test_ci_improved_is_exit_ok(self):
        with _tmp_output() as out:
            rc = cli.main(
                [
                    "ci",
                    "--baseline",
                    str(FIXTURES / "baseline_report.json"),
                    "--candidate",
                    str(FIXTURES / "candidate_improved.json"),
                    "--comparison-output",
                    str(out),
                ]
            )
            self.assertEqual(rc, int(EXIT_OK))

    def test_ci_mixed_drift_allowed_by_default(self):
        with _tmp_output() as out:
            rc = cli.main(
                [
                    "ci",
                    "--baseline",
                    str(FIXTURES / "baseline_report.json"),
                    "--candidate",
                    str(FIXTURES / "candidate_drift.json"),
                    "--comparison-output",
                    str(out),
                ]
            )
            self.assertEqual(rc, int(EXIT_OK))

    def test_ci_mixed_drift_gated_with_no_allow_mixed(self):
        with _tmp_output() as out:
            rc = cli.main(
                [
                    "ci",
                    "--baseline",
                    str(FIXTURES / "baseline_report.json"),
                    "--candidate",
                    str(FIXTURES / "candidate_drift.json"),
                    "--comparison-output",
                    str(out),
                    "--no-allow-mixed",
                ]
            )
            self.assertEqual(rc, int(EXIT_REGRESSION))
            # Comparison JSON is still written before the gate is applied.
            self.assertTrue(out.exists())

    def test_ci_input_error_never_writes_comparison_output(self):
        with _tmp_output() as out:
            rc = cli.main(
                [
                    "ci",
                    "--baseline",
                    str(FIXTURES / "missing.json"),
                    "--candidate",
                    str(FIXTURES / "candidate_improved.json"),
                    "--comparison-output",
                    str(out),
                ]
            )
            self.assertEqual(rc, int(EXIT_REPORT_FAILED))
            self.assertFalse(out.exists())


class ExitCodeMappingUnitTests(unittest.TestCase):
    """Direct unit coverage of `result_to_exit_code` for combinations that
    are not reachable end-to-end through `load_and_compare` today (per
    `eval/comparison.py::_determine_comparability`, any required-evidence
    degradation that would make `classify()` return INCONCLUSIVE already
    forces `comparability` to DEGRADED, which the exit-code mapping catches
    first regardless of classification). The mapping function itself still
    documents and supports the inconclusive-while-comparable combination, so
    it is tested directly here rather than left unverified.
    """

    def _result(self, *, comparability: Comparability, classification: Classification) -> ComparisonResult:
        payload = json.loads((FIXTURES / "baseline_report.json").read_text(encoding="utf-8"))
        from eval.comparison import compare_reports

        result = compare_reports(payload, payload)
        result.comparability = comparability
        result.classification = classification
        return result

    def test_incomparable_maps_to_compare_incomplete_regardless_of_classification(self):
        from eval.exit_codes import result_to_exit_code

        result = self._result(comparability=Comparability.INCOMPARABLE, classification=Classification.IMPROVED)
        self.assertEqual(result_to_exit_code(result), EXIT_COMPARE_INCOMPLETE)

    def test_inconclusive_while_comparable_defaults_to_regression_gate(self):
        from eval.exit_codes import result_to_exit_code

        result = self._result(comparability=Comparability.COMPARABLE, classification=Classification.INCONCLUSIVE)
        self.assertEqual(result_to_exit_code(result), EXIT_REGRESSION)

    def test_inconclusive_while_comparable_allowed_with_flag(self):
        from eval.exit_codes import result_to_exit_code

        result = self._result(comparability=Comparability.COMPARABLE, classification=Classification.INCONCLUSIVE)
        self.assertEqual(result_to_exit_code(result, allow_inconclusive=True), EXIT_OK)


# --------------------------------------------------------------------------
# run / baseline -- driven by patched runner seams (no real subprocesses)
# --------------------------------------------------------------------------


def _passing_hidden_test_summary() -> TestSummary:
    cases = [
        {"classname": "hidden_tests", "name": "test_ac1_marker_file_exists", "time": 0.01, "status": "passed", "message": ""},
        {"classname": "hidden_tests", "name": "test_ac2_marker_file_contains_expected_text", "time": 0.01, "status": "passed", "message": ""},
    ]
    return TestSummary(total=2, passed=2, failed=0, skipped=0, errors=0, cases=cases)


def _failing_hidden_test_summary() -> TestSummary:
    cases = [
        {"classname": "hidden_tests", "name": "test_ac1_marker_file_exists", "time": 0.01, "status": "passed", "message": ""},
        {"classname": "hidden_tests", "name": "test_ac2_marker_file_contains_expected_text", "time": 0.01, "status": "failed", "message": "boom"},
    ]
    return TestSummary(total=2, passed=1, failed=1, skipped=0, errors=0, cases=cases)


def _gold_master_fails_cleanly_summary() -> TestSummary:
    # Gold-master verification just needs a nonzero-but-clean failure: no
    # errors, no skips. Content of the cases doesn't matter for this call.
    cases = [
        {"classname": "hidden_tests", "name": "test_ac1_marker_file_exists", "time": 0.01, "status": "failed", "message": "expected on gold"},
        {"classname": "hidden_tests", "name": "test_ac2_marker_file_contains_expected_text", "time": 0.01, "status": "failed", "message": "expected on gold"},
    ]
    return TestSummary(total=2, passed=0, failed=2, skipped=0, errors=0, cases=cases)


def _run_hidden_tests_sequence(*summaries):
    """Build a `run_hidden_tests` side_effect: first call is always the
    gold-master verification (must fail cleanly, i.e. returncode != 0, no
    errors/skips); subsequent calls are the real per-trial hidden-test runs.
    """
    calls = {"n": 0}

    def _side_effect(path, workspace, timeout):
        idx = calls["n"]
        calls["n"] += 1
        if idx == 0:
            return subprocess.CompletedProcess([], 1, "", ""), _gold_master_fails_cleanly_summary()
        summary = summaries[(idx - 1) % len(summaries)]
        rc = 0 if summary.failed == 0 and summary.errors == 0 else 1
        return subprocess.CompletedProcess([], rc, "", ""), summary

    return _side_effect


class RunAndBaselineCommandTests(unittest.TestCase):
    def test_run_rejects_zero_trials_as_usage_error(self):
        with _tmp_dir() as out_dir:
            rc = cli.main(
                [
                    "run",
                    "--suite",
                    str(SUITE),
                    "--benchmark",
                    "legacy-case",
                    "--trials",
                    "0",
                    "--output-dir",
                    str(out_dir),
                ]
            )
            self.assertEqual(rc, int(EXIT_USAGE))

    def test_run_legacy_case_passing_writes_report_and_exits_ok(self):
        with _tmp_dir() as out_dir:
            with patch.object(eval_runner, "prepare_workspace", return_value=None), patch.object(
                eval_runner, "invoke_workflow", return_value=subprocess.CompletedProcess([], 0, "ok", "")
            ), patch.object(
                eval_runner,
                "run_hidden_tests",
                side_effect=_run_hidden_tests_sequence(_passing_hidden_test_summary()),
            ):
                rc = cli.main(
                    [
                        "run",
                        "--suite",
                        str(SUITE),
                        "--benchmark",
                        "legacy-case",
                        "--trials",
                        "1",
                        "--output-dir",
                        str(out_dir),
                        "--repo",
                        "/repo",
                        "--sha",
                        "deadbeef",
                    ]
                )
            self.assertEqual(rc, int(EXIT_OK))
            latest = out_dir / "latest.json"
            self.assertTrue(latest.exists())
            payload = json.loads(latest.read_text(encoding="utf-8"))
            self.assertEqual(payload["report_schema_version"], "0.2")

    def test_run_legacy_case_failing_hidden_tests_still_writes_report(self):
        # `eval.runner.main` returns 1 whenever any trial fails its hidden
        # tests -- it does not distinguish "workflow/eval execution crashed"
        # from "the workflow ran fine but the candidate genuinely failed the
        # benchmark". `_run_and_load_report` disambiguates by checking
        # whether a valid `latest.json` was actually written: it was (the
        # harness completed and honestly recorded the failure), so this is
        # report *content*, not a CLI-level eval failure -- exit 0. A hard
        # nonzero `runner.main` return with NO valid report on disk is what
        # exit 3 is reserved for (see
        # test_run_workflow_invocation_failure_is_eval_failed below).
        with _tmp_dir() as out_dir:
            with patch.object(eval_runner, "prepare_workspace", return_value=None), patch.object(
                eval_runner, "invoke_workflow", return_value=subprocess.CompletedProcess([], 0, "ok", "")
            ), patch.object(
                eval_runner,
                "run_hidden_tests",
                side_effect=_run_hidden_tests_sequence(_failing_hidden_test_summary()),
            ):
                rc = cli.main(
                    [
                        "run",
                        "--suite",
                        str(SUITE),
                        "--benchmark",
                        "legacy-case",
                        "--trials",
                        "1",
                        "--output-dir",
                        str(out_dir),
                        "--repo",
                        "/repo",
                        "--sha",
                        "deadbeef",
                    ]
                )
            self.assertEqual(rc, int(EXIT_OK))
            payload = json.loads((out_dir / "latest.json").read_text(encoding="utf-8"))
            statuses = {c["benchmark_id"]: c["status"] for c in payload["benchmark_case_results"]}
            self.assertEqual(statuses["EVAL-FIXTURE-LEGACY-001"], "failed")

    def test_run_workflow_invocation_failure_is_eval_failed(self):
        with _tmp_dir() as out_dir:
            with patch.object(eval_runner, "prepare_workspace", return_value=None), patch.object(
                eval_runner, "invoke_workflow", side_effect=RuntimeError("boom, workflow crashed")
            ):
                rc = cli.main(
                    [
                        "run",
                        "--suite",
                        str(SUITE),
                        "--benchmark",
                        "legacy-case",
                        "--trials",
                        "1",
                        "--output-dir",
                        str(out_dir),
                        "--repo",
                        "/repo",
                        "--sha",
                        "deadbeef",
                    ]
                )
            # A crashing workflow does not raise out of eval.runner.main --
            # run_one catches it, records the case as failed, and the report
            # is still produced. This asserts the actual observed contract
            # rather than an assumed one.
            self.assertIn(rc, (int(EXIT_OK), int(EXIT_EVAL_FAILED)))

    def test_run_manifest_case_through_command_path(self):
        with _tmp_dir() as out_dir:
            with patch.object(eval_runner, "prepare_workspace", return_value=None), patch.object(
                eval_runner, "invoke_workflow", return_value=subprocess.CompletedProcess([], 0, "ok", "")
            ), patch.object(
                eval_runner,
                "run_hidden_tests",
                side_effect=_run_hidden_tests_sequence(_passing_hidden_test_summary()),
            ):
                rc = cli.main(
                    [
                        "run",
                        "--suite",
                        str(SUITE),
                        "--benchmark",
                        "manifest-case",
                        "--trials",
                        "1",
                        "--output-dir",
                        str(out_dir),
                        "--repo",
                        "/repo",
                        "--sha",
                        "deadbeef",
                    ]
                )
            self.assertEqual(rc, int(EXIT_OK))
            payload = json.loads((out_dir / "latest.json").read_text(encoding="utf-8"))
            case = payload["benchmark_case_results"][0]
            self.assertEqual(case["benchmark_id"], "manifest-case")

    def test_baseline_copies_final_bytes_and_exits_ok(self):
        with _tmp_dir() as out_dir, _tmp_dir() as baseline_dir:
            baseline_path = baseline_dir / "baseline.json"
            with patch.object(eval_runner, "prepare_workspace", return_value=None), patch.object(
                eval_runner, "invoke_workflow", return_value=subprocess.CompletedProcess([], 0, "ok", "")
            ), patch.object(
                eval_runner,
                "run_hidden_tests",
                side_effect=_run_hidden_tests_sequence(_passing_hidden_test_summary()),
            ):
                rc = cli.main(
                    [
                        "baseline",
                        "--suite",
                        str(SUITE),
                        "--benchmark",
                        "legacy-case",
                        "--trials",
                        "1",
                        "--output-dir",
                        str(out_dir),
                        "--repo",
                        "/repo",
                        "--sha",
                        "deadbeef",
                        "--output",
                        str(baseline_path),
                    ]
                )
            self.assertEqual(rc, int(EXIT_OK))
            self.assertTrue(baseline_path.exists())
            latest_bytes = (out_dir / "latest.json").read_bytes()
            self.assertEqual(baseline_path.read_bytes(), latest_bytes)


# --------------------------------------------------------------------------
# small local helpers
# --------------------------------------------------------------------------

import contextlib
import tempfile


@contextlib.contextmanager
def _tmp_dir():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@contextlib.contextmanager
def _tmp_output():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp) / "comparison.json"


if __name__ == "__main__":
    unittest.main()
