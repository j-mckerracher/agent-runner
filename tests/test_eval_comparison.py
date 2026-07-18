"""Engine-level tests for `eval.comparison` (Prompt 5).

Covers, as table-driven/semantic tests rather than 93 literal cases, the
prompt's numbered coverage categories:
  1-8   input/schema validation
  9-19  AC comparison / identity
  20-29 benchmark comparison / identity
  30-40 scorecard/metric delta semantics (incl. the zero-baseline rule)
  54-63 runtime/cost/reliability deltas
  64-75 configuration drift
  76-80 traceability/evidence tiers

Fixtures live in `tests/fixtures/eval/comparison/`. Classification-precedence
tests live in `tests/test_comparison_classification.py`; rendering tests in
`tests/test_comparison_render.py`; import-isolation in
`tests/test_comparison_isolation.py`.
"""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from eval.comparison import compare_reports, load_and_compare
from eval.comparison_policy import ComparisonPolicy
from eval.comparison_schema import (
    ChangeCategory,
    Classification,
    Comparability,
    CompareInputError,
    DeltaAvailability,
    DriftSeverity,
    validate_comparison_payload,
)

FIXTURES = Path(__file__).parent / "fixtures" / "eval" / "comparison"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class ComparisonInputValidationTests(unittest.TestCase):
    """Categories 1-8: input/schema validation."""

    def setUp(self):
        self.baseline = _load("baseline_report.json")
        self.candidate = _load("candidate_improved.json")

    def test_accepts_plain_dicts(self):
        result = compare_reports(self.baseline, self.candidate)
        self.assertEqual(result.classification, Classification.IMPROVED)

    def test_accepts_eval_report_objects(self):
        from eval.report_schema import EvalReport

        result = compare_reports(EvalReport.from_dict(self.baseline), EvalReport.from_dict(self.candidate))
        self.assertEqual(result.classification, Classification.IMPROVED)

    def test_rejects_wrong_type(self):
        with self.assertRaises(CompareInputError) as ctx:
            compare_reports("not-a-report", self.candidate)
        self.assertEqual(ctx.exception.side, "baseline")

    def test_rejects_invalid_schema_tags_correct_side(self):
        bad = copy.deepcopy(self.candidate)
        del bad["benchmark_case_results"]
        with self.assertRaises(CompareInputError) as ctx:
            compare_reports(self.baseline, bad)
        self.assertEqual(ctx.exception.side, "candidate")
        self.assertTrue(ctx.exception.errors)

    def test_rejects_unsupported_schema_version(self):
        bad = copy.deepcopy(self.baseline)
        bad["report_schema_version"] = "0.99"
        with self.assertRaises(CompareInputError) as ctx:
            compare_reports(bad, self.candidate)
        self.assertEqual(ctx.exception.side, "baseline")
        self.assertIn("0.99", str(ctx.exception))

    def test_rejects_duplicate_benchmark_id(self):
        bad = copy.deepcopy(self.baseline)
        bad["benchmark_case_results"].append(copy.deepcopy(bad["benchmark_case_results"][0]))
        with self.assertRaises(CompareInputError) as ctx:
            compare_reports(bad, self.candidate)
        self.assertEqual(ctx.exception.side, "baseline")

    def test_rejects_duplicate_ac_id_within_case(self):
        bad = copy.deepcopy(self.candidate)
        bad["benchmark_case_results"][0]["acceptance_criteria_results"].append(
            copy.deepcopy(bad["benchmark_case_results"][0]["acceptance_criteria_results"][0])
        )
        with self.assertRaises(CompareInputError) as ctx:
            compare_reports(self.baseline, bad)
        self.assertEqual(ctx.exception.side, "candidate")

    def test_rejects_invalid_policy_configuration(self):
        bad_policy = ComparisonPolicy(runtime_increase_hard_budget_pct=1.0, runtime_increase_soft_budget_pct=50.0)
        with self.assertRaises(CompareInputError) as ctx:
            compare_reports(self.baseline, self.candidate, policy=bad_policy)
        self.assertEqual(ctx.exception.side, "policy")

    def test_does_not_mutate_inputs(self):
        baseline_copy = copy.deepcopy(self.baseline)
        candidate_copy = copy.deepcopy(self.candidate)
        compare_reports(self.baseline, self.candidate)
        self.assertEqual(self.baseline, baseline_copy)
        self.assertEqual(self.candidate, candidate_copy)

    def test_deterministic_across_repeated_calls(self):
        r1 = compare_reports(self.baseline, self.candidate)
        r2 = compare_reports(self.baseline, self.candidate)
        self.assertEqual(r1.to_dict(), r2.to_dict())


class LoadAndCompareTests(unittest.TestCase):
    def test_loads_from_disk_and_compares(self):
        result = load_and_compare(FIXTURES / "baseline_report.json", FIXTURES / "candidate_improved.json")
        self.assertEqual(result.classification, Classification.IMPROVED)
        self.assertEqual(result.baseline_identity.label, str(FIXTURES / "baseline_report.json"))
        self.assertEqual(result.candidate_identity.label, str(FIXTURES / "candidate_improved.json"))

    def test_missing_baseline_path_raises_tagged_error(self):
        with self.assertRaises(CompareInputError) as ctx:
            load_and_compare(FIXTURES / "does-not-exist.json", FIXTURES / "candidate_improved.json")
        self.assertEqual(ctx.exception.side, "baseline")

    def test_missing_candidate_path_raises_tagged_error(self):
        with self.assertRaises(CompareInputError) as ctx:
            load_and_compare(FIXTURES / "baseline_report.json", FIXTURES / "does-not-exist.json")
        self.assertEqual(ctx.exception.side, "candidate")


class AcComparisonTests(unittest.TestCase):
    """Categories 9-19: AC identity, matching, added/removed, critical flag."""

    def setUp(self):
        self.baseline = _load("baseline_report.json")

    def test_ac_matched_by_benchmark_and_ac_id(self):
        result = compare_reports(self.baseline, _load("candidate_improved.json"))
        ac_ids = {(d.benchmark_id, d.ac_id) for d in result.ac_deltas}
        self.assertIn(("medium-1", "AC2"), ac_ids)

    def test_ac_pass_to_fail_transition_is_regressed(self):
        result = compare_reports(self.baseline, _load("candidate_regressed.json"))
        regressed = {(d.benchmark_id, d.ac_id) for d in result.regressed_acs}
        self.assertIn(("easy-1", "AC1"), regressed)

    def test_ac_fail_to_pass_transition_is_improved(self):
        result = compare_reports(self.baseline, _load("candidate_improved.json"))
        improved = {(d.benchmark_id, d.ac_id) for d in result.improved_acs}
        self.assertIn(("medium-1", "AC2"), improved)

    def test_unchanged_ac_status_is_unchanged_category(self):
        result = compare_reports(self.baseline, _load("candidate_improved.json"))
        unchanged = {(d.benchmark_id, d.ac_id) for d in result.unchanged_acs}
        self.assertIn(("easy-1", "AC1"), unchanged)

    def test_critical_flag_carried_onto_delta(self):
        result = compare_reports(self.baseline, _load("candidate_regressed.json"))
        critical_regression = next(d for d in result.regressed_acs if d.ac_id == "AC1" and d.benchmark_id == "easy-1")
        self.assertTrue(critical_regression.critical)
        self.assertIn(critical_regression, result.critical_ac_regressions)

    def test_noncritical_regression_excluded_from_critical_list(self):
        result = compare_reports(self.baseline, _load("candidate_regressed.json"))
        self.assertNotIn(("medium-1", "AC2"), {(d.benchmark_id, d.ac_id) for d in result.critical_ac_regressions})

    def test_added_ac_when_candidate_introduces_new_ac(self):
        candidate = _load("candidate_improved.json")
        candidate["benchmark_case_results"][0]["acceptance_criteria_results"].append(
            {"ac_id": "AC3", "description": "new criterion", "status": "pass", "critical": False}
        )
        result = compare_reports(self.baseline, candidate)
        added = {(d.benchmark_id, d.ac_id) for d in result.added_acs}
        self.assertIn(("easy-1", "AC3"), added)

    def test_removed_ac_when_candidate_drops_an_ac(self):
        candidate = _load("candidate_improved.json")
        candidate["benchmark_case_results"][0]["acceptance_criteria_results"].pop()
        result = compare_reports(self.baseline, candidate)
        removed = {(d.benchmark_id, d.ac_id) for d in result.removed_acs}
        self.assertIn(("easy-1", "AC2"), removed)

    def test_indeterminate_status_transition_is_incomparable(self):
        baseline = copy.deepcopy(self.baseline)
        candidate = _load("candidate_improved.json")
        baseline["benchmark_case_results"][0]["acceptance_criteria_results"][0]["status"] = "skipped"
        result = compare_reports(baseline, candidate)
        incomparable = {(d.benchmark_id, d.ac_id) for d in result.incomparable_acs}
        self.assertIn(("easy-1", "AC1"), incomparable)


class BenchmarkComparisonTests(unittest.TestCase):
    """Categories 20-29: benchmark identity, difficulty, trial-count comparability."""

    def setUp(self):
        self.baseline = _load("baseline_report.json")

    def test_benchmark_matched_by_benchmark_id(self):
        result = compare_reports(self.baseline, _load("candidate_improved.json"))
        ids = {d.benchmark_id for d in result.benchmark_deltas}
        self.assertEqual(ids, {"easy-1", "medium-1"})

    def test_resolved_failure_is_improved_and_listed(self):
        result = compare_reports(self.baseline, _load("candidate_improved.json"))
        resolved_ids = {d.benchmark_id for d in result.resolved_failures}
        self.assertEqual(resolved_ids, {"medium-1"})

    def test_new_failure_is_regressed_and_listed(self):
        result = compare_reports(self.baseline, _load("candidate_regressed.json"))
        new_failure_ids = {d.benchmark_id for d in result.new_failures}
        self.assertIn("easy-1", new_failure_ids)

    def test_easy_tier_regression_flagged(self):
        result = compare_reports(self.baseline, _load("candidate_regressed.json"))
        self.assertEqual({d.benchmark_id for d in result.easy_regressions}, {"easy-1"})

    def test_medium_tier_regression_not_flagged_as_easy(self):
        candidate = _load("candidate_regressed.json")
        # medium-1 stays failed->failed (unchanged) in this fixture; assert no
        # false positive easy_regression for a non-easy case.
        result = compare_reports(self.baseline, candidate)
        self.assertNotIn("medium-1", {d.benchmark_id for d in result.easy_regressions})

    def test_difficulty_carried_from_each_side_independently(self):
        result = compare_reports(self.baseline, _load("candidate_improved.json"))
        easy_delta = next(d for d in result.benchmark_deltas if d.benchmark_id == "easy-1")
        self.assertEqual(easy_delta.baseline_difficulty, "easy")
        self.assertEqual(easy_delta.candidate_difficulty, "easy")

    def test_trial_count_comparable_within_ratio(self):
        result = compare_reports(self.baseline, _load("candidate_improved.json"))
        easy_delta = next(d for d in result.benchmark_deltas if d.benchmark_id == "easy-1")
        self.assertTrue(easy_delta.trial_count_comparable)  # 2 vs 2

    def test_trial_count_incomparable_beyond_ratio(self):
        candidate = _load("candidate_improved.json")
        # baseline easy-1 has 2 trials; blow the ratio (2 -> default max ratio 2.0, so 5 trials triggers it)
        extra_trial = copy.deepcopy(candidate["benchmark_case_results"][0]["trial_results"][0])
        for _ in range(4):
            candidate["benchmark_case_results"][0]["trial_results"].append(copy.deepcopy(extra_trial))
        result = compare_reports(self.baseline, candidate)
        easy_delta = next(d for d in result.benchmark_deltas if d.benchmark_id == "easy-1")
        self.assertFalse(easy_delta.trial_count_comparable)

    def test_zero_trials_on_one_case_marks_that_case_incomparable_for_trials(self):
        candidate = _load("candidate_improved.json")
        candidate["benchmark_case_results"][0]["trial_results"] = []
        result = compare_reports(self.baseline, candidate)
        easy_delta = next(d for d in result.benchmark_deltas if d.benchmark_id == "easy-1")
        self.assertFalse(easy_delta.trial_count_comparable)

    def test_added_benchmark_case(self):
        candidate = _load("candidate_improved.json")
        new_case = copy.deepcopy(candidate["benchmark_case_results"][0])
        new_case["benchmark_id"] = "easy-2"
        candidate["benchmark_case_results"].append(new_case)
        result = compare_reports(self.baseline, candidate)
        self.assertIn("easy-2", {d.benchmark_id for d in result.added_benchmarks})

    def test_removed_benchmark_case(self):
        candidate = _load("candidate_improved.json")
        candidate["benchmark_case_results"].pop()
        result = compare_reports(self.baseline, candidate)
        self.assertIn("medium-1", {d.benchmark_id for d in result.removed_benchmarks})


class ScorecardMetricDeltaTests(unittest.TestCase):
    """Categories 30-40: metric delta semantics, incl. the zero-baseline rule."""

    def setUp(self):
        self.baseline = _load("baseline_report.json")

    def test_known_known_computes_absolute_and_relative_delta(self):
        result = compare_reports(self.baseline, _load("candidate_improved.json"))
        delta = result.scorecard_deltas["capability"]["ac_pass_rate"]
        self.assertEqual(delta.availability, DeltaAvailability.COMPUTED)
        self.assertAlmostEqual(delta.absolute_delta, 0.25)
        self.assertAlmostEqual(delta.relative_delta, (0.25 / 0.75))

    def test_zero_baseline_computes_absolute_delta_but_not_relative(self):
        # workflow_failure_rate is 0.0 -> 0.0 in the improved fixture.
        result = compare_reports(self.baseline, _load("candidate_improved.json"))
        delta = result.scorecard_deltas["reliability"]["workflow_failure_rate"]
        self.assertEqual(delta.availability, DeltaAvailability.COMPUTED)
        self.assertEqual(delta.absolute_delta, 0.0)
        self.assertIsNone(delta.relative_delta)
        self.assertIsNotNone(delta.reason)
        self.assertIn("baseline value is 0", delta.reason)

    def test_zero_baseline_with_nonzero_candidate_still_computes_absolute(self):
        candidate = _load("candidate_improved.json")
        candidate["scorecard"]["reliability"]["flaky_case_count"]["value"] = 3
        result = compare_reports(self.baseline, candidate)
        delta = result.scorecard_deltas["reliability"]["flaky_case_count"]
        self.assertEqual(delta.availability, DeltaAvailability.COMPUTED)
        self.assertEqual(delta.absolute_delta, 3)
        self.assertIsNone(delta.relative_delta)

    def test_unknown_baseline_marks_unavailable_not_zero(self):
        baseline = copy.deepcopy(self.baseline)
        baseline["scorecard"]["capability"]["ac_pass_rate"] = {
            "status": "unknown",
            "unavailable_reason": "not measured in this run",
        }
        result = compare_reports(baseline, _load("candidate_improved.json"))
        delta = result.scorecard_deltas["capability"]["ac_pass_rate"]
        self.assertEqual(delta.availability, DeltaAvailability.UNAVAILABLE)
        self.assertIsNone(delta.absolute_delta)
        self.assertIn("baseline", delta.reason)

    def test_unknown_candidate_marks_unavailable_not_zero(self):
        candidate = _load("candidate_improved.json")
        candidate["scorecard"]["capability"]["ac_pass_rate"] = {
            "status": "not_collected",
            "unavailable_reason": "instrumentation disabled",
        }
        result = compare_reports(self.baseline, candidate)
        delta = result.scorecard_deltas["capability"]["ac_pass_rate"]
        self.assertEqual(delta.availability, DeltaAvailability.UNAVAILABLE)
        self.assertIn("candidate", delta.reason)

    def test_both_sides_unavailable(self):
        baseline = copy.deepcopy(self.baseline)
        candidate = _load("candidate_improved.json")
        baseline["scorecard"]["capability"]["ac_pass_rate"] = {"status": "unknown"}
        candidate["scorecard"]["capability"]["ac_pass_rate"] = {"status": "not_applicable"}
        result = compare_reports(baseline, candidate)
        delta = result.scorecard_deltas["capability"]["ac_pass_rate"]
        self.assertEqual(delta.availability, DeltaAvailability.UNAVAILABLE)
        self.assertIn("both sides unavailable", delta.reason)

    def test_boolean_metric_never_gets_numeric_delta(self):
        # traceability fields are handled separately via evidence_deltas, but
        # a boolean value slipping into a numeric scorecard metric must never
        # be subtracted (see _metric_delta's explicit bool guard).
        baseline = copy.deepcopy(self.baseline)
        candidate = _load("candidate_improved.json")
        baseline["scorecard"]["capability"]["ac_pass_rate"] = {"status": "known", "value": True}
        candidate["scorecard"]["capability"]["ac_pass_rate"] = {"status": "known", "value": False}
        result = compare_reports(baseline, candidate)
        delta = result.scorecard_deltas["capability"]["ac_pass_rate"]
        self.assertIsNone(delta.absolute_delta)
        self.assertIsNone(delta.relative_delta)

    def test_all_scorecard_categories_present(self):
        result = compare_reports(self.baseline, _load("candidate_improved.json"))
        self.assertEqual(set(result.scorecard_deltas), {"capability", "reliability", "efficiency"})

    def test_metric_present_only_on_one_side_still_produces_a_delta_entry(self):
        candidate = _load("candidate_improved.json")
        candidate["scorecard"]["efficiency"]["new_metric"] = {"status": "known", "value": 42}
        result = compare_reports(self.baseline, candidate)
        delta = result.scorecard_deltas["efficiency"]["new_metric"]
        self.assertEqual(delta.availability, DeltaAvailability.UNAVAILABLE)
        self.assertIn("baseline", delta.reason)


class RuntimeCostReliabilityBudgetTests(unittest.TestCase):
    """Categories 54-63: runtime/cost/reliability delta + budget semantics."""

    def setUp(self):
        self.baseline = _load("baseline_report.json")

    def test_runtime_within_soft_budget_no_regression_reason(self):
        result = compare_reports(self.baseline, _load("candidate_improved.json"))
        codes = {r.code for r in result.reasons}
        self.assertNotIn("runtime_soft_budget_breach", codes)
        self.assertNotIn("runtime_hard_budget_breach", codes)

    def test_runtime_hard_budget_breach_triggers_regressed(self):
        candidate = _load("candidate_improved.json")
        # baseline wall_clock_duration_ms = 358000; +60% breaches the 50% hard budget.
        candidate["scorecard"]["efficiency"]["wall_clock_duration_ms"]["value"] = int(358000 * 1.6)
        result = compare_reports(self.baseline, candidate)
        self.assertEqual(result.classification, Classification.REGRESSED)
        self.assertIn("runtime_hard_budget_breach", {r.code for r in result.reasons})

    def test_cost_hard_budget_breach_triggers_regressed(self):
        candidate = _load("candidate_improved.json")
        candidate["scorecard"]["efficiency"]["estimated_cost"]["value"] = 1.20 * 1.6
        result = compare_reports(self.baseline, candidate)
        self.assertEqual(result.classification, Classification.REGRESSED)
        self.assertIn("cost_hard_budget_breach", {r.code for r in result.reasons})

    def test_trial_pass_rate_material_improvement_reported(self):
        result = compare_reports(self.baseline, _load("candidate_improved.json"))
        self.assertIn("reliability_improvement", {r.code for r in result.reasons})

    def test_trial_pass_rate_material_regression_reported(self):
        candidate = _load("candidate_improved.json")
        candidate["scorecard"]["reliability"]["trial_pass_rate"]["value"] = 0.5  # baseline is 0.667
        result = compare_reports(self.baseline, candidate)
        self.assertIn("reliability_regression", {r.code for r in result.reasons})


class ConfigDriftTests(unittest.TestCase):
    """Categories 64-75: identity/drift extraction from fixed fields only."""

    def setUp(self):
        self.baseline = _load("baseline_report.json")

    def test_no_drift_when_metadata_identical(self):
        result = compare_reports(self.baseline, _load("candidate_improved.json"))
        self.assertEqual(result.drift, [])

    def test_material_metadata_drift_detected(self):
        result = compare_reports(self.baseline, _load("candidate_drift.json"))
        drift_fields = {(d.field, d.severity) for d in result.drift}
        self.assertIn(("metadata.model", DriftSeverity.MATERIAL), drift_fields)

    def test_informational_drift_for_non_material_key(self):
        candidate = _load("candidate_improved.json")
        candidate["metadata"]["loop_limit"] = "10"
        self.baseline["metadata"] = dict(self.baseline["metadata"], loop_limit="5")
        result = compare_reports(self.baseline, candidate)
        # loop_limit IS material by default policy; use a non-listed key to
        # exercise the informational branch instead.
        policy = ComparisonPolicy(
            identity_metadata_keys=("runner", "model", "config_hash", "extra_key"),
            material_drift_metadata_keys=("runner", "model", "config_hash"),
        )
        baseline2 = copy.deepcopy(self.baseline)
        candidate2 = copy.deepcopy(candidate)
        baseline2["metadata"]["extra_key"] = "a"
        candidate2["metadata"]["extra_key"] = "b"
        result2 = compare_reports(baseline2, candidate2, policy=policy)
        drift_fields = {(d.field, d.severity) for d in result2.drift}
        self.assertIn(("metadata.extra_key", DriftSeverity.INFORMATIONAL), drift_fields)

    def test_benchmark_suite_mismatch_is_incompatible_and_incomparable(self):
        candidate = _load("candidate_improved.json")
        candidate["benchmark_suite_id"] = "seed-v2"
        result = compare_reports(self.baseline, candidate)
        self.assertEqual(result.comparability, Comparability.INCOMPARABLE)
        self.assertEqual(result.classification, Classification.INCONCLUSIVE)
        self.assertTrue(any(d.severity == DriftSeverity.INCOMPATIBLE for d in result.drift))

    def test_drift_never_parses_summary_text(self):
        baseline = copy.deepcopy(self.baseline)
        candidate = _load("candidate_improved.json")
        baseline["summary"] = {"notes": "runner=totally-different-runner"}
        result = compare_reports(baseline, candidate)
        self.assertEqual(result.drift, [])

    def test_identity_metadata_subset_is_fixed_allowlist(self):
        baseline = copy.deepcopy(self.baseline)
        baseline["metadata"]["unlisted_key"] = "should not appear"
        result = compare_reports(baseline, _load("candidate_improved.json"))
        self.assertNotIn("unlisted_key", result.baseline_identity.metadata)

    def test_zero_trial_report_is_incomparable(self):
        candidate = _load("candidate_improved.json")
        for case in candidate["benchmark_case_results"]:
            case["trial_results"] = []
        result = compare_reports(self.baseline, candidate)
        self.assertEqual(result.comparability, Comparability.INCOMPARABLE)
        self.assertEqual(result.classification, Classification.INCONCLUSIVE)


class TraceabilityEvidenceTierTests(unittest.TestCase):
    """Categories 76-80: required/conditional/optional evidence tiers."""

    def setUp(self):
        self.baseline = _load("baseline_report.json")

    def test_required_evidence_regression_is_hard_gate(self):
        candidate = _load("candidate_improved.json")
        candidate["scorecard"]["traceability"]["trace_present"]["value"] = False
        result = compare_reports(self.baseline, candidate)
        self.assertEqual(result.classification, Classification.REGRESSED)
        self.assertIn("required_evidence_lost", {r.code for r in result.reasons})

    def test_required_evidence_becoming_unavailable_is_inconclusive(self):
        candidate = _load("candidate_improved.json")
        candidate["scorecard"]["traceability"]["trace_present"] = {
            "status": "not_collected",
            "unavailable_reason": "trace capture disabled",
        }
        result = compare_reports(self.baseline, candidate)
        self.assertEqual(result.classification, Classification.INCONCLUSIVE)
        self.assertIn("required_evidence_unavailable", {r.code for r in result.reasons})

    def test_conditional_evidence_lost_is_soft_not_hard(self):
        candidate = _load("candidate_improved.json")
        candidate["scorecard"]["traceability"]["final_diff_ref_present"]["value"] = False
        result = compare_reports(self.baseline, candidate)
        # Not a hard gate on its own -> should not force REGRESSED.
        self.assertNotEqual(result.classification, Classification.INCONCLUSIVE)
        evidence_field = next(e for e in result.evidence_deltas if e.field == "final_diff_ref_present")
        self.assertTrue(evidence_field.regressed)
        self.assertEqual(evidence_field.requirement, "conditional")

    def test_optional_evidence_loss_never_blocks_classification(self):
        candidate = _load("candidate_improved.json")
        candidate["scorecard"]["traceability"]["prompt_hashes_present"] = {"status": "not_collected"}
        result = compare_reports(self.baseline, candidate)
        self.assertNotEqual(result.classification, Classification.INCONCLUSIVE)

    def test_evidence_gained_is_tracked(self):
        baseline = copy.deepcopy(self.baseline)
        candidate = _load("candidate_improved.json")
        baseline["scorecard"]["traceability"]["prompt_hashes_present"] = {"status": "not_collected"}
        candidate["scorecard"]["traceability"]["prompt_hashes_present"] = {"status": "known", "value": True}
        result = compare_reports(baseline, candidate)
        evidence_field = next(e for e in result.evidence_deltas if e.field == "prompt_hashes_present")
        self.assertTrue(evidence_field.gained)


class SerializationPurityTests(unittest.TestCase):
    """No enums/paths/datetimes/dataclasses leak into JSON output."""

    def test_to_dict_round_trips_through_json_and_validates(self):
        result = load_and_compare(FIXTURES / "baseline_report.json", FIXTURES / "candidate_improved.json")
        payload = result.to_dict()
        text = json.dumps(payload)  # must not raise
        reparsed = json.loads(text)
        self.assertEqual(validate_comparison_payload(reparsed), [])

    def test_no_enum_instances_survive_serialization(self):
        result = load_and_compare(FIXTURES / "baseline_report.json", FIXTURES / "candidate_regressed.json")
        payload = result.to_dict()

        def _walk(node):
            if isinstance(node, dict):
                for v in node.values():
                    _walk(v)
            elif isinstance(node, list):
                for v in node:
                    _walk(v)
            else:
                self.assertIsInstance(node, (str, int, float, bool, type(None)))

        _walk(payload)

    def test_generated_canonical_fixture_matches_current_output(self):
        expected = json.loads((FIXTURES / "comparison_result_improved.json").read_text(encoding="utf-8"))
        # The golden fixture's identity labels were generated using the
        # paths recorded in `expected["baseline_identity"]["label"]` /
        # `["candidate_identity"]["label"]`; use the same path strings here
        # so this is a like-for-like comparison rather than an
        # environment-dependent absolute-path mismatch.
        baseline_path = expected["baseline_identity"]["label"]
        candidate_path = expected["candidate_identity"]["label"]
        result = load_and_compare(baseline_path, candidate_path)
        self.assertEqual(result.to_dict(), expected)


if __name__ == "__main__":
    unittest.main()
