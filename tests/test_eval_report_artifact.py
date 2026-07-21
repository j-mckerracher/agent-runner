"""Tests for EvalReportArtifact (eval/report_artifact.py)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path


FIXTURE = Path(__file__).parent / "fixtures" / "eval" / "v02_report.json"


class TestEvalReportArtifactLoad(unittest.TestCase):
    def test_valid_v02_report_loads(self):
        from eval.report_artifact import EvalReportArtifact
        art = EvalReportArtifact.load_with_validation(FIXTURE)
        self.assertEqual(art.report.report_schema_version, "0.2")

    def test_read_only(self):
        """Source file must be unchanged after loading."""
        original = FIXTURE.read_bytes()
        from eval.report_artifact import EvalReportArtifact
        EvalReportArtifact.load_with_validation(FIXTURE)
        self.assertEqual(FIXTURE.read_bytes(), original)

    def test_missing_file_raises_load_error(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "missing.json"
            from eval.report_artifact import EvalReportArtifact
            from artifacts.validation import ArtifactLoadError
            with self.assertRaises(ArtifactLoadError):
                EvalReportArtifact.load_with_validation(p)

    def test_malformed_json_raises_load_error(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "report.json"
            p.write_bytes(b"{not valid json")
            from eval.report_artifact import EvalReportArtifact
            from artifacts.validation import ArtifactLoadError
            with self.assertRaises(ArtifactLoadError):
                EvalReportArtifact.load_with_validation(p)

    def test_wrong_schema_version_raises_validation_error(self):
        import tempfile
        data = json.loads(FIXTURE.read_bytes())
        data["report_schema_version"] = "0.99"
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "report.json"
            p.write_bytes(json.dumps(data).encode("utf-8"))
            from eval.report_artifact import EvalReportArtifact
            from artifacts.validation import ArtifactValidationError
            with self.assertRaises(ArtifactValidationError):
                EvalReportArtifact.load_with_validation(p)

    def test_structurally_invalid_report_raises_validation_error(self):
        import tempfile
        # Remove required field.
        data = json.loads(FIXTURE.read_bytes())
        del data["eval_run_id"]
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "report.json"
            p.write_bytes(json.dumps(data).encode("utf-8"))
            from eval.report_artifact import EvalReportArtifact
            from artifacts.validation import ArtifactValidationError
            with self.assertRaises(ArtifactValidationError):
                EvalReportArtifact.load_with_validation(p)

    def test_round_trip(self):
        """to_dict / to_json must not change the report content."""
        from eval.report_artifact import EvalReportArtifact
        art = EvalReportArtifact.load_with_validation(FIXTURE)
        original_dict = json.loads(FIXTURE.read_bytes())
        round_tripped = art.to_dict()
        # Core identity fields must survive the round trip.
        self.assertEqual(round_tripped["eval_run_id"], original_dict["eval_run_id"])
        self.assertEqual(round_tripped["report_schema_version"], original_dict["report_schema_version"])

    def test_to_json_returns_string(self):
        from eval.report_artifact import EvalReportArtifact
        art = EvalReportArtifact.load_with_validation(FIXTURE)
        out = art.to_json()
        self.assertIsInstance(out, str)
        parsed = json.loads(out)
        self.assertEqual(parsed["eval_run_id"], art.report.eval_run_id)

    def test_source_unchanged_after_round_trip(self):
        original = FIXTURE.read_bytes()
        from eval.report_artifact import EvalReportArtifact
        art = EvalReportArtifact.load_with_validation(FIXTURE)
        art.to_dict()
        art.to_json()
        self.assertEqual(FIXTURE.read_bytes(), original)


class TestEvalReportArtifactRef(unittest.TestCase):
    def test_to_artifact_ref_path(self):
        from eval.report_artifact import EvalReportArtifact
        from artifacts import ArtifactRef
        art = EvalReportArtifact.load_with_validation(FIXTURE)
        ref = art.to_artifact_ref(path=str(FIXTURE))
        self.assertIsInstance(ref, ArtifactRef)
        self.assertEqual(ref.artifact_type, "eval_report")

    def test_identity_metadata_from_report(self):
        from eval.report_artifact import EvalReportArtifact
        art = EvalReportArtifact.load_with_validation(FIXTURE)
        ref = art.to_artifact_ref(path=str(FIXTURE))
        self.assertEqual(ref.metadata["eval_run_id"], art.report.eval_run_id)
        self.assertEqual(ref.metadata["benchmark_suite_id"], art.report.benchmark_suite_id)
        self.assertEqual(ref.metadata["candidate_version"], art.report.candidate_version)

    def test_identity_metadata_can_be_overridden(self):
        from eval.report_artifact import EvalReportArtifact
        art = EvalReportArtifact.load_with_validation(FIXTURE)
        ref = art.to_artifact_ref(
            path=str(FIXTURE),
            eval_run_id="custom-run",
            benchmark_suite_id="custom-suite",
            candidate_version="v9.9",
        )
        self.assertEqual(ref.metadata["eval_run_id"], "custom-run")
        self.assertEqual(ref.metadata["benchmark_suite_id"], "custom-suite")
        self.assertEqual(ref.metadata["candidate_version"], "v9.9")

    def test_checksum_passthrough(self):
        from eval.report_artifact import EvalReportArtifact
        art = EvalReportArtifact.load_with_validation(FIXTURE)
        cs = "b" * 64
        ref = art.to_artifact_ref(path=str(FIXTURE), checksum_sha256=cs)
        self.assertEqual(ref.checksum_sha256, cs)

    def test_schema_metadata(self):
        from eval.report_artifact import EvalReportArtifact
        art = EvalReportArtifact.load_with_validation(FIXTURE)
        ref = art.to_artifact_ref(path=str(FIXTURE))
        self.assertEqual(ref.artifact_schema, EvalReportArtifact.ARTIFACT_SCHEMA)
        self.assertEqual(ref.artifact_schema_version, EvalReportArtifact.ARTIFACT_SCHEMA_VERSION)
        self.assertEqual(ref.producer_stage, EvalReportArtifact.PRODUCER_STAGE)


if __name__ == "__main__":
    unittest.main()
