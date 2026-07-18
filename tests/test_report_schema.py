import json
import unittest
from pathlib import Path

from eval.report_schema import (
    AcceptanceCriteriaResult,
    AcStatus,
    BenchmarkCaseResult,
    CapabilityScore,
    CaseStatus,
    EfficiencyScore,
    EvalReport,
    Metric,
    MetricStatus,
    Reference,
    ReliabilityScore,
    ReportValidationError,
    Scorecard,
    TraceabilityScore,
    TrialResult,
    TrialStatus,
    adapt_legacy_runner_report,
    validate_report_payload,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "eval" / "v02_report.json"


def _metric(status: str = "known", value=1.0):
    return Metric(status=MetricStatus(status), value=value if status == "known" else None)


def _minimal_scorecard() -> Scorecard:
    return Scorecard(
        capability=CapabilityScore(
            ac_pass_rate=_metric(),
            critical_ac_pass_rate=_metric(),
            hidden_test_pass_rate=_metric(),
            full_benchmark_pass_rate=_metric(),
        ),
        reliability=ReliabilityScore(
            trial_pass_rate=_metric(),
            variance=Metric.not_applicable("single trial"),
            flaky_case_count=_metric(value=0),
            workflow_failure_rate=_metric(value=0.0),
        ),
        efficiency=EfficiencyScore(
            wall_clock_duration_ms=_metric(value=1234),
            agent_invocation_count=Metric.unknown("no event log"),
            loop_iteration_count=Metric.unknown("no event log"),
            token_usage=Metric.not_collected("not enabled"),
            estimated_cost=Metric.not_collected("not enabled"),
        ),
        traceability=TraceabilityScore(
            trace_present=_metric(value=False),
            test_output_present=_metric(value=True),
            final_artifact_refs_present=_metric(value=True),
            final_diff_ref_present=_metric(value=False),
            prompt_hashes_present=Metric.not_collected("not implemented"),
        ),
    )


def _minimal_report() -> EvalReport:
    trial = TrialResult(
        trial_id="t1",
        benchmark_id="easy",
        status=TrialStatus.PASSED,
        started_at="2026-07-17T00:00:00Z",
        completed_at="2026-07-17T00:01:00Z",
        duration_ms=_metric(value=60000),
    )
    ac_pass = AcceptanceCriteriaResult(
        ac_id="AC1",
        description="does the thing",
        status=AcStatus.PASS,
        critical=True,
        evidence=[Reference(ref_id="e1", uri="agent-context/e1.json")],
    )
    ac_fail = AcceptanceCriteriaResult(
        ac_id="AC2",
        description="does the other thing",
        status=AcStatus.FAIL,
        critical=False,
        evidence=[],
        failure_reason="did not happen",
    )
    case = BenchmarkCaseResult(
        benchmark_id="easy",
        difficulty="easy",
        status=CaseStatus.PARTIAL,
        trial_results=[trial],
        acceptance_criteria_results=[ac_pass, ac_fail],
    )
    return EvalReport(
        eval_run_id="run-1",
        created_at="2026-07-17T00:00:00Z",
        candidate_version="abc123",
        benchmark_suite_id="easy",
        benchmark_case_results=[case],
        scorecard=_minimal_scorecard(),
    )


class MetricTests(unittest.TestCase):
    def test_easy__known_metric_round_trips(self):
        metric = Metric.known(0)
        self.assertEqual(metric.status, MetricStatus.KNOWN)
        self.assertEqual(metric.value, 0)
        restored = Metric.from_dict(metric.to_dict())
        self.assertEqual(restored, metric)

    def test_easy__zero_is_distinct_from_unknown(self):
        zero = Metric.known(0)
        unknown = Metric.unknown("not measured")
        self.assertNotEqual(zero.to_dict(), unknown.to_dict())
        self.assertIsNone(unknown.value)

    def test_medium__validate_payload_rejects_known_without_value(self):
        errors = Metric.validate_payload({"status": "known", "value": None}, "m")
        self.assertTrue(any("value is None" in e for e in errors))

    def test_medium__validate_payload_rejects_value_on_non_known_status(self):
        errors = Metric.validate_payload({"status": "not_collected", "value": 5}, "m")
        self.assertTrue(any("missing metrics must not carry a value" in e for e in errors))

    def test_medium__validate_payload_rejects_bad_status(self):
        errors = Metric.validate_payload({"status": "bogus"}, "m")
        self.assertTrue(any("invalid metric status" in e for e in errors))


class AcceptanceCriteriaResultTests(unittest.TestCase):
    def test_easy__pass_and_fail_round_trip(self):
        for status in (AcStatus.PASS, AcStatus.FAIL, AcStatus.UNKNOWN, AcStatus.SKIPPED):
            ac = AcceptanceCriteriaResult(ac_id="AC1", description="d", status=status, critical=False)
            restored = AcceptanceCriteriaResult.from_dict(ac.to_dict())
            self.assertEqual(restored.status, status)

    def test_medium__accepts_string_status(self):
        ac = AcceptanceCriteriaResult(ac_id="AC1", description="d", status="pass", critical=True)
        self.assertEqual(ac.status, AcStatus.PASS)

    def test_medium__validate_payload_flags_invalid_status(self):
        errors = AcceptanceCriteriaResult.validate_payload(
            {"ac_id": "AC1", "description": "d", "status": "maybe", "critical": True}, "ac"
        )
        self.assertTrue(any("invalid AC status" in e for e in errors))


class ScorecardTests(unittest.TestCase):
    def test_medium__round_trips_through_dict(self):
        scorecard = _minimal_scorecard()
        restored = Scorecard.from_dict(scorecard.to_dict())
        self.assertEqual(restored, scorecard)

    def test_medium__validate_payload_flags_missing_category(self):
        payload = _minimal_scorecard().to_dict()
        del payload["traceability"]
        errors = Scorecard.validate_payload(payload, "scorecard")
        self.assertTrue(any("missing 'traceability'" in e for e in errors))

    def test_hard__validate_payload_flags_malformed_metric_inside_category(self):
        payload = _minimal_scorecard().to_dict()
        payload["capability"]["ac_pass_rate"] = {"status": "known", "value": None}
        errors = Scorecard.validate_payload(payload, "scorecard")
        self.assertTrue(any("ac_pass_rate" in e for e in errors))


class EvalReportTests(unittest.TestCase):
    def test_easy__creates_report_object(self):
        report = _minimal_report()
        self.assertEqual(report.report_schema_version, "0.2")
        self.assertEqual(len(report.benchmark_case_results), 1)
        self.assertEqual(report.benchmark_case_results[0].acceptance_criteria_results[0].status, AcStatus.PASS)
        self.assertEqual(report.benchmark_case_results[0].acceptance_criteria_results[1].status, AcStatus.FAIL)

    def test_easy__serializes_to_json_and_back(self):
        report = _minimal_report()
        text = report.to_json()
        restored = EvalReport.from_json(text)
        self.assertEqual(restored.eval_run_id, report.eval_run_id)
        self.assertEqual(len(restored.benchmark_case_results), 1)
        self.assertEqual(restored.scorecard.efficiency.token_usage.status, MetricStatus.NOT_COLLECTED)

    def test_easy__to_dict_is_json_serializable(self):
        report = _minimal_report()
        json.dumps(report.to_dict())  # must not raise

    def test_medium__validate_returns_empty_for_valid_report(self):
        report = _minimal_report()
        self.assertEqual(report.validate(), [])

    def test_medium__from_dict_raises_on_missing_required_field(self):
        payload = _minimal_report().to_dict()
        del payload["eval_run_id"]
        with self.assertRaises(ReportValidationError) as ctx:
            EvalReport.from_dict(payload)
        self.assertTrue(any("eval_run_id" in e for e in ctx.exception.errors))

    def test_medium__from_dict_raises_on_invalid_case_status(self):
        payload = _minimal_report().to_dict()
        payload["benchmark_case_results"][0]["status"] = "not-a-status"
        with self.assertRaises(ReportValidationError) as ctx:
            EvalReport.from_dict(payload)
        self.assertTrue(any("invalid case status" in e for e in ctx.exception.errors))

    def test_hard__validate_report_payload_collects_multiple_errors(self):
        payload = _minimal_report().to_dict()
        del payload["eval_run_id"]
        payload["benchmark_case_results"][0]["status"] = "bogus"
        errors = validate_report_payload(payload)
        self.assertGreaterEqual(len(errors), 2)

    def test_hard__evidence_references_are_preserved_round_trip(self):
        report = _minimal_report()
        restored = EvalReport.from_dict(report.to_dict())
        ac_with_evidence = restored.benchmark_case_results[0].acceptance_criteria_results[0]
        self.assertEqual(len(ac_with_evidence.evidence), 1)
        self.assertEqual(ac_with_evidence.evidence[0].uri, "agent-context/e1.json")


class FixtureTests(unittest.TestCase):
    def test_easy__fixture_loads_and_validates(self):
        text = FIXTURE_PATH.read_text(encoding="utf-8")
        report = EvalReport.from_json(text)
        self.assertEqual(report.validate(), [])

    def test_medium__fixture_has_pass_and_fail_ac(self):
        report = EvalReport.from_json(FIXTURE_PATH.read_text(encoding="utf-8"))
        statuses = {ac.status for ac in report.benchmark_case_results[0].acceptance_criteria_results}
        self.assertIn(AcStatus.PASS, statuses)
        self.assertIn(AcStatus.FAIL, statuses)

    def test_medium__fixture_represents_missing_metrics_honestly(self):
        report = EvalReport.from_json(FIXTURE_PATH.read_text(encoding="utf-8"))
        token_usage = report.scorecard.efficiency.token_usage
        self.assertEqual(token_usage.status, MetricStatus.NOT_COLLECTED)
        self.assertIsNone(token_usage.value)

    def test_hard__fixture_round_trips_byte_stable_through_dict(self):
        report = EvalReport.from_json(FIXTURE_PATH.read_text(encoding="utf-8"))
        again = EvalReport.from_dict(report.to_dict())
        self.assertEqual(again.to_dict(), report.to_dict())


class LegacyAdapterTests(unittest.TestCase):
    def test_medium__adapts_legacy_report_without_llm_calls(self):
        legacy_payload = {
            "created_at": "2026-07-17T00:00:00Z",
            "repo": "/tmp/repo",
            "sha": "deadbeef",
            "runner": "claude",
            "model": "fake-model",
            "runs": 1,
            "summary": {
                "quality": {"weighted_score": 1.0},
                "reliability": {"pass_rate": 1.0},
                "efficiency": {"wall_seconds_mean": 12.5, "tokens_total_mean": 100, "cost_usd_mean": 0.0},
            },
            "results": [
                {
                    "name": "easy",
                    "run_id": "easy-t1",
                    "status": "PASS",
                    "error": "",
                    "quality": {"weighted_score": 1.0, "ac_failed_ids": []},
                    "metrics": {"wall_seconds": 12.5},
                    "story": {"acceptance_criteria": [{"id": "AC1", "description": "d"}]},
                    "artifacts": {"story": "/tmp/story.json"},
                }
            ],
        }
        report = adapt_legacy_runner_report(legacy_payload)
        self.assertEqual(report.validate(), [])
        self.assertEqual(len(report.benchmark_case_results), 1)
        self.assertEqual(report.benchmark_case_results[0].acceptance_criteria_results[0].status, AcStatus.PASS)
        self.assertEqual(report.scorecard.efficiency.wall_clock_duration_ms.value, 12500)

    def test_medium__adapts_legacy_report_with_missing_metrics_as_not_collected(self):
        legacy_payload = {
            "created_at": "2026-07-17T00:00:00Z",
            "sha": "deadbeef",
            "runner": "claude",
            "summary": {},
            "results": [
                {
                    "name": "hard",
                    "run_id": "hard-t1",
                    "status": "FAIL",
                    "error": "workflow failed",
                    "quality": {"weighted_score": 0.0, "ac_failed_ids": ["AC1"]},
                    "metrics": {},
                    "story": {"acceptance_criteria": [{"id": "AC1", "description": "d"}]},
                    "artifacts": {},
                }
            ],
        }
        report = adapt_legacy_runner_report(legacy_payload)
        self.assertEqual(report.validate(), [])
        self.assertEqual(report.scorecard.efficiency.token_usage.status, MetricStatus.NOT_COLLECTED)
        trial = report.benchmark_case_results[0].trial_results[0]
        self.assertEqual(trial.duration_ms.status, MetricStatus.NOT_COLLECTED)
        self.assertEqual(report.benchmark_case_results[0].acceptance_criteria_results[0].status, AcStatus.FAIL)


if __name__ == "__main__":
    unittest.main()
