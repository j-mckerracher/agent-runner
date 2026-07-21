"""Tests for eval/evidence_artifacts.py — registry."""

from __future__ import annotations

import unittest


class TestEvidenceArtifactsRegistry(unittest.TestCase):
    def test_registry_contains_three_adapters(self):
        from eval.evidence_artifacts import EVIDENCE_ARTIFACTS
        self.assertEqual(len(EVIDENCE_ARTIFACTS), 3)

    def test_registry_contains_eval_report_artifact(self):
        from eval.evidence_artifacts import EVIDENCE_ARTIFACTS, EvalReportArtifact
        self.assertIn(EvalReportArtifact, EVIDENCE_ARTIFACTS)

    def test_registry_contains_trace_artifact(self):
        from eval.evidence_artifacts import EVIDENCE_ARTIFACTS, TraceArtifact
        self.assertIn(TraceArtifact, EVIDENCE_ARTIFACTS)

    def test_registry_contains_final_diff_artifact(self):
        from eval.evidence_artifacts import EVIDENCE_ARTIFACTS, FinalDiffArtifact
        self.assertIn(FinalDiffArtifact, EVIDENCE_ARTIFACTS)

    def test_artifact_types_are_unique(self):
        from eval.evidence_artifacts import EVIDENCE_ARTIFACTS
        types = [cls.ARTIFACT_TYPE for cls in EVIDENCE_ARTIFACTS]
        self.assertEqual(len(types), len(set(types)))

    def test_artifact_types_match_expected_values(self):
        from eval.evidence_artifacts import EVIDENCE_ARTIFACTS
        type_set = {cls.ARTIFACT_TYPE for cls in EVIDENCE_ARTIFACTS}
        self.assertIn("eval_report", type_set)
        self.assertIn("trace", type_set)
        self.assertIn("final_diff", type_set)

    def test_all_have_load_with_validation(self):
        from eval.evidence_artifacts import EVIDENCE_ARTIFACTS
        for cls in EVIDENCE_ARTIFACTS:
            self.assertTrue(
                callable(getattr(cls, "load_with_validation", None)),
                f"{cls.__name__} missing load_with_validation",
            )

    def test_all_have_to_artifact_ref(self):
        from eval.evidence_artifacts import EVIDENCE_ARTIFACTS
        for cls in EVIDENCE_ARTIFACTS:
            self.assertTrue(
                callable(getattr(cls, "to_artifact_ref", None)),
                f"{cls.__name__} missing to_artifact_ref",
            )

    def test_re_exports_match_direct_imports(self):
        from eval.evidence_artifacts import (
            EvalReportArtifact as A,
            FinalDiffArtifact as B,
            TraceArtifact as C,
        )
        from eval.report_artifact import EvalReportArtifact
        from artifacts.evidence import FinalDiffArtifact
        from telemetry.trace_artifact import TraceArtifact
        self.assertIs(A, EvalReportArtifact)
        self.assertIs(B, FinalDiffArtifact)
        self.assertIs(C, TraceArtifact)

    def test_registry_does_not_overlap_planning_artifacts(self):
        from eval.evidence_artifacts import EVIDENCE_ARTIFACTS
        from artifacts.payloads import PLANNING_ARTIFACTS
        evidence_types = {cls.ARTIFACT_TYPE for cls in EVIDENCE_ARTIFACTS}
        planning_types = {cls.ARTIFACT_TYPE for cls in PLANNING_ARTIFACTS}
        self.assertFalse(evidence_types & planning_types)

    def test_registry_does_not_overlap_report_artifacts(self):
        from eval.evidence_artifacts import EVIDENCE_ARTIFACTS
        from artifacts.payloads import REPORT_ARTIFACTS
        evidence_types = {cls.ARTIFACT_TYPE for cls in EVIDENCE_ARTIFACTS}
        report_types = {cls.ARTIFACT_TYPE for cls in REPORT_ARTIFACTS}
        self.assertFalse(evidence_types & report_types)


if __name__ == "__main__":
    unittest.main()
