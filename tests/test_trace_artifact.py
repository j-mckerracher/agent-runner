"""Tests for TraceArtifact (telemetry/trace_artifact.py)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

FIXED_TS = "2026-07-17T10:00:00.000000Z"


def _make_event_dict(run_id: str = "run-1", event_type: str = "run.started") -> dict:
    return {
        "event_schema_version": "1",
        "event_type": event_type,
        "timestamp": FIXED_TS,
        "run_id": run_id,
    }


def _write_jsonl(path: Path, dicts: list[dict]) -> None:
    lines = [json.dumps(d, sort_keys=True) + "\n" for d in dicts]
    path.write_bytes("".join(lines).encode("utf-8"))


class TestTraceArtifactLoad(unittest.TestCase):
    def test_multi_event_trace(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "trace.jsonl"
            events = [
                _make_event_dict("run-1", "run.started"),
                _make_event_dict("run-1", "run.completed"),
            ]
            _write_jsonl(p, events)
            from telemetry.trace_artifact import TraceArtifact
            art = TraceArtifact.load_with_validation(p)
            self.assertEqual(art.event_count, 2)
            self.assertEqual(art.run_id, "run-1")

    def test_single_event_trace(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "trace.jsonl"
            _write_jsonl(p, [_make_event_dict("run-42")])
            from telemetry.trace_artifact import TraceArtifact
            art = TraceArtifact.load_with_validation(p)
            self.assertEqual(art.event_count, 1)
            self.assertEqual(art.run_id, "run-42")

    def test_empty_file_is_valid_empty_trace(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "trace.jsonl"
            p.write_bytes(b"")
            from telemetry.trace_artifact import TraceArtifact
            art = TraceArtifact.load_with_validation(p)
            self.assertEqual(art.event_count, 0)
            self.assertIsNone(art.run_id)

    def test_missing_file_raises_load_error(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "missing.jsonl"
            from telemetry.trace_artifact import TraceArtifact
            from artifacts.validation import ArtifactLoadError
            with self.assertRaises(ArtifactLoadError):
                TraceArtifact.load_with_validation(p)

    def test_malformed_json_line_raises_with_line_number(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "trace.jsonl"
            lines = [
                json.dumps(_make_event_dict("run-1")) + "\n",
                "{ not json\n",
            ]
            p.write_bytes("".join(lines).encode("utf-8"))
            from telemetry.trace_artifact import TraceArtifact
            from artifacts.validation import ArtifactValidationError
            with self.assertRaises(ArtifactValidationError) as ctx:
                TraceArtifact.load_with_validation(p)
            self.assertIn("2", str(ctx.exception))

    def test_invalid_event_line_raises_with_line_number(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "trace.jsonl"
            bad_event = {"event_schema_version": "1", "event_type": "bogus.type",
                         "timestamp": FIXED_TS, "run_id": "run-1"}
            _write_jsonl(p, [_make_event_dict(), bad_event])
            from telemetry.trace_artifact import TraceArtifact
            from artifacts.validation import ArtifactValidationError
            with self.assertRaises(ArtifactValidationError) as ctx:
                TraceArtifact.load_with_validation(p)
            self.assertIn("2", str(ctx.exception))

    def test_incompatible_run_ids_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "trace.jsonl"
            events = [
                _make_event_dict("run-A"),
                _make_event_dict("run-B"),
            ]
            _write_jsonl(p, events)
            from telemetry.trace_artifact import TraceArtifact
            from artifacts.validation import ArtifactValidationError
            with self.assertRaises(ArtifactValidationError) as ctx:
                TraceArtifact.load_with_validation(p)
            self.assertIn("run_id", str(ctx.exception))

    def test_source_unchanged_after_load(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "trace.jsonl"
            original = (json.dumps(_make_event_dict(), sort_keys=True) + "\n").encode("utf-8")
            p.write_bytes(original)
            from telemetry.trace_artifact import TraceArtifact
            TraceArtifact.load_with_validation(p)
            self.assertEqual(p.read_bytes(), original)

    def test_blank_lines_are_skipped(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "trace.jsonl"
            content = (
                json.dumps(_make_event_dict(), sort_keys=True) + "\n"
                "\n"
                "   \n"
                + json.dumps(_make_event_dict(event_type="run.completed"), sort_keys=True) + "\n"
            )
            p.write_bytes(content.encode("utf-8"))
            from telemetry.trace_artifact import TraceArtifact
            art = TraceArtifact.load_with_validation(p)
            self.assertEqual(art.event_count, 2)


class TestTraceArtifactRoundTrip(unittest.TestCase):
    def test_to_jsonl_round_trip(self):
        """to_jsonl() must reproduce exactly what JsonlEventSink writes."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "trace.jsonl"
            events = [
                _make_event_dict("run-1", "run.started"),
                _make_event_dict("run-1", "run.completed"),
            ]
            original_bytes = "".join(
                json.dumps(d, sort_keys=True) + "\n" for d in events
            ).encode("utf-8")
            p.write_bytes(original_bytes)
            from telemetry.trace_artifact import TraceArtifact
            art = TraceArtifact.load_with_validation(p)
            self.assertEqual(art.to_jsonl().encode("utf-8"), original_bytes)

    def test_empty_trace_to_jsonl(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "trace.jsonl"
            p.write_bytes(b"")
            from telemetry.trace_artifact import TraceArtifact
            art = TraceArtifact.load_with_validation(p)
            self.assertEqual(art.to_jsonl(), "")


class TestTraceArtifactRef(unittest.TestCase):
    def _load(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "trace.jsonl"
            _write_jsonl(p, [_make_event_dict("run-99")])
            from telemetry.trace_artifact import TraceArtifact
            return TraceArtifact.load_with_validation(p), p

    def test_to_artifact_ref_path(self):
        from artifacts import ArtifactRef
        art, p = self._load()
        ref = art.to_artifact_ref(path=str(p))
        self.assertIsInstance(ref, ArtifactRef)
        self.assertEqual(ref.artifact_type, "trace")

    def test_metadata_run_id_and_event_count(self):
        art, p = self._load()
        ref = art.to_artifact_ref(path=str(p))
        self.assertEqual(ref.metadata["run_id"], "run-99")
        self.assertEqual(ref.metadata["event_count"], 1)

    def test_checksum_passthrough(self):
        art, p = self._load()
        cs = "c" * 64
        ref = art.to_artifact_ref(path=str(p), checksum_sha256=cs)
        self.assertEqual(ref.checksum_sha256, cs)

    def test_schema_metadata(self):
        from telemetry.trace_artifact import TraceArtifact
        art, p = self._load()
        ref = art.to_artifact_ref(path=str(p))
        self.assertEqual(ref.artifact_schema, TraceArtifact.ARTIFACT_SCHEMA)
        self.assertEqual(ref.artifact_schema_version, TraceArtifact.ARTIFACT_SCHEMA_VERSION)
        self.assertEqual(ref.producer_stage, TraceArtifact.PRODUCER_STAGE)


if __name__ == "__main__":
    unittest.main()
