"""Tests for FinalDiffArtifact (artifacts/evidence.py)."""

from __future__ import annotations

import unittest
from pathlib import Path


class TestFinalDiffArtifactLoad(unittest.TestCase):
    def test_nonempty_diff_loads(self, tmp_path=None):
        import tempfile, os
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "final.diff"
            content = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
            p.write_bytes(content.encode("utf-8"))
            from artifacts import FinalDiffArtifact
            art = FinalDiffArtifact.load(p)
            self.assertEqual(art.text, content)

    def test_empty_diff_is_valid(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "final.diff"
            p.write_bytes(b"")
            from artifacts import FinalDiffArtifact
            art = FinalDiffArtifact.load(p)
            self.assertEqual(art.text, "")
            self.assertTrue(art.is_empty)

    def test_missing_file_raises_load_error(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "missing.diff"
            from artifacts import FinalDiffArtifact
            from artifacts.validation import ArtifactLoadError
            with self.assertRaises(ArtifactLoadError) as ctx:
                FinalDiffArtifact.load(p)
            self.assertIn(str(p), str(ctx.exception))

    def test_invalid_utf8_raises_load_error(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "final.diff"
            p.write_bytes(b"\xff\xfe invalid bytes")
            from artifacts import FinalDiffArtifact
            from artifacts.validation import ArtifactLoadError
            with self.assertRaises(ArtifactLoadError):
                FinalDiffArtifact.load(p)

    def test_exact_byte_preservation(self):
        """Line endings (CRLF, LF) must be preserved exactly — no normalization."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "final.diff"
            content = "line1\r\nline2\nline3\r\n"
            p.write_bytes(content.encode("utf-8"))
            from artifacts import FinalDiffArtifact
            art = FinalDiffArtifact.load(p)
            self.assertEqual(art.text, content)

    def test_load_with_validation_is_alias_for_load(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "final.diff"
            p.write_bytes(b"diff text")
            from artifacts import FinalDiffArtifact
            a1 = FinalDiffArtifact.load(p)
            a2 = FinalDiffArtifact.load_with_validation(p)
            self.assertEqual(a1, a2)

    def test_read_only_load(self):
        """load() must not write any bytes — source file unchanged after load."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "final.diff"
            original = b"diff --git a/f b/f\n--- a/f\n+++ b/f\n"
            p.write_bytes(original)
            from artifacts import FinalDiffArtifact
            FinalDiffArtifact.load(p)
            self.assertEqual(p.read_bytes(), original)


class TestFinalDiffArtifactMetadata(unittest.TestCase):
    def _make(self, text: str):
        from artifacts import FinalDiffArtifact
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "final.diff"
            p.write_bytes(text.encode("utf-8"))
            return FinalDiffArtifact.load(p)

    def test_char_length(self):
        art = self._make("abc\n")
        self.assertEqual(art.char_length, 4)

    def test_byte_length_ascii(self):
        art = self._make("hello\n")
        self.assertEqual(art.byte_length, 6)

    def test_byte_length_multibyte(self):
        text = "\u00e9"  # é — 2 bytes in UTF-8
        art = self._make(text)
        self.assertEqual(art.char_length, 1)
        self.assertEqual(art.byte_length, 2)

    def test_is_empty_false_for_nonempty(self):
        art = self._make("x")
        self.assertFalse(art.is_empty)

    def test_is_empty_true_for_empty(self):
        art = self._make("")
        self.assertTrue(art.is_empty)


class TestFinalDiffArtifactRef(unittest.TestCase):
    def _load(self, text: str = "diff\n"):
        from artifacts import FinalDiffArtifact
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "final.diff"
            p.write_bytes(text.encode("utf-8"))
            return FinalDiffArtifact.load(p), p

    def test_to_artifact_ref_path(self):
        from artifacts import ArtifactRef
        art, p = self._load("diff text\n")
        ref = art.to_artifact_ref(path=str(p))
        self.assertIsInstance(ref, ArtifactRef)
        self.assertEqual(ref.artifact_type, "final_diff")
        self.assertIsNotNone(ref.path)

    def test_to_artifact_ref_uri(self):
        art, _ = self._load()
        ref = art.to_artifact_ref(uri="s3://bucket/final.diff")
        self.assertEqual(ref.uri, "s3://bucket/final.diff")

    def test_to_artifact_ref_metadata_contains_lengths_and_empty(self):
        art, p = self._load("abc\n")
        ref = art.to_artifact_ref(path=str(p))
        self.assertIn("char_length", ref.metadata)
        self.assertIn("byte_length", ref.metadata)
        self.assertIn("is_empty", ref.metadata)
        self.assertEqual(ref.metadata["char_length"], 4)
        self.assertFalse(ref.metadata["is_empty"])

    def test_to_artifact_ref_with_checksum(self):
        art, p = self._load()
        checksum = "a" * 64
        ref = art.to_artifact_ref(path=str(p), checksum_sha256=checksum)
        self.assertEqual(ref.checksum_sha256, checksum)

    def test_to_artifact_ref_schema_metadata(self):
        from artifacts.evidence import FinalDiffArtifact
        art, p = self._load()
        ref = art.to_artifact_ref(path=str(p))
        self.assertEqual(ref.artifact_schema, FinalDiffArtifact.ARTIFACT_SCHEMA)
        self.assertEqual(ref.artifact_schema_version, FinalDiffArtifact.ARTIFACT_SCHEMA_VERSION)
        self.assertEqual(ref.producer_stage, FinalDiffArtifact.PRODUCER_STAGE)


if __name__ == "__main__":
    unittest.main()
