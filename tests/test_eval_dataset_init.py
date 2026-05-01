import json
import shutil
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path

from eval.dataset_manifest import ManifestValidationError, load_dataset_lock, load_dataset_manifest
from eval.dataset_sources import DatasetSourceError, read_source
from eval.init_dataset import initialize_dataset, sample_records
from eval.yaml_io import dump_yaml


class EvalDatasetInitTests(unittest.TestCase):
    def setUp(self):
        self.workdir = Path(__file__).parent / ".dataset_init_workdir"
        if self.workdir.exists():
            shutil.rmtree(self.workdir)
        self.workdir.mkdir()

    def tearDown(self):
        if self.workdir.exists():
            shutil.rmtree(self.workdir)

    def write_manifest(self, payload):
        path = self.workdir / f"{payload.get('dataset_id', 'dataset')}.yaml"
        dump_yaml(payload, path)
        return path

    def test_manifest_validation_requires_seed(self):
        csv_path = self.workdir / "records.csv"
        csv_path.write_text("id,name\n1,Ada\n", encoding="utf-8")
        manifest_path = self.write_manifest(
            {
                "dataset_id": "claims",
                "display_name": "Claims",
                "source": {"type": "csv", "path": "records.csv"},
                "sampling": {"strategy": "head", "sample_size": 1},
                "domain_context": "Claims domain",
            }
        )

        with self.assertRaisesRegex(ManifestValidationError, "sampling.seed"):
            load_dataset_manifest(manifest_path)

    def test_csv_source_reader_reads_records(self):
        csv_path = self.workdir / "records.csv"
        csv_path.write_text("id,name\n1,Ada\n2,Grace\n", encoding="utf-8")

        result = read_source({"type": "csv", "path": "records.csv"}, self.workdir / "dataset.yaml")

        self.assertEqual(result.records[0]["name"], "Ada")
        self.assertEqual(result.metadata["fields"], ["id", "name"])

    def test_jsonl_source_reader_rejects_non_object_lines(self):
        jsonl_path = self.workdir / "records.jsonl"
        jsonl_path.write_text('{"id": 1}\n["bad"]\n', encoding="utf-8")

        with self.assertRaisesRegex(DatasetSourceError, "must be a JSON object"):
            read_source({"type": "jsonl", "path": "records.jsonl"}, self.workdir / "dataset.yaml")

    def test_sqlite_source_reader_reads_table(self):
        db_path = self.workdir / "records.db"
        with sqlite3.connect(db_path) as connection:
            connection.execute("CREATE TABLE records (id INTEGER, name TEXT)")
            connection.execute("INSERT INTO records VALUES (1, 'Ada')")

        result = read_source({"type": "sqlite", "path": "records.db", "table": "records"}, self.workdir / "dataset.yaml")

        self.assertEqual(result.records, [{"id": 1, "name": "Ada"}])

    def test_optional_source_readers_fail_actionably(self):
        with self.assertRaisesRegex(DatasetSourceError, "pyarrow|pandas"):
            read_source({"type": "parquet", "path": "records.parquet"}, self.workdir / "dataset.yaml")
        with self.assertRaisesRegex(DatasetSourceError, "psycopg"):
            read_source(
                {"type": "postgres", "connection_string": "postgres://example", "query": "select 1"},
                self.workdir / "dataset.yaml",
            )
        with self.assertRaisesRegex(DatasetSourceError, "boto3|s3fs"):
            read_source({"type": "s3_glob", "uri": "s3://bucket/*.jsonl"}, self.workdir / "dataset.yaml")

    def test_random_sampling_is_deterministic_with_seed(self):
        manifest_path = self.write_manifest(
            {
                "dataset_id": "claims",
                "display_name": "Claims",
                "source": {"type": "csv", "path": "records.csv"},
                "sampling": {"strategy": "random", "sample_size": 3, "seed": 11},
                "domain_context": "Claims domain",
            }
        )
        manifest = load_dataset_manifest(manifest_path)
        records = [{"id": index} for index in range(10)]

        first, first_meta = sample_records(records, manifest)
        second, second_meta = sample_records(records, manifest)

        self.assertEqual(first, second)
        self.assertEqual(first_meta["indexes"], second_meta["indexes"])

    def test_stratified_sampling_reports_distribution(self):
        manifest_path = self.write_manifest(
            {
                "dataset_id": "claims",
                "display_name": "Claims",
                "source": {"type": "jsonl", "path": "records.jsonl"},
                "sampling": {"strategy": "stratified", "sample_size": 4, "seed": 7, "stratify_by": "tier"},
                "domain_context": "Claims domain",
            }
        )
        manifest = load_dataset_manifest(manifest_path)
        records = [{"id": f"a{index}", "tier": "easy"} for index in range(6)] + [
            {"id": f"b{index}", "tier": "hard"} for index in range(2)
        ]

        sample, metadata = sample_records(records, manifest)

        self.assertEqual(len(sample), 4)
        self.assertEqual(metadata["stratum_distribution"], {"easy": 3, "hard": 1})

    def test_manual_sampling_requires_explicit_ids_or_indexes(self):
        manifest_path = self.write_manifest(
            {
                "dataset_id": "claims",
                "display_name": "Claims",
                "source": {"type": "csv", "path": "records.csv"},
                "sampling": {"strategy": "manual", "sample_size": 1, "seed": 3},
                "domain_context": "Claims domain",
            }
        )

        with self.assertRaisesRegex(ManifestValidationError, "manual sampling requires"):
            load_dataset_manifest(manifest_path)

    def test_initialize_dataset_writes_sample_and_lock(self):
        csv_path = self.workdir / "records.csv"
        csv_path.write_text("id,name,score\n1,Ada,10\n2,Grace,\n", encoding="utf-8")
        manifest_path = self.write_manifest(
            {
                "dataset_id": "claims",
                "display_name": "Claims",
                "source": {"type": "csv", "path": "records.csv"},
                "sampling": {"strategy": "head", "sample_size": 2, "seed": 42},
                "domain_context": "Claims domain",
            }
        )

        lock, summary = initialize_dataset(manifest_path)

        sample_path = self.workdir / "samples" / "claims_sample.jsonl"
        lock_path = self.workdir / "claims.lock"
        self.assertTrue(sample_path.exists())
        self.assertTrue(lock_path.exists())
        self.assertEqual(summary["sample_count"], 2)
        self.assertEqual(lock.schema["fields"], ["id", "name", "score"])
        self.assertIn("stable_hash", lock.schema)
        loaded_lock = load_dataset_lock(lock_path)
        self.assertEqual(loaded_lock.source_fingerprint, lock.source_fingerprint)

    def test_lock_hash_changes_when_schema_changes(self):
        first_csv = self.workdir / "first.csv"
        second_csv = self.workdir / "second.csv"
        first_csv.write_text("id,name\n1,Ada\n", encoding="utf-8")
        second_csv.write_text("id,name,score\n1,Ada,10\n", encoding="utf-8")
        first_manifest = self.write_manifest(
            {
                "dataset_id": "first",
                "display_name": "First",
                "source": {"type": "csv", "path": "first.csv"},
                "sampling": {"strategy": "head", "sample_size": 1, "seed": 1},
                "domain_context": "Domain",
            }
        )
        second_manifest = self.write_manifest(
            {
                "dataset_id": "second",
                "display_name": "Second",
                "source": {"type": "csv", "path": "second.csv"},
                "sampling": {"strategy": "head", "sample_size": 1, "seed": 1},
                "domain_context": "Domain",
            }
        )

        first_lock, _ = initialize_dataset(first_manifest)
        second_lock, _ = initialize_dataset(second_manifest)

        self.assertNotEqual(first_lock.schema["stable_hash"], second_lock.schema["stable_hash"])

    def test_cli_smoke_creates_sample_and_lock(self):
        csv_path = self.workdir / "records.csv"
        csv_path.write_text("id,name\n1,Ada\n2,Grace\n", encoding="utf-8")
        manifest_path = self.write_manifest(
            {
                "dataset_id": "cli_claims",
                "display_name": "CLI Claims",
                "source": {"type": "csv", "path": "records.csv"},
                "sampling": {"strategy": "head", "sample_size": 1, "seed": 99},
                "domain_context": "Claims domain",
            }
        )

        result = subprocess.run(
            [sys.executable, "eval/init_dataset.py", "--dataset", str(manifest_path)],
            cwd=Path(__file__).resolve().parent.parent,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Initialized dataset sample: 1 records", result.stdout)
        self.assertTrue((self.workdir / "samples" / "cli_claims_sample.jsonl").exists())
        self.assertTrue((self.workdir / "cli_claims.lock").exists())
        lines = (self.workdir / "samples" / "cli_claims_sample.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(lines[0])["name"], "Ada")


if __name__ == "__main__":
    unittest.main()
