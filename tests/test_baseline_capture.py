import json
import tempfile
import unittest
from pathlib import Path

from core.file_hash import UNAVAILABLE_MISSING, hash_file
from scripts.capture_v01_baseline import (
    UNAVAILABLE,
    build_manifest,
    discover_benchmarks,
    inventory_dir,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class FileHashTests(unittest.TestCase):
    def test_easy__hash_file_is_stable_for_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "sample.txt"
            file_path.write_text("hello baseline", encoding="utf-8")
            first = hash_file(file_path)
            second = hash_file(file_path)
            self.assertEqual(first, second)
            self.assertNotEqual(first, UNAVAILABLE_MISSING)
            self.assertEqual(len(first), 64)

    def test_easy__hash_file_changes_with_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "sample.txt"
            file_path.write_text("version-a", encoding="utf-8")
            hash_a = hash_file(file_path)
            file_path.write_text("version-b", encoding="utf-8")
            hash_b = hash_file(file_path)
            self.assertNotEqual(hash_a, hash_b)

    def test_medium__hash_file_reports_missing_marker_for_absent_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing_path = Path(tmp) / "does-not-exist.txt"
            self.assertEqual(hash_file(missing_path), UNAVAILABLE_MISSING)


class BenchmarkDiscoveryTests(unittest.TestCase):
    def _build_fake_benchmarks(self, root: Path) -> None:
        _write(root / "eval/benchmarks/easy/story.json", json.dumps({"metadata": {"difficulty": "easy"}}))
        _write(root / "eval/benchmarks/easy/hidden_tests.py", "def test_ac1(): pass\n")

        _write(root / "eval/benchmarks/medium/story.json", json.dumps({"metadata": {"difficulty": "medium"}}))
        # medium has no hidden_tests.py on purpose

        _write(root / "eval/benchmarks/hard/hidden_tests.py", "def test_ac1(): pass\n")
        # hard has no story.json on purpose

    def test_medium__discovers_easy_medium_hard_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._build_fake_benchmarks(root)

            cases = discover_benchmarks(root)
            by_id = {case["benchmark_id"]: case for case in cases}

            self.assertEqual(set(by_id), {"easy", "medium", "hard"})

            self.assertEqual(by_id["easy"]["difficulty"], "easy")
            self.assertTrue(by_id["easy"]["story_json_present"])
            self.assertTrue(by_id["easy"]["hidden_tests_present"])
            self.assertNotEqual(by_id["easy"]["file_hashes"]["story.json"], UNAVAILABLE_MISSING)

            self.assertEqual(by_id["medium"]["difficulty"], "medium")
            self.assertTrue(by_id["medium"]["story_json_present"])
            self.assertFalse(by_id["medium"]["hidden_tests_present"])
            self.assertEqual(by_id["medium"]["file_hashes"]["hidden_tests.py"], UNAVAILABLE_MISSING)

            self.assertEqual(by_id["hard"]["difficulty"], "hard")
            self.assertFalse(by_id["hard"]["story_json_present"])
            self.assertTrue(by_id["hard"]["hidden_tests_present"])

    def test_easy__missing_benchmarks_root_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(discover_benchmarks(Path(tmp)), [])


class InventoryDirTests(unittest.TestCase):
    def test_easy__absent_directory_is_not_reported_as_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = inventory_dir(Path(tmp), "logs")
            self.assertFalse(result["exists"])
            self.assertEqual(result["file_count"], UNAVAILABLE)
            self.assertEqual(result["files"], [])

    def test_medium__existing_directory_reports_real_zero_when_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "logs").mkdir()
            result = inventory_dir(root, "logs")
            self.assertTrue(result["exists"])
            self.assertEqual(result["file_count"], 0)

    def test_medium__existing_directory_lists_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "logs/run-1/session.json", "{}")
            _write(root / "logs/run-2/session.json", "{}")
            result = inventory_dir(root, "logs")
            self.assertTrue(result["exists"])
            self.assertEqual(result["file_count"], 2)
            self.assertFalse(result["truncated"])


class BuildManifestTests(unittest.TestCase):
    def test_hard__manifest_has_expected_shape_for_fake_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "eval/benchmarks/easy/story.json", json.dumps({"metadata": {"difficulty": "easy"}}))
            _write(root / "eval/benchmarks/easy/hidden_tests.py", "def test_ac1(): pass\n")
            _write(root / "requirements.txt", "numpy\n")

            manifest = build_manifest(root)

            for key in (
                "baseline_id",
                "created_at",
                "schema_version",
                "code_version",
                "environment",
                "benchmark_inventory",
                "eval_report_inventory",
                "trace_log_inventory",
                "artifact_inventory",
                "prompt_config_hashes",
                "runner_workflow_config_hashes",
                "notes",
            ):
                self.assertIn(key, manifest)

            self.assertTrue(manifest["baseline_id"].startswith("v0.1-"))
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(len(manifest["benchmark_inventory"]), 1)

            # Not a git repo: git metadata must say so, never fabricate a commit.
            self.assertEqual(manifest["code_version"]["git_commit"], UNAVAILABLE)
            self.assertEqual(manifest["code_version"]["git_dirty"], UNAVAILABLE)

            # requirements.txt exists in the fake repo -> real hash, not the missing marker.
            self.assertNotEqual(manifest["prompt_config_hashes"]["requirements.txt"], UNAVAILABLE_MISSING)
            # core/agent_prompts.py does not exist in the fake repo -> missing marker, not 0/"".
            self.assertEqual(manifest["prompt_config_hashes"]["core/agent_prompts.py"], UNAVAILABLE_MISSING)

            self.assertIsInstance(json.dumps(manifest), str)

    def test_hard__manifest_is_json_serializable_and_reproducible_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "eval/benchmarks/easy/story.json", "{}")

            first = build_manifest(root)
            second = build_manifest(root)

            # created_at legitimately varies; everything else should not.
            first.pop("created_at")
            second.pop("created_at")
            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
