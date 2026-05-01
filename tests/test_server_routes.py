"""Smoke tests for the local FastAPI server."""
from __future__ import annotations

import json
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

    def test_medium__settings_returns_agent_model_defaults(self):
        s = self.client.get("/settings").json()
        self.assertIn("agent_model_defaults", s)
        self.assertIsInstance(s["agent_model_defaults"], dict)

    def test_medium__settings_put_sets_agent_model_defaults(self):
        defaults = {
            "intake": {"claude": "claude-sonnet-4-6"},
            "task-generator": {"copilot": "gpt-5.5"},
        }
        r = self.client.put("/settings", json={"agent_model_defaults": defaults})
        self.assertEqual(r.status_code, 200)
        cfg = self.client.get("/settings").json()
        self.assertEqual(cfg["agent_model_defaults"]["intake"]["claude"], "claude-sonnet-4-6")
        self.assertEqual(cfg["agent_model_defaults"]["task-generator"]["copilot"], "gpt-5.5")

    def test_medium__settings_put_invalid_runner_in_defaults_rejected(self):
        r = self.client.put("/settings", json={
            "agent_model_defaults": {
                "intake": {"invalid_runner": "some-model"}
            }
        })
        self.assertEqual(r.status_code, 422)
        errors = r.json().get("detail", {}).get("errors", [])
        self.assertTrue(any("invalid_runner" in str(e) for e in errors))

    def test_medium__settings_put_invalid_model_for_runner_rejected(self):
        r = self.client.put("/settings", json={
            "agent_model_defaults": {
                "intake": {"claude": "invalid-model-name"}
            }
        })
        self.assertEqual(r.status_code, 422)
        errors = r.json().get("detail", {}).get("errors", [])
        self.assertTrue(any("invalid" in str(e).lower() for e in errors))

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

    def test_medium__corpus_exposes_generated_story_suite_metadata_and_skips_invalid(self):
        from server import corpus

        original_root = corpus.EVAL_STORIES_ROOT
        case_root = Path(self.tmpdir) / "generated-corpus"
        stories_root = case_root / "stories"
        suites_root = case_root / "suites" / "medium"
        stories_root.mkdir(parents=True, exist_ok=True)
        suites_root.mkdir(parents=True, exist_ok=True)
        story_path = stories_root / "raw_story_001_medium.json"
        suite_yaml = "suites/medium/raw_story_001_medium.yaml"
        story_path.write_text(
            json.dumps({
                "change_id": "raw_story_001_medium",
                "title": "Generated medium story",
                "description": "A generated evaluation story.",
                "acceptance_criteria": ["Check one", "Check two"],
                "metadata": {
                    "eval_story_id": "raw-story-001",
                    "suite_tier": "medium",
                    "dataset_id": "dataset-alpha",
                },
                "raw_metadata": {
                    "suite_yaml": suite_yaml,
                    "raw_story_id": "raw-story-001",
                    "tier": "medium",
                    "dataset_id": "dataset-alpha",
                },
            }),
            encoding="utf-8",
        )
        (stories_root / "invalid.json").write_text("[]", encoding="utf-8")
        (suites_root / "suite_manifest.yaml").write_text(
            "\n".join([
                "suite_id: dataset-alpha-medium",
                "suite_tier: medium",
                "dataset_id: dataset-alpha",
                "stories:",
                "  - raw_story_001_medium.yaml",
                "total_checks: 2",
                "generated_runner: copilot",
                "generated_model: gpt-5.5",
                "compatibility_story_ids:",
                "  - raw_story_001_medium",
            ]),
            encoding="utf-8",
        )
        try:
            corpus.EVAL_STORIES_ROOT = stories_root
            listing = self.client.get("/corpus")
            self.assertEqual(listing.status_code, 200)
            payload = listing.json()
            self.assertEqual(payload["count"], 1)
            item = payload["items"][0]
            self.assertEqual(item["id"], "raw_story_001_medium")
            self.assertEqual(item["suite_tier"], "medium")
            self.assertEqual(item["dataset_id"], "dataset-alpha")
            self.assertEqual(item["acceptance_criteria_count"], 2)
            self.assertEqual(item["check_count"], 2)
            self.assertEqual(item["generated_runner"], "copilot")
            self.assertEqual(item["generated_model"], "gpt-5.5")
            self.assertTrue(item["story_file"].endswith("raw_story_001_medium.json"))
            self.assertTrue(item["suite_story_path"].endswith("suites/medium/raw_story_001_medium.yaml"))

            detail = self.client.get("/corpus/raw_story_001_medium")
            self.assertEqual(detail.status_code, 200)
            detail_payload = detail.json()
            self.assertEqual(len(detail_payload["acceptance_criteria"]), 2)
            self.assertEqual(detail_payload["workflow"], "staged-delivery")
            self.assertEqual(detail_payload["agent"], "code-reviewer")
        finally:
            corpus.EVAL_STORIES_ROOT = original_root

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

    def test_medium__evaluate_summary_uses_persisted_weighted_score_when_present(self):
        from server import corpus, db

        original_root = corpus.EVAL_STORIES_ROOT
        stories_root = Path(self.tmpdir) / "scored-corpus" / "stories"
        stories_root.mkdir(parents=True, exist_ok=True)
        (stories_root / "EVAL-SCORED.json").write_text(
            json.dumps({
                "change_id": "EVAL-SCORED",
                "title": "Scored story",
                "description": "Story with future persisted metric column.",
                "acceptance_criteria": ["Check score"],
            }),
            encoding="utf-8",
        )
        try:
            corpus.EVAL_STORIES_ROOT = stories_root
            with db.cursor() as cur:
                try:
                    cur.execute("ALTER TABLE jobs ADD COLUMN score_weighted_composite REAL")
                except sqlite3.OperationalError as exc:
                    self.assertIn("duplicate column", str(exc).lower())
            submitted_at = db.now_iso()
            db.insert_job({
                "id": "job_eval_scored",
                "change_id": "EVAL-SCORED",
                "status": "failed",
                "run_kind": "evaluation",
                "mode": "live",
                "runner": "claude",
                "repo": str(Path.cwd()),
                "submitted_at": submitted_at,
            })
            db.update_job("job_eval_scored", score_weighted_composite=0.75)

            summary = self.client.get("/evaluate/summary").json()
            row = next(item for item in summary["rows"] if item["task"] == "EVAL-SCORED")
            self.assertEqual(row["current"], 75)
            self.assertEqual(row["baseline"], 75)
            self.assertEqual(row["score_source"], "score_weighted_composite")
        finally:
            corpus.EVAL_STORIES_ROOT = original_root

    def test_medium__evaluate_summary_does_not_mix_scores_with_status_baseline(self):
        from server import corpus, db

        original_root = corpus.EVAL_STORIES_ROOT
        stories_root = Path(self.tmpdir) / "mixed-score-corpus" / "stories"
        stories_root.mkdir(parents=True, exist_ok=True)
        (stories_root / "EVAL-MIXED.json").write_text(
            json.dumps({
                "change_id": "EVAL-MIXED",
                "title": "Mixed metric story",
                "description": "Story with scores only on newer runs.",
                "acceptance_criteria": ["Check metric fallback"],
            }),
            encoding="utf-8",
        )
        try:
            corpus.EVAL_STORIES_ROOT = stories_root
            with db.cursor() as cur:
                try:
                    cur.execute("ALTER TABLE jobs ADD COLUMN score_weighted_composite REAL")
                except sqlite3.OperationalError as exc:
                    self.assertIn("duplicate column", str(exc).lower())
            for index, status in enumerate(("succeeded", "succeeded", "failed", "failed"), start=1):
                job_id = f"job_eval_mixed_{index}"
                db.insert_job({
                    "id": job_id,
                    "change_id": "EVAL-MIXED",
                    "status": status,
                    "run_kind": "evaluation",
                    "mode": "live",
                    "runner": "claude",
                    "repo": str(Path.cwd()),
                    "submitted_at": f"2026-01-01T00:00:0{index}Z",
                })
                if index >= 3:
                    db.update_job(job_id, score_weighted_composite=0.86)

            summary = self.client.get("/evaluate/summary").json()
            row = next(item for item in summary["rows"] if item["task"] == "EVAL-MIXED")
            self.assertEqual(row["current"], 50)
            self.assertEqual(row["baseline"], 100)
            self.assertEqual(row["score_source"], "job_status")
        finally:
            corpus.EVAL_STORIES_ROOT = original_root

    def test_medium__evaluate_summary_uses_status_when_some_short_history_scores_missing(self):
        from server import corpus, db

        original_root = corpus.EVAL_STORIES_ROOT
        stories_root = Path(self.tmpdir) / "partial-score-corpus" / "stories"
        stories_root.mkdir(parents=True, exist_ok=True)
        (stories_root / "EVAL-PARTIAL.json").write_text(
            json.dumps({
                "change_id": "EVAL-PARTIAL",
                "title": "Partial score story",
                "description": "Story with scores missing from a short run history.",
                "acceptance_criteria": ["Check short-history metric fallback"],
            }),
            encoding="utf-8",
        )
        try:
            corpus.EVAL_STORIES_ROOT = stories_root
            with db.cursor() as cur:
                try:
                    cur.execute("ALTER TABLE jobs ADD COLUMN score_weighted_composite REAL")
                except sqlite3.OperationalError as exc:
                    self.assertIn("duplicate column", str(exc).lower())
            for index, status in enumerate(("succeeded", "succeeded", "failed"), start=1):
                job_id = f"job_eval_partial_{index}"
                db.insert_job({
                    "id": job_id,
                    "change_id": "EVAL-PARTIAL",
                    "status": status,
                    "run_kind": "evaluation",
                    "mode": "live",
                    "runner": "claude",
                    "repo": str(Path.cwd()),
                    "submitted_at": f"2026-01-01T00:01:0{index}Z",
                })
                if index >= 2:
                    db.update_job(job_id, score_weighted_composite=0.70)

            summary = self.client.get("/evaluate/summary").json()
            row = next(item for item in summary["rows"] if item["task"] == "EVAL-PARTIAL")
            self.assertEqual(row["current"], 67)
            self.assertEqual(row["baseline"], 67)
            self.assertEqual(row["score_source"], "job_status")
        finally:
            corpus.EVAL_STORIES_ROOT = original_root

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

    def test_medium__submit_generated_evaluation_story_queues_job_hidden_from_regular_runs(self):
        from server import corpus

        original_root = corpus.EVAL_STORIES_ROOT
        stories_root = Path(self.tmpdir) / "generated-run-corpus" / "stories"
        stories_root.mkdir(parents=True, exist_ok=True)
        (stories_root / "generated_story_easy.json").write_text(
            json.dumps({
                "change_id": "generated_story_easy",
                "title": "Generated easy story",
                "description": "A workflow-compatible generated evaluation story.",
                "acceptance_criteria": ["Generated check"],
                "metadata": {
                    "eval_story_id": "generated-story",
                    "suite_tier": "easy",
                    "dataset_id": "dataset-beta",
                },
                "raw_metadata": {
                    "suite_yaml": "suites/easy/generated_story_easy.yaml",
                    "raw_story_id": "generated-story",
                    "tier": "easy",
                    "dataset_id": "dataset-beta",
                },
            }),
            encoding="utf-8",
        )
        try:
            corpus.EVAL_STORIES_ROOT = stories_root
            r = self.client.post(
                "/evaluate/runs",
                json={"repo": str(Path.cwd()), "story_id": "generated_story_easy", "runner": "claude", "mode": "live"},
            )
            self.assertEqual(r.status_code, 200)
            jid = r.json()["job_id"]
            detail = self.client.get(f"/runs/{jid}").json()
            self.assertEqual(detail["change_id"], "generated_story_easy")
            self.assertEqual(detail["run_kind"], "evaluation")
            self.assertTrue(detail["story_file"].endswith("generated_story_easy.json"))
            regular_listing = self.client.get("/runs").json()
            self.assertNotIn(jid, [item["id"] for item in regular_listing["items"]])
            eval_listing = self.client.get("/runs?run_kind=evaluation").json()
            self.assertIn(jid, [item["id"] for item in eval_listing["items"]])
        finally:
            corpus.EVAL_STORIES_ROOT = original_root

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
