"""Smoke tests for the local FastAPI server."""
from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path


class ServerRoutesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="agentrunner-test-")
        os.environ["AGENT_RUNNER_DATA_DIR"] = cls.tmpdir
        from server import db, jobs
        db.reset_for_tests()
        jobs.reset_for_tests()
        from server.app import create_app
        from fastapi.testclient import TestClient
        cls._client_ctx = TestClient(create_app())
        cls.client = cls._client_ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._client_ctx.__exit__(None, None, None)
        shutil.rmtree(cls.tmpdir, ignore_errors=True)
        os.environ.pop("AGENT_RUNNER_DATA_DIR", None)

    def test_easy__health_returns_ok(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

    def test_easy__index_serves_gui_html(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("agent-runner", r.text)
        self.assertIn("Open current run in Opik", r.text)
        self.assertIn("Open Opik evaluation workspace", r.text)
        self.assertIn("Start evaluation run", r.text)
        self.assertIn("Select an agent to view its latest prompt.", r.text)

    def test_medium__settings_returns_runner_models_and_efforts(self):
        s = self.client.get("/settings").json()
        self.assertIn("runner_models", s)
        self.assertIn("claude", s["runner_models"])
        self.assertIn("api", s)
        self.assertIn("opik", s)
        self.assertIn("dashboard_url", s["opik"])

    def test_medium__settings_put_partial_merges_and_persists(self):
        r = self.client.put("/settings", json={"concurrency": {"max_running_jobs": 4}})
        self.assertEqual(r.status_code, 200)
        cfg = self.client.get("/settings").json()
        self.assertEqual(cfg["concurrency"]["max_running_jobs"], 4)
        self.assertIn("api", cfg)

    def test_medium__settings_put_invalid_port_rejected(self):
        r = self.client.put("/settings", json={"api": {"port": 0}})
        self.assertEqual(r.status_code, 422)

    def test_easy__agents_lists_known_materializable_agents(self):
        r = self.client.get("/agents")
        self.assertEqual(r.status_code, 200)
        names = [a["name"] for a in r.json()["items"]]
        self.assertIn("intake", names)

    def test_medium__agent_detail_returns_latest_prompt(self):
        r = self.client.get("/agents/intake")
        self.assertEqual(r.status_code, 200)
        payload = r.json()
        self.assertEqual(payload["name"], "intake")
        self.assertTrue(payload["version"].startswith("v"))
        self.assertTrue(payload["prompt_file"].endswith("agent-sources/intake/v1/prompt.md"))
        self.assertIn("Intake Agent Prompt", payload["prompt_text"])
        self.assertIn("tags", payload)

    def test_medium__agent_detail_unknown_agent_returns_404(self):
        r = self.client.get("/agents/does-not-exist")
        self.assertEqual(r.status_code, 404)

    def test_easy__corpus_returns_eval_stories(self):
        r = self.client.get("/corpus").json()
        self.assertGreaterEqual(r["count"], 0)

    def test_easy__evaluate_summary_returns_200(self):
        r = self.client.get("/evaluate/summary")
        self.assertEqual(r.status_code, 200)
        payload = r.json()
        self.assertIn("overall_pass_rate", payload)
        self.assertIn("regressions", payload)
        self.assertIn("total_runs", payload)
        self.assertIn("avg_cost_usd", payload)
        self.assertIn("rows", payload)

    def test_medium__evaluate_summary_counts_only_evaluation_jobs(self):
        from server import db

        before = self.client.get("/evaluate/summary").json()["total_runs"]
        submitted_at = db.now_iso()
        db.insert_job({
            "id": "job_regular_eval_filter",
            "change_id": "EVAL-002",
            "status": "succeeded",
            "run_kind": "regular",
            "mode": "live",
            "runner": "claude",
            "repo": "/tmp/none",
            "submitted_at": submitted_at,
        })
        after_regular = self.client.get("/evaluate/summary").json()["total_runs"]
        self.assertEqual(after_regular, before)

        db.insert_job({
            "id": "job_evaluation_eval_filter",
            "change_id": "EVAL-002",
            "status": "succeeded",
            "run_kind": "evaluation",
            "mode": "live",
            "runner": "claude",
            "repo": "/tmp/none",
            "submitted_at": submitted_at,
        })
        after_evaluation = self.client.get("/evaluate/summary").json()["total_runs"]
        self.assertEqual(after_evaluation, before + 1)

    def test_medium__submit_run_inserts_queued_job(self):
        r = self.client.post(
            "/runs",
            json={"repo": "/tmp/none", "change_id": "TEST-AC-001", "runner": "claude", "mode": "live"},
        )
        self.assertEqual(r.status_code, 200)
        jid = r.json()["job_id"]
        listing = self.client.get("/runs").json()
        self.assertGreaterEqual(listing["count"], 1)
        detail = self.client.get(f"/runs/{jid}").json()
        self.assertEqual(detail["change_id"], "TEST-AC-001")
        self.assertEqual(detail["run_kind"], "regular")
        self.assertIn(detail["status"], ("queued", "running", "failed", "cancelled"))

    def test_medium__submit_evaluation_run_queues_evaluation_job_hidden_from_runs(self):
        r = self.client.post(
            "/evaluate/runs",
            json={"repo": "/tmp/none", "story_id": "EVAL-001", "runner": "claude", "mode": "live"},
        )
        self.assertEqual(r.status_code, 200)
        jid = r.json()["job_id"]
        detail = self.client.get(f"/runs/{jid}").json()
        self.assertEqual(detail["change_id"], "EVAL-001")
        self.assertEqual(detail["run_kind"], "evaluation")
        self.assertTrue(detail["story_file"].endswith("eval/stories/EVAL-001.json"))
        listing = self.client.get("/runs").json()
        self.assertNotIn(jid, [item["id"] for item in listing["items"]])
        eval_listing = self.client.get("/runs?run_kind=evaluation").json()
        self.assertIn(jid, [item["id"] for item in eval_listing["items"]])

    def test_medium__submit_run_rejects_invalid_runner(self):
        r = self.client.post(
            "/runs",
            json={"repo": "/tmp/none", "change_id": "TEST-S2", "runner": "bogus", "mode": "live"},
        )
        self.assertIn(r.status_code, (400, 422))

    def test_medium__submit_run_rejects_both_ado_and_story(self):
        r = self.client.post(
            "/runs",
            json={
                "repo": "/tmp/none",
                "change_id": "TEST-S3",
                "runner": "claude",
                "mode": "live",
                "ado_url": "http://x",
                "story_file": "y.json",
            },
        )
        self.assertEqual(r.status_code, 400)

    def test_medium__submit_run_rejects_evaluation_run_kind(self):
        r = self.client.post(
            "/runs",
            json={
                "repo": "/tmp/none",
                "change_id": "EVAL-001",
                "runner": "claude",
                "mode": "live",
                "run_kind": "evaluation",
            },
        )
        self.assertEqual(r.status_code, 400)

    def test_medium__submit_evaluation_run_rejects_unknown_story_without_queueing(self):
        from server import db

        before = len(db.list_jobs(run_kind="evaluation", limit=500))
        r = self.client.post(
            "/evaluate/runs",
            json={"repo": "/tmp/none", "story_id": "EVAL-DOES-NOT-EXIST", "runner": "claude", "mode": "live"},
        )
        self.assertEqual(r.status_code, 404)
        after = len(db.list_jobs(run_kind="evaluation", limit=500))
        self.assertEqual(after, before)

    def test_medium__submit_run_rejects_story_change_id_mismatch_before_queueing(self):
        before = self.client.get("/runs").json()["count"]
        with tempfile.TemporaryDirectory() as tmpdir:
            story = Path(tmpdir) / "story.json"
            story.write_text(
                """
                {
                  "change_id": "STORY-123",
                  "title": "Synthetic story",
                  "description": "desc",
                  "acceptance_criteria": ["ac"]
                }
                """.strip(),
                encoding="utf-8",
            )
            r = self.client.post(
                "/runs",
                json={
                    "repo": "/tmp/none",
                    "change_id": "RUN-456",
                    "runner": "claude",
                    "mode": "live",
                    "story_file": str(story),
                },
            )
        self.assertEqual(r.status_code, 400)
        self.assertIn("does not match", r.text)
        after = self.client.get("/runs").json()["count"]
        self.assertEqual(after, before)

    def test_medium__legacy_jobs_db_migrates_run_kind_before_runs_query(self):
        from server import db

        original_data_dir = os.environ.get("AGENT_RUNNER_DATA_DIR")
        with tempfile.TemporaryDirectory(prefix="agentrunner-legacy-db-") as tmpdir:
            legacy_db = Path(tmpdir) / "jobs.db"
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
                  copilot_effort TEXT,
                  repo TEXT NOT NULL,
                  ado_url TEXT,
                  story_file TEXT,
                  extra_context TEXT,
                  skip_materialize INTEGER DEFAULT 0,
                  submitted_at TEXT NOT NULL,
                  started_at TEXT,
                  finished_at TEXT,
                  exit_code INTEGER,
                  error_message TEXT,
                  pid INTEGER,
                  events_path TEXT,
                  cassette_path TEXT,
                  tokens_in INTEGER DEFAULT 0,
                  tokens_out INTEGER DEFAULT 0,
                  cost_usd REAL DEFAULT 0,
                  current_stage TEXT
                );
                CREATE INDEX idx_jobs_status ON jobs(status);
                CREATE INDEX idx_jobs_submitted ON jobs(submitted_at DESC);
                CREATE INDEX idx_jobs_parent ON jobs(parent_job_id);
                CREATE INDEX idx_jobs_change ON jobs(change_id);
                """
            )
            conn.close()

            os.environ["AGENT_RUNNER_DATA_DIR"] = tmpdir
            db.reset_for_tests()
            try:
                rows = db.list_jobs(run_kind="regular", limit=50)
                self.assertEqual(rows, [])

                migrated = sqlite3.connect(legacy_db)
                try:
                    columns = {
                        row[1]
                        for row in migrated.execute("PRAGMA table_info(jobs)").fetchall()
                    }
                    self.assertIn("run_kind", columns)
                finally:
                    migrated.close()
            finally:
                if original_data_dir is None:
                    os.environ.pop("AGENT_RUNNER_DATA_DIR", None)
                else:
                    os.environ["AGENT_RUNNER_DATA_DIR"] = original_data_dir
                db.reset_for_tests()

    def test_medium__get_run_detail_includes_error_message(self):
        from server import db

        r = self.client.post(
            "/runs",
            json={"repo": "/tmp/none", "change_id": "TEST-AC-001", "runner": "claude", "mode": "live"},
        )
        self.assertEqual(r.status_code, 200)
        jid = r.json()["job_id"]
        db.update_job(jid, status="failed", error_message="boom", exit_code=1)
        detail = self.client.get(f"/runs/{jid}").json()
        self.assertEqual(detail["error_message"], "boom")
        self.assertEqual(detail["exit_code"], 1)

    def test_medium__get_run_detail_includes_opik_dashboard_link_when_configured(self):
        settings = {
            "opik": {
                "dashboard_url": "https://www.comet.com/opik",
                "workspace_name": "demo-workspace",
                "project_id": "4f9e3c11-8bfe-4af5-9f18-ef4b44552a7a",
                "project_name": "agent-runner",
            }
        }
        r = self.client.put("/settings", json=settings)
        self.assertEqual(r.status_code, 200)
        submit = self.client.post(
            "/runs",
            json={"repo": "/tmp/none", "change_id": "TEST-AC-001", "runner": "claude", "mode": "live"},
        )
        self.assertEqual(submit.status_code, 200)
        jid = submit.json()["job_id"]
        detail = self.client.get(f"/runs/{jid}").json()
        self.assertEqual(detail["opik"]["thread_id"], "TEST-AC-001")
        self.assertEqual(detail["opik"]["project_name"], "agent-runner")
        self.assertIn("/workspaceGuard/demo-workspace/projects/4f9e3c11-8bfe-4af5-9f18-ef4b44552a7a", detail["opik"]["dashboard_url"])
        self.assertIn("tab=logs", detail["opik"]["dashboard_url"])
        self.assertIn("logsType=traces", detail["opik"]["dashboard_url"])
        self.assertIn("traces_filters=", detail["opik"]["dashboard_url"])


    def test_medium__respond_returns_404_for_unknown_job(self):
        r = self.client.post(
            "/runs/nonexistent-job-id/respond",
            json={"responses": {"Is this testable?": "Yes"}},
        )
        self.assertEqual(r.status_code, 404)

    def test_medium__respond_returns_409_when_job_not_awaiting_input(self):
        import uuid
        from server import db

        jid = str(uuid.uuid4())
        db.insert_job({
            "id": jid, "change_id": "RESPOND-001", "status": "queued",
            "run_kind": "regular", "mode": "live", "runner": "claude",
            "repo": "/tmp/none", "submitted_at": db.now_iso(),
        })
        r = self.client.post(
            f"/runs/{jid}/respond",
            json={"responses": {"Is this testable?": "Yes"}},
        )
        self.assertEqual(r.status_code, 409)

    def test_medium__respond_writes_responses_file_and_returns_ok(self):
        import json as _json
        import uuid
        from server import db, paths

        jid = str(uuid.uuid4())
        db.insert_job({
            "id": jid, "change_id": "RESPOND-002", "status": "awaiting_input",
            "run_kind": "regular", "mode": "live", "runner": "claude",
            "repo": "/tmp/none", "submitted_at": db.now_iso(),
        })

        answers = {"What is the expected error code?": "404", "Is this an ADO story?": "Yes"}
        r2 = self.client.post(f"/runs/{jid}/respond", json={"responses": answers})

        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.json().get("ok"))

        responses_path = paths.user_responses_path_for("RESPOND-002")
        self.assertTrue(responses_path.exists())
        written = _json.loads(responses_path.read_text())
        self.assertEqual(written["responses"], answers)

        updated_job = db.get_job(jid)
        self.assertEqual(updated_job["status"], "running")

        responses_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
