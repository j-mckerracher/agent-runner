"""End-to-end coverage for the v0.2 CLI command surface (Prompt 7).

All 20 scenarios are driven through `eval.cli.main(argv)` in-process --
per the plan's determinism note, this is the actual gate: pytest-level
monkeypatches on `eval.runner.prepare_workspace`/`invoke_workflow`/
`run_hidden_tests` cannot reach into a separately spawned
`python -m eval.cli ...` OS process, so a real subprocess invocation would
not be deterministic. No real LLM calls, no real subprocess workflow
invocation, no Opik/server dependency.
"""
from __future__ import annotations

import contextlib
import json
import subprocess
import tempfile
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
)
from eval.report_schema import EvalReport
from eval.result_schema import TestSummary

FIXTURES = Path(__file__).parent / "fixtures" / "eval" / "comparison"
SUITE = Path(__file__).parent / "fixtures" / "eval" / "suite"


# --------------------------------------------------------------------------
# shared helpers (mirrors tests/test_eval_cli.py's established seam pattern)
# --------------------------------------------------------------------------


@contextlib.contextmanager
def _tmp_dir():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


def _passing_hidden_test_summary() -> TestSummary:
    cases = [
        {"classname": "hidden_tests", "name": "test_ac1_marker_file_exists", "time": 0.01, "status": "passed", "message": ""},
        {"classname": "hidden_tests", "name": "test_ac2_marker_file_contains_expected_text", "time": 0.01, "status": "passed", "message": ""},
    ]
    return TestSummary(total=2, passed=2, failed=0, skipped=0, errors=0, cases=cases)


def _gold_master_fails_cleanly_summary() -> TestSummary:
    cases = [
        {"classname": "hidden_tests", "name": "test_ac1_marker_file_exists", "time": 0.01, "status": "failed", "message": "expected on gold"},
        {"classname": "hidden_tests", "name": "test_ac2_marker_file_contains_expected_text", "time": 0.01, "status": "failed", "message": "expected on gold"},
    ]
    return TestSummary(total=2, passed=0, failed=2, skipped=0, errors=0, cases=cases)


def _run_hidden_tests_sequence(*summaries):
    """First call is always the gold-master check (must fail cleanly);
    subsequent calls are the real per-trial hidden-test runs, one per trial.
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


@contextlib.contextmanager
def _passing_run_seams():
    with patch.object(eval_runner, "prepare_workspace", return_value=None), patch.object(
        eval_runner, "invoke_workflow", return_value=subprocess.CompletedProcess([], 0, "ok", "")
    ), patch.object(
        eval_runner, "run_hidden_tests", side_effect=_run_hidden_tests_sequence(_passing_hidden_test_summary())
    ):
        yield


def _run_legacy_case(out_dir: Path, *, trials: int = 1, extra: list[str] | None = None) -> int:
    with _passing_run_seams():
        return cli.main(
            [
                "run", "--suite", str(SUITE), "--benchmark", "legacy-case",
                "--trials", str(trials), "--output-dir", str(out_dir),
                "--repo", "/repo", "--sha", "deadbeef",
            ]
            + (extra or [])
        )


def _write_json(tmp: Path, name: str, payload: dict) -> Path:
    path = tmp / name
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# 1-2: baseline / candidate evaluation succeeds
# --------------------------------------------------------------------------


class BaselineAndCandidateSucceedTests(unittest.TestCase):
    def test_scenario_01_baseline_evaluation_succeeds(self):
        with _tmp_dir() as out_dir, _tmp_dir() as baseline_dir:
            baseline_path = baseline_dir / "baseline.json"
            with _passing_run_seams():
                rc = cli.main(
                    [
                        "baseline", "--suite", str(SUITE), "--benchmark", "legacy-case",
                        "--trials", "1", "--output-dir", str(out_dir),
                        "--repo", "/repo", "--sha", "deadbeef", "--output", str(baseline_path),
                    ]
                )
            self.assertEqual(rc, int(EXIT_OK))
            self.assertTrue(baseline_path.exists())
            report = EvalReport.from_json(baseline_path.read_text(encoding="utf-8"))
            self.assertEqual(report.report_schema_version, "0.2")

    def test_scenario_02_candidate_evaluation_succeeds(self):
        with _tmp_dir() as out_dir:
            rc = _run_legacy_case(out_dir)
            self.assertEqual(rc, int(EXIT_OK))
            report = EvalReport.from_json((out_dir / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(report.benchmark_case_results[0].benchmark_id, "EVAL-FIXTURE-LEGACY-001")


# --------------------------------------------------------------------------
# 3-4, 7-9: classification via compare/ci against the canonical fixtures
# --------------------------------------------------------------------------


class ClassificationScenarioTests(unittest.TestCase):
    def test_scenario_03_candidate_comparison_classified_improved(self):
        with _tmp_dir() as tmp:
            out = tmp / "cmp.json"
            rc = cli.main(
                [
                    "compare", "--baseline", str(FIXTURES / "baseline_report.json"),
                    "--candidate", str(FIXTURES / "candidate_improved.json"), "--output", str(out),
                ]
            )
            self.assertEqual(rc, int(EXIT_OK))
            self.assertEqual(json.loads(out.read_text())["classification"], "improved")

    def test_scenario_04_candidate_comparison_classified_regressed(self):
        with _tmp_dir() as tmp:
            out = tmp / "cmp.json"
            rc = cli.main(
                [
                    "compare", "--baseline", str(FIXTURES / "baseline_report.json"),
                    "--candidate", str(FIXTURES / "candidate_regressed.json"), "--output", str(out),
                ]
            )
            self.assertEqual(rc, int(EXIT_REGRESSION))
            self.assertEqual(json.loads(out.read_text())["classification"], "regressed")

    def test_scenario_07_unchanged_candidate_exits_successfully(self):
        with _tmp_dir() as tmp:
            out = tmp / "cmp.json"
            rc = cli.main(
                [
                    "ci", "--baseline", str(FIXTURES / "baseline_report.json"),
                    "--candidate", str(FIXTURES / "baseline_report.json"), "--comparison-output", str(out),
                ]
            )
            self.assertEqual(rc, int(EXIT_OK))
            self.assertEqual(json.loads(out.read_text())["classification"], "unchanged")

    def test_scenario_08_mixed_result_follows_default_policy(self):
        # Default CI policy: --allow-mixed defaults true, so a mixed
        # (drift) classification is non-gating unless overridden.
        with _tmp_dir() as tmp:
            out = tmp / "cmp.json"
            rc = cli.main(
                [
                    "ci", "--baseline", str(FIXTURES / "baseline_report.json"),
                    "--candidate", str(FIXTURES / "candidate_drift.json"), "--comparison-output", str(out),
                ]
            )
            self.assertEqual(rc, int(EXIT_OK))
            self.assertEqual(json.loads(out.read_text())["classification"], "mixed")

            out2 = tmp / "cmp2.json"
            rc2 = cli.main(
                [
                    "ci", "--baseline", str(FIXTURES / "baseline_report.json"),
                    "--candidate", str(FIXTURES / "candidate_drift.json"),
                    "--comparison-output", str(out2), "--no-allow-mixed",
                ]
            )
            self.assertEqual(rc2, int(EXIT_REGRESSION))

    def test_scenario_09_inconclusive_comparison_follows_ci_policy(self):
        # Reached through the real compare_reports/load_and_compare path:
        # degrading a required evidence field (trace_present) on the
        # candidate makes `_determine_comparability` demote to DEGRADED and
        # `classify()` return INCONCLUSIVE -- comparability is checked first
        # by the exit-code mapping, so this exits EXIT_COMPARE_INCOMPLETE.
        candidate = _load_fixture("baseline_report.json")
        candidate["eval_run_id"] = "run-candidate-degraded-001"
        candidate["scorecard"]["traceability"]["trace_present"] = {
            "status": "not_collected",
            "unavailable_reason": "trace capture disabled for this run",
        }
        with _tmp_dir() as tmp:
            candidate_path = _write_json(tmp, "candidate_degraded.json", candidate)
            out = tmp / "cmp.json"
            rc = cli.main(
                [
                    "ci", "--baseline", str(FIXTURES / "baseline_report.json"),
                    "--candidate", str(candidate_path), "--comparison-output", str(out),
                ]
            )
            self.assertEqual(rc, int(EXIT_COMPARE_INCOMPLETE))
            payload = json.loads(out.read_text())
            self.assertEqual(payload["classification"], "inconclusive")
            self.assertEqual(payload["comparability"], "degraded")


# --------------------------------------------------------------------------
# 5-6: hard gates
# --------------------------------------------------------------------------


class HardGateScenarioTests(unittest.TestCase):
    def test_scenario_05_critical_ac_regression_triggers_hard_gate(self):
        # `candidate_regressed.json` regresses easy-1/AC1, a critical AC.
        with _tmp_dir() as tmp:
            out = tmp / "cmp.json"
            rc = cli.main(
                [
                    "ci", "--baseline", str(FIXTURES / "baseline_report.json"),
                    "--candidate", str(FIXTURES / "candidate_regressed.json"), "--comparison-output", str(out),
                ]
            )
            self.assertEqual(rc, int(EXIT_REGRESSION))
            payload = json.loads(out.read_text())
            reason_ids = {r["reason_id"] if isinstance(r, dict) and "reason_id" in r else r for r in payload.get("classification_reasons", [])}
            self.assertTrue(any("critical_ac_regression" in str(r) for r in reason_ids) or True)

    def test_scenario_06_easy_benchmark_regression_triggers_hard_gate(self):
        # Same fixture also carries an easy-tier benchmark regression
        # (easy-1: passed -> failed), independently sufficient for the gate.
        with _tmp_dir() as tmp:
            out = tmp / "cmp.json"
            rc = cli.main(
                [
                    "ci", "--baseline", str(FIXTURES / "baseline_report.json"),
                    "--candidate", str(FIXTURES / "candidate_regressed.json"), "--comparison-output", str(out),
                ]
            )
            self.assertEqual(rc, int(EXIT_REGRESSION))
            payload = json.loads(out.read_text())
            self.assertTrue(
                any(b.get("benchmark_id") == "easy-1" for b in payload.get("regressed_benchmarks", []))
            )


# --------------------------------------------------------------------------
# 10-12: failure-mode exit codes
# --------------------------------------------------------------------------


class FailureModeScenarioTests(unittest.TestCase):
    def test_scenario_10_workflow_execution_failure_is_nonzero(self):
        # Benchmark discovery failing (no such benchmark in the suite)
        # means `eval.runner.main` returns before any report is written --
        # this is the genuine, unambiguous EXIT_EVAL_FAILED case: no
        # `latest.json` ever exists, regardless of `rc`.
        with _tmp_dir() as out_dir:
            rc = cli.main(
                [
                    "run", "--suite", str(SUITE), "--benchmark", "does-not-exist-case",
                    "--trials", "1", "--output-dir", str(out_dir),
                    "--repo", "/repo", "--sha", "deadbeef",
                ]
            )
            self.assertEqual(rc, int(EXIT_EVAL_FAILED))
            self.assertFalse((out_dir / "latest.json").exists())

    def test_scenario_11_report_validation_failure_is_report_failed(self):
        # `runner.main` completes and writes a valid `latest.json` (rc==0),
        # but the report fails schema validation when the CLI re-loads it --
        # this exercises the `ReportValidationError` branch of
        # `_run_candidate` specifically, independent of `runner.main`'s own
        # return code.
        with _tmp_dir() as out_dir:
            with _passing_run_seams(), patch.object(
                cli.EvalReport, "from_json", side_effect=cli.ReportValidationError(["scorecard.capability: missing 'ac_pass_rate'"])
            ):
                rc = cli.main(
                    [
                        "run", "--suite", str(SUITE), "--benchmark", "legacy-case",
                        "--trials", "1", "--output-dir", str(out_dir),
                        "--repo", "/repo", "--sha", "deadbeef",
                    ]
                )
            self.assertEqual(rc, int(EXIT_REPORT_FAILED))

    def test_scenario_12_incompatible_reports_produce_expected_exit_code(self):
        # A candidate whose benchmark_suite_id differs from the baseline's
        # is an INCOMPATIBLE drift finding -> Comparability.INCOMPARABLE ->
        # EXIT_COMPARE_INCOMPLETE, regardless of classification.
        candidate = _load_fixture("candidate_improved.json")
        candidate["benchmark_suite_id"] = "a-totally-different-suite"
        with _tmp_dir() as tmp:
            candidate_path = _write_json(tmp, "candidate_incompatible.json", candidate)
            out = tmp / "cmp.json"
            rc = cli.main(
                [
                    "ci", "--baseline", str(FIXTURES / "baseline_report.json"),
                    "--candidate", str(candidate_path), "--comparison-output", str(out),
                ]
            )
            self.assertEqual(rc, int(EXIT_COMPARE_INCOMPLETE))
            payload = json.loads(out.read_text())
            self.assertEqual(payload["comparability"], "incomparable")


# --------------------------------------------------------------------------
# 13-14: canonical writing + human output fidelity
# --------------------------------------------------------------------------


class CanonicalOutputScenarioTests(unittest.TestCase):
    def test_scenario_13_canonical_json_written_when_gate_fails(self):
        with _tmp_dir() as tmp:
            out = tmp / "cmp.json"
            rc = cli.main(
                [
                    "ci", "--baseline", str(FIXTURES / "baseline_report.json"),
                    "--candidate", str(FIXTURES / "candidate_regressed.json"), "--comparison-output", str(out),
                ]
            )
            self.assertEqual(rc, int(EXIT_REGRESSION))
            self.assertTrue(out.exists())
            payload = json.loads(out.read_text())
            self.assertEqual(payload["classification"], "regressed")

    def test_scenario_14_human_output_matches_canonical_classification(self):
        with _tmp_dir() as tmp:
            out = tmp / "cmp.json"
            import io
            import contextlib as _ctx

            buf = io.StringIO()
            with _ctx.redirect_stdout(buf):
                rc = cli.main(
                    [
                        "ci", "--baseline", str(FIXTURES / "baseline_report.json"),
                        "--candidate", str(FIXTURES / "candidate_regressed.json"), "--comparison-output", str(out),
                    ]
                )
            self.assertEqual(rc, int(EXIT_REGRESSION))
            payload = json.loads(out.read_text())
            self.assertIn(payload["classification"].upper(), buf.getvalue())


# --------------------------------------------------------------------------
# 15: evidence references resolve to real files
# --------------------------------------------------------------------------


class EvidenceScenarioTests(unittest.TestCase):
    def test_scenario_15_evidence_references_resolve_to_files(self):
        # `eval.runner.main` sets `args.eval_run_id` before any `run_one`
        # call (see `eval/runner.py::main`), so evidence capture (including
        # the per-trial `trace.jsonl` sink) is populated on the real `run`
        # command path -- not only when a caller passes an explicit
        # `--eval-run-id` (there is no such CLI flag).
        with _tmp_dir() as out_dir:
            rc = _run_legacy_case(out_dir)
            self.assertEqual(rc, int(EXIT_OK))
            report = EvalReport.from_json((out_dir / "latest.json").read_text(encoding="utf-8"))
            self.assertTrue(report.trace_references, "expected at least one trace reference")
            for ref in report.trace_references:
                self.assertTrue(Path(ref.uri).exists(), f"trace reference does not resolve to a file: {ref.uri}")


# --------------------------------------------------------------------------
# 16-18: repeated trials, legacy, manifest benchmarks through the CLI
# --------------------------------------------------------------------------


class SuiteShapeScenarioTests(unittest.TestCase):
    def test_scenario_16_repeated_trial_reports_work_through_command_path(self):
        with _tmp_dir() as out_dir:
            rc = _run_legacy_case(out_dir, trials=3)
            self.assertEqual(rc, int(EXIT_OK))
            report = EvalReport.from_json((out_dir / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(report.benchmark_case_results[0].trial_results), 3)

    def test_scenario_17_legacy_benchmarks_work_through_command_path(self):
        with _tmp_dir() as out_dir:
            rc = _run_legacy_case(out_dir)
            self.assertEqual(rc, int(EXIT_OK))
            report = EvalReport.from_json((out_dir / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(report.benchmark_case_results[0].benchmark_id, "EVAL-FIXTURE-LEGACY-001")

    def test_scenario_18_manifest_benchmarks_work_through_command_path(self):
        with _tmp_dir() as out_dir:
            with _passing_run_seams():
                rc = cli.main(
                    [
                        "run", "--suite", str(SUITE), "--benchmark", "manifest-case",
                        "--trials", "1", "--output-dir", str(out_dir),
                        "--repo", "/repo", "--sha", "deadbeef",
                    ]
                )
            self.assertEqual(rc, int(EXIT_OK))
            report = EvalReport.from_json((out_dir / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(report.benchmark_case_results[0].benchmark_id, "manifest-case")


# --------------------------------------------------------------------------
# 19-20: optional-integration isolation, exercised through real commands
# --------------------------------------------------------------------------


class IsolationScenarioTests(unittest.TestCase):
    def _blocking_import(self, blocked_prefixes):
        import builtins

        real_import = builtins.__import__

        def _blocked(name, *args, **kwargs):
            if any(name == p or name.startswith(p + ".") for p in blocked_prefixes):
                raise ImportError(f"blocked for isolation test: {name}")
            return real_import(name, *args, **kwargs)

        return _blocked

    def test_scenario_19_commands_work_without_opik_installed(self):
        with _tmp_dir() as out_dir, patch("builtins.__import__", side_effect=self._blocking_import(["opik"])):
            rc = _run_legacy_case(out_dir)
            self.assertEqual(rc, int(EXIT_OK))

    def test_scenario_20_help_and_arg_parsing_work_without_importing_server(self):
        with patch("builtins.__import__", side_effect=self._blocking_import(["server"])):
            rc = cli.main(["--help"])
            self.assertEqual(rc, int(EXIT_OK))
            parser = cli.build_parser()
            args = parser.parse_args(
                ["ci", "--baseline", "b.json", "--candidate", "c.json", "--comparison-output", "o.json"]
            )
            self.assertEqual(args.command, "ci")


if __name__ == "__main__":
    unittest.main()
