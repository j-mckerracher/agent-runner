from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path


class TelemetryDbTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="awb-telemetry-db-")
        os.environ["AGENT_RUNNER_DATA_DIR"] = self.tmpdir
        from server import db
        db.reset_for_tests()

    def tearDown(self):
        from server import db
        db.reset_for_tests()
        os.environ.pop("AGENT_RUNNER_DATA_DIR", None)
        os.environ.pop("AGENT_RUNNER_EVENT_LOG", None)
        os.environ.pop("AGENT_RUNNER_JOB_ID", None)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_medium__legacy_db_migration_preserves_jobs_and_adds_telemetry(self):
        legacy_db = Path(self.tmpdir) / "jobs.db"
        conn = sqlite3.connect(legacy_db)
        conn.executescript(
            """
            CREATE TABLE jobs (
              id TEXT PRIMARY KEY,
              change_id TEXT NOT NULL,
              parent_job_id TEXT,
              status TEXT NOT NULL,
              mode TEXT NOT NULL,
              runner TEXT NOT NULL,
              model TEXT,
              repo TEXT NOT NULL,
              submitted_at TEXT NOT NULL,
              events_path TEXT
            );
            INSERT INTO jobs (id, change_id, status, mode, runner, repo, submitted_at)
            VALUES ('job_legacy', 'LEGACY-1', 'succeeded', 'live', 'claude', '/tmp/repo', '2026-05-23T00:00:00Z');
            """
        )
        conn.close()

        from server import db
        row = db.get_job("job_legacy")
        self.assertIsNotNone(row)
        self.assertEqual(row["change_id"], "LEGACY-1")

        migrated = sqlite3.connect(legacy_db)
        try:
            tables = {row[0] for row in migrated.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("telemetry_events", tables)
            jobs = list(migrated.execute("SELECT id, change_id FROM jobs"))
            self.assertEqual(jobs, [("job_legacy", "LEGACY-1")])
        finally:
            migrated.close()

    def test_medium__telemetry_insert_is_idempotent_and_lists_by_job(self):
        from server import db

        db.insert_telemetry_event("job_one", {"seq": 1, "ts": "2026-05-23T00:00:00Z", "type": "stage.start", "stage": "intake"})
        db.insert_telemetry_event("job_one", {"seq": 1, "ts": "2026-05-23T00:00:01Z", "type": "stage.end", "stage": "intake"})
        db.insert_telemetry_event("job_two", {"seq": 1, "ts": "2026-05-23T00:00:02Z", "type": "log"})

        events = db.list_telemetry_events(["job_one"])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "stage.start")
        self.assertEqual(db.count_telemetry_events("job_one"), 1)

    def test_medium__backfill_tolerates_bad_seq_missing_ts_and_malformed_rows(self):
        from server import db

        events_path = Path(self.tmpdir) / "legacy-events.jsonl"
        events_path.write_text(
            "\n".join([
                json.dumps({"seq": 1, "ts": "2026-05-23T00:00:00Z", "type": "stage.start", "stage": "intake"}),
                json.dumps({"seq": 1, "type": "stage.end", "stage": "intake", "status": "ok"}),
                json.dumps({"seq": 0, "type": "metrics", "tokens_in": 10}),
                "{not-json",
                json.dumps({"seq": -1, "type": "log", "msg": "still imported"}),
            ]),
            encoding="utf-8",
        )
        job = {
            "id": "job_backfill",
            "submitted_at": "2026-05-22T23:59:00Z",
            "events_path": str(events_path),
        }

        inserted = db.backfill_telemetry_events_for_job(job)
        events = db.list_telemetry_events(["job_backfill"])

        self.assertEqual(inserted, 4)
        self.assertEqual([event["seq"] for event in events], [1, 2, 3, 5])
        self.assertEqual(events[1]["ts"], "2026-05-22T23:59:00Z")
        self.assertEqual(events[-1]["type"], "log")

    def test_easy__events_path_for_job_is_persistent_and_job_specific(self):
        from server.paths import data_dir, events_path_for, events_path_for_job

        first = events_path_for_job("job_first")
        second = events_path_for_job("job_second")

        self.assertNotEqual(first, second)
        self.assertTrue(str(first).startswith(str(data_dir() / "events")))
        self.assertTrue(str(events_path_for("CHANGE-1")).endswith("logs/CHANGE-1/events.jsonl"))

    def test_easy__prepare_job_paths_uses_job_specific_events_and_keeps_cassettes(self):
        from server.runner_proc import prepare_job_paths

        first_events, first_cassette = prepare_job_paths("SAME-CHANGE", "live", job_id="job_first")
        second_events, second_cassette = prepare_job_paths("SAME-CHANGE", "live", job_id="job_second")
        hermetic_events, hermetic_cassette = prepare_job_paths("SAME-CHANGE", "hermetic", job_id="job_third")

        self.assertNotEqual(first_events, second_events)
        self.assertIn("job_first", first_events)
        self.assertIn("job_second", second_events)
        self.assertIsNone(first_cassette)
        self.assertIsNone(second_cassette)
        self.assertIn("job_third", hermetic_events)
        self.assertTrue(hermetic_cassette.endswith("SAME-CHANGE.jsonl"))

    def test_medium__emit_writes_sqlite_best_effort_when_job_id_is_available(self):
        from server import db, events
        from server.events import emit

        os.environ["AGENT_RUNNER_EVENT_LOG"] = str(Path(self.tmpdir) / "events.jsonl")
        os.environ["AGENT_RUNNER_JOB_ID"] = "job_emit"
        events._default = None

        emit("log", level="info", msg="hello")

        rows = db.list_telemetry_events(["job_emit"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["job_id"], "job_emit")
        self.assertEqual(rows[0]["msg"], "hello")


class TelemetryRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="awb-telemetry-route-")
        os.environ["AGENT_RUNNER_DATA_DIR"] = cls.tmpdir
        from server import db, jobs
        db.reset_for_tests()
        jobs.reset_for_tests()
        from fastapi.testclient import TestClient
        from server.app import create_app
        cls._client_ctx = TestClient(create_app())
        cls.client = cls._client_ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._client_ctx.__exit__(None, None, None)
        from server import db, jobs
        db.reset_for_tests()
        jobs.reset_for_tests()
        os.environ.pop("AGENT_RUNNER_DATA_DIR", None)
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _insert_job(self, job_id: str, **overrides):
        from server import db
        record = {
            "id": job_id,
            "change_id": overrides.pop("change_id", job_id.upper()),
            "status": overrides.pop("status", "succeeded"),
            "run_kind": overrides.pop("run_kind", "regular"),
            "mode": overrides.pop("mode", "live"),
            "runner": overrides.pop("runner", "claude"),
            "model": overrides.pop("model", "claude-sonnet"),
            "repo": overrides.pop("repo", self.tmpdir),
            "submitted_at": overrides.pop("submitted_at", "2026-05-23T00:00:00Z"),
            **overrides,
        }
        db.insert_job(record)
        update_fields = {
            key: value for key, value in record.items()
            if key not in {
                "id", "change_id", "parent_job_id", "status", "run_kind", "mode", "runner", "model",
                "log_level", "repo", "ado_url", "story_file", "extra_context", "eval_runner_args",
                "submitted_at", "events_path", "cassette_path",
            }
        }
        if update_fields:
            db.update_job(job_id, **update_fields)
        return record

    def test_medium__runs_events_prefers_sqlite_and_jsonl_backfill_fallback(self):
        from server import db

        self._insert_job("job_sqlite_events")
        db.insert_telemetry_event("job_sqlite_events", {"seq": 1, "ts": "2026-05-23T00:00:00Z", "type": "log", "msg": "from db"})
        r = self.client.get("/runs/job_sqlite_events/events")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()[0]["msg"], "from db")

        events_path = Path(self.tmpdir) / "legacy-job-events.jsonl"
        events_path.write_text(json.dumps({"seq": 1, "ts": "2026-05-23T00:00:00Z", "type": "log", "msg": "from jsonl"}) + "\n", encoding="utf-8")
        self._insert_job("job_jsonl_events", events_path=str(events_path))
        r2 = self.client.get("/runs/job_jsonl_events/events")
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()[0]["msg"], "from jsonl")

    def test_medium__telemetry_runs_returns_historical_jobs_and_filter_options(self):
        self._insert_job("job_telemetry_runs", change_id="TEL-RUNS", runner="copilot", model="gpt-5.5")

        r = self.client.get("/telemetry/runs?q=TEL-RUNS&limit=10")

        self.assertEqual(r.status_code, 200)
        payload = r.json()
        self.assertGreaterEqual(payload["count"], 1)
        self.assertTrue(any(item["id"] == "job_telemetry_runs" for item in payload["items"]))
        self.assertIn("copilot", payload["filter_options"]["runners"])

    def test_medium__telemetry_query_selected_ignores_visible_filters(self):
        from server import db

        self._insert_job(
            "job_selected_kept",
            change_id="SELECTED-KEEP",
            status="succeeded",
            started_at="2026-05-23T00:01:00Z",
            finished_at="2026-05-23T00:03:00Z",
        )
        db.insert_telemetry_event("job_selected_kept", {"seq": 1, "ts": "2026-05-23T00:01:00Z", "type": "stage.start", "stage": "execution"})
        payload = {
            "selection_mode": "selected",
            "job_ids": ["job_selected_kept"],
            "filters": {"status": ["failed"]},
        }

        r = self.client.post("/telemetry/query", json=payload)

        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["selected_count"], 1)
        self.assertEqual(body["run_rows"][0]["id"], "job_selected_kept")

    def test_medium__telemetry_query_all_mode_truncates_at_5000(self):
        for index in range(5001):
            self._insert_job(
                f"job_cap_{index}",
                change_id=f"CAP-{index}",
                repo="/tmp/cap-repo",
                submitted_at=f"2026-05-23T01:{index % 60:02d}:00Z",
            )

        r = self.client.post(
            "/telemetry/query",
            json={"selection_mode": "all", "filters": {"repo": ["/tmp/cap-repo"]}},
        )

        self.assertEqual(r.status_code, 200)
        coverage = r.json()["coverage"]
        self.assertTrue(coverage["truncated"])
        self.assertEqual(coverage["matched_jobs"], 5001)
        self.assertEqual(coverage["aggregated_jobs"], 5000)

    def test_medium__telemetry_query_calculates_core_aggregates_and_cost_source(self):
        from server import db

        self._insert_job(
            "job_aggregate",
            change_id="AGG-1",
            status="failed",
            started_at="2026-05-23T00:01:00Z",
            finished_at="2026-05-23T00:05:00Z",
            tokens_in=100,
            tokens_out=50,
            cost_usd=0,
            error_message="execution failed in /tmp/repo/job_123",
        )
        db.insert_telemetry_event("job_aggregate", {"seq": 1, "ts": "2026-05-23T00:01:00Z", "type": "stage.start", "stage": "execution"})
        db.insert_telemetry_event("job_aggregate", {"seq": 2, "ts": "2026-05-23T00:02:00Z", "type": "user.prompt", "stage": "execution", "title": "Need input"})
        db.insert_telemetry_event("job_aggregate", {"seq": 3, "ts": "2026-05-23T00:03:00Z", "type": "user.prompt.timeout", "stage": "execution"})
        db.insert_telemetry_event("job_aggregate", {"seq": 4, "ts": "2026-05-23T00:04:00Z", "type": "stage.end", "stage": "execution", "status": "error"})

        r = self.client.post("/telemetry/query", json={"selection_mode": "selected", "job_ids": ["job_aggregate"]})

        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["summary"]["tokens_total"], 150)
        self.assertEqual(body["summary"]["top_failed_stage"], "execution")
        self.assertEqual(body["user_input_stats"]["timeout_count"], 1)
        self.assertEqual(body["coverage"]["cost_data_source"], "estimated")
        execution_stage = next(row for row in body["stage_stats"] if row["stage"] == "execution")
        self.assertEqual(execution_stage["p99_duration_seconds"], 180.0)
        self.assertEqual(body["error_stats"][0]["count"], 1)
        self.assertEqual(body["error_stats"][0]["severity"], "high")
        self.assertEqual(body["error_stats"][0]["triage_state"], "open")

    def test_medium__telemetry_query_filters_telemetry_gaps(self):
        from server import db

        repo = "/tmp/telemetry-gap-repo"
        self._insert_job("job_gap_missing", change_id="GAP-MISSING", repo=repo)
        self._insert_job("job_gap_present", change_id="GAP-PRESENT", repo=repo)
        db.insert_telemetry_event("job_gap_present", {"seq": 1, "ts": "2026-05-23T00:00:00Z", "type": "log", "msg": "present"})

        r = self.client.post(
            "/telemetry/query",
            json={"selection_mode": "all", "filters": {"repo": [repo], "missing_events_only": True}},
        )

        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["selected_count"], 1)
        self.assertEqual(body["run_rows"][0]["id"], "job_gap_missing")

    def test_medium__telemetry_query_adds_daily_chart_payload(self):
        from server import db

        self._insert_job(
            "job_chart",
            change_id="CHART-1",
            status="succeeded",
            submitted_at="2026-05-23T00:00:00Z",
            started_at="2026-05-23T00:01:00Z",
            finished_at="2026-05-23T00:05:00Z",
            tokens_in=100,
            tokens_out=50,
            original_ac_count=2,
        )
        db.insert_telemetry_event("job_chart", {"seq": 1, "ts": "2026-05-23T00:01:00Z", "type": "stage.start", "stage": "execution"})
        db.insert_telemetry_event("job_chart", {"seq": 2, "ts": "2026-05-23T00:02:00Z", "type": "llm.call", "stage": "execution", "model": "claude-sonnet", "tokens_in": 80, "tokens_out": 20})
        db.insert_telemetry_event("job_chart", {"seq": 3, "ts": "2026-05-23T00:03:00Z", "type": "loop.end", "loop_name": "eval-optimizer", "stage": "execution", "actual_iterations": 3, "max_iterations": 3, "passed": False})
        db.insert_telemetry_event("job_chart", {"seq": 4, "ts": "2026-05-23T00:04:00Z", "type": "loop.end", "loop_name": "uow-eval", "stage": "execution", "actual_iterations": 1, "max_iterations": 3, "passed": True})
        db.insert_telemetry_event("job_chart", {"seq": 5, "ts": "2026-05-23T00:05:00Z", "type": "stage.end", "stage": "execution", "status": "ok"})

        r = self.client.post("/telemetry/query", json={"selection_mode": "selected", "job_ids": ["job_chart"]})

        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("timeseries", body)
        charts = body["charts"]
        self.assertEqual(charts["bucket"], "day")
        self.assertEqual(charts["rollup"], "run")
        self.assertEqual(charts["duration_points"][0]["complete_elapsed_seconds"], 300.0)
        self.assertEqual(charts["duration_points"][0]["original_ac_count"], 2)
        self.assertEqual(charts["duration_series"][0]["complete_elapsed_median_seconds"], 300.0)
        self.assertEqual(charts["token_series"][0]["tokens_total"], 100)
        self.assertEqual(charts["stage_duration_heatmap"][0]["stage"], "execution")
        self.assertEqual(charts["stage_duration_boxplot"][0]["stage"], "execution")
        self.assertEqual(charts["stage_duration_boxplot"][0]["median_seconds"], 240.0)
        self.assertEqual(charts["stage_duration_boxplot"][0]["q1_seconds"], 240.0)
        self.assertEqual(charts["stage_duration_boxplot"][0]["q3_seconds"], 240.0)
        self.assertEqual(charts["stage_duration_boxplot"][0]["max_seconds"], 240.0)
        model_point = charts["model_points"][0]
        self.assertEqual(model_point["label"], "claude / claude-sonnet")
        self.assertEqual(model_point["runner"], "claude")
        self.assertEqual(model_point["model_key"], "claude-sonnet")
        self.assertEqual(model_point["runs"], 1)
        self.assertEqual(charts["model_run_counts"][0]["model"], "claude-sonnet")
        self.assertEqual(charts["model_run_counts"][0]["runs"], 1)
        self.assertEqual(charts["model_run_counts"][0]["runners"], ["claude"])
        loop_by_name = {row["loop_name"]: row for row in charts["loop_iteration_series"]}
        self.assertEqual(loop_by_name["eval-optimizer"]["total_iterations"], 3)
        self.assertEqual(loop_by_name["uow-eval"]["total_iterations"], 1)

    def test_medium__telemetry_profile_endpoint_backfills_jsonl_events(self):
        events_path = Path(self.tmpdir) / "profile-events.jsonl"
        events_path.write_text(
            "\n".join([
                json.dumps({"seq": 1, "ts": "2026-05-23T00:01:00Z", "type": "stage.start", "stage": "execution"}),
                json.dumps({"seq": 2, "ts": "2026-05-23T00:02:00Z", "type": "llm.call", "stage": "execution", "model": "gpt-5.5", "tokens_in": 10, "tokens_out": 5}),
                json.dumps({"seq": 3, "ts": "2026-05-23T00:03:00Z", "type": "stage.end", "stage": "execution", "status": "ok"}),
            ])
            + "\n",
            encoding="utf-8",
        )
        self._insert_job(
            "job_profile",
            change_id="PROFILE-1",
            status="succeeded",
            submitted_at="2026-05-23T00:00:00Z",
            started_at="2026-05-23T00:01:00Z",
            finished_at="2026-05-23T00:03:00Z",
            events_path=str(events_path),
        )

        r = self.client.get("/telemetry/runs/job_profile/profile")

        self.assertEqual(r.status_code, 200)
        profile = r.json()["profile"]
        self.assertEqual(profile["job_id"], "job_profile")
        self.assertEqual(profile["complete_elapsed_seconds"], 180.0)
        self.assertEqual(profile["stage_spans"][0]["stage"], "execution")
        self.assertEqual(profile["stage_spans"][0]["duration_seconds"], 120.0)
        self.assertEqual(profile["models_used"], ["gpt-5.5"])
        self.assertEqual(profile["tokens"]["tokens_total"], 15)

    def test_medium__benchmark_evaluation_is_parent_job_level_with_missing_coverage(self):
        self._insert_job("job_benchmark_parent", run_kind="benchmark_evaluation", change_id="BENCH-1")

        r = self.client.post("/telemetry/query", json={"selection_mode": "selected", "job_ids": ["job_benchmark_parent"]})

        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["selected_count"], 1)
        self.assertEqual(body["run_rows"][0]["run_kind"], "benchmark_evaluation")
        self.assertEqual(body["coverage"]["jobs_missing_events"], 1)


if __name__ == "__main__":
    unittest.main()
