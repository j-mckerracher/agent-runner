"""Tests for eval/evidence_paths.py — centralized path contract."""

from __future__ import annotations

import hashlib
import unittest
from pathlib import Path


class TestSanitizePathSegment(unittest.TestCase):
    def _san(self, raw: str) -> str:
        from eval.evidence_paths import _sanitize_path_segment
        return _sanitize_path_segment(raw)

    def test_safe_chars_preserved(self):
        seg = self._san("abc-123_v1.0")
        self.assertTrue(seg.startswith("abc-123_v1.0-"))

    def test_unsafe_chars_replaced_with_dash(self):
        seg = self._san("run/foo:bar")
        # slashes and colons must not appear in the cleaned prefix
        prefix = seg.rsplit("-", 1)[0]
        self.assertNotIn("/", prefix)
        self.assertNotIn(":", prefix)

    def test_always_appends_8char_hash_suffix(self):
        seg = self._san("anything")
        parts = seg.rsplit("-", 1)
        self.assertEqual(len(parts), 2)
        self.assertEqual(len(parts[1]), 8)

    def test_hash_suffix_is_sha256(self):
        raw = "test-id"
        expected_digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
        seg = self._san(raw)
        self.assertTrue(seg.endswith(f"-{expected_digest}"))

    def test_empty_string_becomes_unknown(self):
        seg = self._san("")
        self.assertTrue(seg.startswith("unknown-"))

    def test_collision_disambiguation(self):
        """Two different raw strings that map to the same cleaned prefix
        must still produce different path segments due to the hash suffix."""
        seg_a = self._san("foo/bar")
        seg_b = self._san("foo-bar")
        self.assertNotEqual(seg_a, seg_b)

    def test_no_traversal_escape(self):
        seg = self._san("../../../etc/passwd")
        self.assertNotIn("..", seg)
        self.assertNotIn("/", seg)


class TestTrialEvidencePaths(unittest.TestCase):
    def test_exact_dir_layout(self):
        from eval.evidence_paths import TrialEvidencePaths, _sanitize_path_segment
        reports = Path("/tmp/reports")
        paths = TrialEvidencePaths.for_trial(reports, "run-1", "medium", "trial-1")
        expected = (
            reports
            / "evidence"
            / _sanitize_path_segment("run-1")
            / _sanitize_path_segment("medium")
            / _sanitize_path_segment("trial-1")
        )
        self.assertEqual(paths.dir, expected)

    def test_seven_filename_properties(self):
        from eval.evidence_paths import (
            TrialEvidencePaths,
            STORY_JSON,
            WORKFLOW_STDOUT_LOG,
            WORKFLOW_STDERR_LOG,
            WORKFLOW_RESULT_JSON,
            HIDDEN_TESTS_XML,
            FINAL_DIFF,
            TRACE_JSONL,
        )
        paths = TrialEvidencePaths.for_trial("/r", "e", "b", "t")
        self.assertEqual(paths.story_json, paths.dir / STORY_JSON)
        self.assertEqual(paths.workflow_stdout_log, paths.dir / WORKFLOW_STDOUT_LOG)
        self.assertEqual(paths.workflow_stderr_log, paths.dir / WORKFLOW_STDERR_LOG)
        self.assertEqual(paths.workflow_result_json, paths.dir / WORKFLOW_RESULT_JSON)
        self.assertEqual(paths.hidden_tests_xml, paths.dir / HIDDEN_TESTS_XML)
        self.assertEqual(paths.final_diff, paths.dir / FINAL_DIFF)
        self.assertEqual(paths.trace_jsonl, paths.dir / TRACE_JSONL)

    def test_pure_no_mkdir(self):
        """for_trial() must not create any directories."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "reports"
            from eval.evidence_paths import TrialEvidencePaths
            paths = TrialEvidencePaths.for_trial(base, "e", "b", "t")
            self.assertFalse(paths.dir.exists())

    def test_ensure_dir_creates_directory(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "reports"
            from eval.evidence_paths import TrialEvidencePaths
            paths = TrialEvidencePaths.for_trial(base, "e", "b", "t")
            returned = paths.ensure_dir()
            self.assertTrue(paths.dir.exists())
            self.assertEqual(returned, paths.dir)

    def test_no_traversal_escape_from_report_dir(self):
        """Sanitization must prevent directory traversal via .. sequences.
        The path stays under reports_dir/evidence/ even when the raw id
        contains path-separator characters."""
        from eval.evidence_paths import TrialEvidencePaths
        paths = TrialEvidencePaths.for_trial(
            "/tmp/reports", "../../../etc", "passwd", "trial"
        )
        path_str = str(paths.dir)
        # No traversal sequences in any path segment.
        self.assertNotIn("..", path_str)
        self.assertNotIn("//", path_str)
        # Path must still start under the expected evidence tree.
        self.assertTrue(path_str.startswith("/tmp/reports/evidence/"))


class TestReportPathHelpers(unittest.TestCase):
    def test_report_stamp(self):
        from eval.evidence_paths import report_stamp
        stamp = report_stamp("2026-07-17T12:34:56.123456Z")
        self.assertEqual(stamp, "20260717T123456.123456Z")

    def test_timestamped_report_path(self):
        from eval.evidence_paths import timestamped_report_path
        p = timestamped_report_path("/reports", "20260717T120000Z", "medium")
        self.assertEqual(p, Path("/reports/20260717T120000Z-medium.json"))

    def test_latest_report_path(self):
        from eval.evidence_paths import latest_report_path
        p = latest_report_path("/reports")
        self.assertEqual(p, Path("/reports/latest.json"))


class TestRunnerUsesHelper(unittest.TestCase):
    """Verify _evidence_dir in eval/runner.py builds paths via TrialEvidencePaths."""

    def test_evidence_dir_path_matches_trial_evidence_paths(self):
        import argparse, tempfile
        with tempfile.TemporaryDirectory() as td:
            args = argparse.Namespace(reports_dir=td, keep_sandbox=False)
            from eval.runner import _evidence_dir
            from eval.evidence_paths import TrialEvidencePaths
            result = _evidence_dir(args, "run-1", "bench-1", "trial-1")
            expected = TrialEvidencePaths.for_trial(td, "run-1", "bench-1", "trial-1").dir
            self.assertEqual(result, expected)

    def test_evidence_dir_creates_directory(self):
        import argparse, tempfile
        with tempfile.TemporaryDirectory() as td:
            args = argparse.Namespace(reports_dir=td, keep_sandbox=False)
            from eval.runner import _evidence_dir
            result = _evidence_dir(args, "run-1", "bench-1", "trial-1")
            self.assertIsNotNone(result)
            self.assertTrue(result.exists())

    def test_str_path_refs_unchanged(self):
        """The str() of emitted path refs must be identical to what they would
        have been with the old inline construction."""
        import argparse, tempfile
        with tempfile.TemporaryDirectory() as td:
            args = argparse.Namespace(reports_dir=td, keep_sandbox=False)
            from eval.runner import _evidence_dir
            from eval.evidence_paths import (
                TrialEvidencePaths,
                STORY_JSON,
                TRACE_JSONL,
            )
            result = _evidence_dir(args, "run-1", "bench-1", "trial-1")
            expected_paths = TrialEvidencePaths.for_trial(td, "run-1", "bench-1", "trial-1")
            self.assertEqual(str(result / STORY_JSON), str(expected_paths.story_json))
            self.assertEqual(str(result / TRACE_JSONL), str(expected_paths.trace_jsonl))


if __name__ == "__main__":
    unittest.main()
