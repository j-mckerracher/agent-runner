"""Smoke tests for the local FastAPI server."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


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
        self.assertIn("Log level", r.text)
        self.assertIn("Open current run in Opik", r.text)
        self.assertIn("Open Opik evaluation workspace", r.text)
        self.assertIn("Opik trace timeline", r.text)
        self.assertIn("Run Telemetry", r.text)
        self.assertIn('data-view="telemetry"', r.text)
        self.assertIn('id="trace-summary"', r.text)
        self.assertIn('id="eval-trace-summary"', r.text)
        self.assertIn('id="s-opik-status"', r.text)
        self.assertIn("Start evaluation run", r.text)
        self.assertIn("Select an agent to view its latest prompt.", r.text)
        self.assertIn('id="f-repo"', r.text)
        self.assertIn('id="f-repo-toggle"', r.text)
        self.assertIn('id="f-repo-menu"', r.text)
        self.assertIn('id="eval-repo"', r.text)
        self.assertIn('id="eval-repo-toggle"', r.text)
        self.assertIn('id="eval-repo-menu"', r.text)
        self.assertIn('id="eval-sha"', r.text)
        self.assertIn('id="eval-sha-custom"', r.text)
        self.assertIn('id="eval-baseline"', r.text)
        self.assertIn('id="eval-detail"', r.text)
        self.assertIn('id="eval-warnings"', r.text)
        self.assertIn('id="s-repo-base-dir"', r.text)

    def test_medium__settings_returns_runner_models_and_efforts(self):
        s = self.client.get("/settings").json()
        self.assertIn("runner_models", s)
        self.assertIn("claude", s["runner_models"])
        self.assertIn("api", s)
        self.assertIn("opik", s)
        self.assertIn("azure_devops", s)
        self.assertIn("dashboard_url", s["opik"])
        self.assertIn("repo_paths", s)
        self.assertIn("repo_path_options", s)
        self.assertIn("eval_bootstrap", s)
        self.assertIn("target_sha", s["eval_bootstrap"])

    def test_medium__azure_devops_status_reports_manual_availability(self):
        status = self.client.get("/integrations/azure-devops/status").json()
        self.assertTrue(status["manual"]["available"])
        self.assertIn("cli", status)
        self.assertIn("mcp", status)

    def test_medium__settings_reads_bootstrap_eval_target_sha_from_repo_env(self):
        root = Path(self.tmpdir) / "runner-root"
        root.mkdir(parents=True, exist_ok=True)
        (root / ".env").write_text('EVAL_TARGET_SHA="abc123"\n', encoding="utf-8")

        with patch("server.routes.settings.RUNNER_ROOT", root):
            s = self.client.get("/settings").json()

        self.assertEqual(s["eval_bootstrap"]["target_sha"], "abc123")

    def test_medium__settings_put_partial_merges_and_persists(self):
        r = self.client.put("/settings", json={"concurrency": {"max_running_jobs": 4}})
        self.assertEqual(r.status_code, 200)
        cfg = self.client.get("/settings").json()
        self.assertEqual(cfg["concurrency"]["max_running_jobs"], 4)
        self.assertIn("api", cfg)

    def test_medium__settings_repo_path_options_are_immediate_subdirs(self):
        root = Path(self.tmpdir) / "repo-base"
        repo_a = root / "repo-a"
        repo_b = root / "repo-b"
        hidden = root / ".hidden-repo"
        nested = repo_a / "nested"
        repo_a.mkdir(parents=True, exist_ok=True)
        repo_b.mkdir(parents=True, exist_ok=True)
        hidden.mkdir(parents=True, exist_ok=True)
        nested.mkdir(parents=True, exist_ok=True)
        (root / "not-a-dir.txt").write_text("ignore me", encoding="utf-8")

        r = self.client.put("/settings", json={"repo_paths": {"base_dir": str(root), "custom_values": []}})
        self.assertEqual(r.status_code, 200)
        cfg = self.client.get("/settings").json()
        self.assertEqual(cfg["repo_paths"]["base_dir"], str(root))
        self.assertEqual(cfg["repo_path_options"], sorted([str(repo_a.resolve()), str(repo_b.resolve())]))
        self.assertNotIn(str(nested.resolve()), cfg["repo_path_options"])
        self.assertNotIn(str(hidden.resolve()), cfg["repo_path_options"])

    def test_medium__settings_repo_path_custom_values_persist(self):
        custom_values = ["~/Code/custom-repo", "/tmp/custom-repo"]
        r = self.client.put("/settings", json={"repo_paths": {"custom_values": custom_values}})
        self.assertEqual(r.status_code, 200)
        cfg = self.client.get("/settings").json()
        self.assertEqual(cfg["repo_paths"]["custom_values"], custom_values)

    def test_medium__settings_repo_path_invalid_shapes_rejected(self):
        cases = [
            {"repo_paths": "not-a-dict"},
            {"repo_paths": {"base_dir": 123}},
            {"repo_paths": {"custom_values": "not-a-list"}},
            {"repo_paths": {"custom_values": [""]}},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                r = self.client.put("/settings", json=payload)
                self.assertEqual(r.status_code, 422)

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

    def test_medium__settings_put_accepts_runner_alias_transport_config(self):
        payload = {
            "runner_aliases": {
                "openai-compat-cloud": {
                    "provider": "openai-compat",
                    "model": "llama3.3:70b",
                    "base_url": "https://openai-compat.example.com",
                    "api_key_env": "OPENAI_COMPAT_CLOUD_API_KEY",
                    "extra_headers": {"X-Tenant": "acme"},
                    "litellm_extra_body": {"session": "enterprise"},
                    "num_retries": 10,
                    "retry_multiplier": 3.0,
                    "retry_min_wait": 12,
                    "retry_max_wait": 180,
                    "timeout": 600,
                }
            }
        }
        r = self.client.put("/settings", json=payload)
        self.assertEqual(r.status_code, 200)
        cfg = self.client.get("/settings").json()
        alias = cfg["runner_aliases"]["openai-compat-cloud"]
        self.assertEqual(alias["provider"], "openai-compat")
        self.assertEqual(alias["model"], "llama3.3:70b")
        self.assertEqual(alias["base_url"], "https://openai-compat.example.com")
        self.assertEqual(alias["api_key_env"], "OPENAI_COMPAT_CLOUD_API_KEY")
        self.assertEqual(alias["extra_headers"], {"X-Tenant": "acme"})
        self.assertEqual(alias["litellm_extra_body"], {"session": "enterprise"})
        self.assertEqual(alias["num_retries"], 10)
        self.assertEqual(alias["retry_multiplier"], 3.0)
        self.assertEqual(alias["retry_min_wait"], 12)
        self.assertEqual(alias["retry_max_wait"], 180)
        self.assertEqual(alias["timeout"], 600)
        self.assertEqual(cfg["runner_models"]["openai-compat-cloud"], ["openai-compat/llama3.3:70b"])

    def test_easy__agents_lists_known_enabled_agents(self):
        r = self.client.get("/agents")
        self.assertEqual(r.status_code, 200)
        names = [a["name"] for a in r.json()["items"]]
        self.assertIn("intake", names)
        self.assertNotIn("lessons-optimizer-hyperagent", names)

    def test_medium__disabled_agent_detail_returns_404(self):
        r = self.client.get("/agents/lessons-optimizer-hyperagent")
        self.assertEqual(r.status_code, 404)

    def test_medium__agent_detail_returns_latest_prompt(self):
        r = self.client.get("/agents/intake")
        self.assertEqual(r.status_code, 200)
        payload = r.json()
        self.assertEqual(payload["name"], "intake")
        self.assertTrue(payload["version"].startswith("v"))
        self.assertIn("agent-definition-source/intake/", payload["prompt_file"])
        self.assertTrue(payload["prompt_file"].endswith("/prompt.md"))
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
        self.assertEqual(payload["source"], "benchmark_reports")
        self.assertIn("overall_pass_rate", payload)
        self.assertIn("rows", payload)
        self.assertIn("warnings", payload)

    def test_medium__evaluate_summary_is_benchmark_report_first(self):
        from server import db, evaluate

        original_reports = evaluate.DEFAULT_REPORTS
        reports_root = Path(self.tmpdir) / "eval-reports"
        reports_root.mkdir(parents=True, exist_ok=True)
        (reports_root / "2026-05-22-120000-medium.json").write_text(
            json.dumps(
                {
                    "created_at": "2026-05-22T12:00:00Z",
                    "repo": "/repo",
                    "sha": "abc123",
                    "runner": "claude",
                    "model": "claude-sonnet-4-6",
                    "runs": 1,
                    "summary": {
                        "trend": "insufficient data",
                        "quality": {"weighted_score": 0.5},
                        "reliability": {"runs": 1, "pass_rate": 0.0},
                        "efficiency": {"wall_seconds_mean": 12.5, "tokens_total_mean": 1234},
                        "warnings": ["No baseline selected.", "Only one run; reliability unknown."],
                    },
                    "results": [
                        {
                            "name": "medium",
                            "run_id": "medium-t1",
                            "trial_index": 1,
                            "status": "FAIL",
                            "error": "hidden tests failed",
                            "quality": {"weighted_score": 0.5, "hidden_tests_skipped": 0},
                            "metrics": {"wall_seconds": 12.5, "tokens_total": 1234},
                            "story": {
                                "title": "Medium benchmark",
                                "description": "Story detail.",
                                "acceptance_criteria": ["AC1: First.", "AC2: Second."],
                            },
                            "hidden_tests": {
                                "skipped": 0,
                                "ac_results": {
                                    "AC1": {
                                        "tests": ["test_ac1_first"],
                                        "passed": True,
                                        "cases": [{"name": "test_ac1_first", "status": "passed", "message": ""}],
                                    },
                                    "AC2": {
                                        "tests": ["test_ac2_second"],
                                        "passed": False,
                                        "cases": [{"name": "test_ac2_second", "status": "failed", "message": "bad"}],
                                    },
                                },
                            },
                            "artifacts": {"story": "/tmp/story.json"},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        try:
            evaluate.DEFAULT_REPORTS = reports_root
            db.insert_job({
                "id": "job_eval_ignored_by_summary",
                "change_id": "EVAL-LEGACY",
                "status": "succeeded",
                "run_kind": "evaluation",
                "mode": "live",
                "runner": "claude",
                "repo": str(Path.cwd()),
                "submitted_at": db.now_iso(),
            })
            summary = self.client.get("/evaluate/summary").json()
            self.assertEqual(summary["source"], "benchmark_reports")
            self.assertEqual(summary["total_runs"], 1)
            self.assertEqual(summary["overall_pass_rate"], 0)
            self.assertEqual(summary["quality_score"], 0.5)
            self.assertIn("No baseline selected.", summary["warnings"])
            row = summary["rows"][0]
            self.assertEqual(row["task"], "medium")
            self.assertEqual(row["score_source"], "benchmark_report")
            self.assertEqual(row["story"]["description"], "Story detail.")
            self.assertEqual(row["details"][0]["hidden_tests"]["ac_results"]["AC2"]["cases"][0]["message"], "bad")
        finally:
            evaluate.DEFAULT_REPORTS = original_reports

    def test_medium__submit_run_inserts_queued_job(self):
        from server import db
        from server.events import EventBus
        from server.runner_proc import JobProcess

        r = self.client.post(
            "/runs",
            json={
                "repo": self.tmpdir,
                "change_id": "TEST-AC-001",
                "runner": "claude",
                "mode": "live",
                "log_level": "INFO",
            },
        )
        self.assertEqual(r.status_code, 200)
        jid = r.json()["job_id"]
        listing = self.client.get("/runs").json()
        self.assertGreaterEqual(listing["count"], 1)
        detail = self.client.get(f"/runs/{jid}").json()
        self.assertEqual(detail["change_id"], "TEST-AC-001")
        self.assertEqual(detail["run_kind"], "regular")
        self.assertEqual(detail["log_level"], "info")
        self.assertIn(detail["status"], ("queued", "running", "failed", "cancelled"))

        job = db.get_job(jid)
        self.assertIsNotNone(job)
        cmd = JobProcess(job, EventBus(), None)._build_cmd()
        self.assertIn("--log-level", cmd)
        self.assertEqual(cmd[cmd.index("--log-level") + 1], "info")

    def test_medium__legacy_evaluation_run_route_is_removed(self):
        r = self.client.post(
            "/evaluate/runs",
            json={"repo": self.tmpdir, "story_id": "EVAL-001", "runner": "claude", "mode": "live"},
        )
        self.assertEqual(r.status_code, 405)

    def test_medium__list_evaluate_runs_returns_only_evaluation_kinds(self):
        from server import db

        db.insert_job({
            "id": "eval_hist_001",
            "change_id": "EVAL-HIST-001",
            "status": "succeeded",
            "run_kind": "evaluation",
            "mode": "live",
            "runner": "claude",
            "repo": str(Path.cwd()),
            "submitted_at": "2026-01-01T00:00:00Z",
        })
        db.insert_job({
            "id": "eval_hist_002",
            "change_id": "EVAL-HIST-002",
            "status": "failed",
            "run_kind": "benchmark_evaluation",
            "mode": "live",
            "runner": "copilot",
            "repo": str(Path.cwd()),
            "submitted_at": "2026-01-01T00:00:01Z",
        })
        db.insert_job({
            "id": "regular_hist_001",
            "change_id": "RUN-HIST-001",
            "status": "succeeded",
            "run_kind": "regular",
            "mode": "live",
            "runner": "claude",
            "repo": str(Path.cwd()),
            "submitted_at": "2026-01-01T00:00:02Z",
        })

        r = self.client.get("/evaluate/runs?limit=10")
        self.assertEqual(r.status_code, 200)
        payload = r.json()
        self.assertEqual(payload["count"], 2)
        ids = [item["id"] for item in payload["items"]]
        self.assertEqual(ids, ["eval_hist_002", "eval_hist_001"])

    def test_medium__submit_benchmark_evaluation_run_queues_eval_runner_job(self):
        from server import db
        from server.events import EventBus
        from server.runner_proc import JobProcess

        r = self.client.post(
            "/evaluate/benchmark-runs",
            json={
                "repo": self.tmpdir,
                "sha": "abc123",
                "runner": "claude",
                "difficulties": ["easy"],
                "runs": 2,
                "project_test_command": "python3 -m pytest -q",
                "compare_to": "/tmp/baseline.json",
            },
        )
        self.assertEqual(r.status_code, 200)
        jid = r.json()["job_id"]
        detail = self.client.get(f"/runs/{jid}").json()
        self.assertEqual(detail["run_kind"], "benchmark_evaluation")
        job = db.get_job(jid)
        cmd = JobProcess(job, EventBus(), None)._build_cmd()
        self.assertIn(str(Path.cwd() / "eval" / "runner.py"), cmd)
        self.assertIn("--difficulty", cmd)
        self.assertIn("easy", cmd)
        self.assertIn("--runs", cmd)
        self.assertIn("2", cmd)
        self.assertIn("--compare-to", cmd)
        self.assertEqual(cmd[cmd.index("--compare-to") + 1], "/tmp/baseline.json")

    def test_medium__submit_run_rejects_invalid_runner(self):
        r = self.client.post(
            "/runs",
            json={"repo": "/tmp/none", "change_id": "TEST-S2", "runner": "bogus", "mode": "live"},
        )
        self.assertIn(r.status_code, (400, 422))

    def test_medium__submit_run_accepts_agent_llm_overrides(self):
        from core.workflow_inputs import WorkflowInput
        from server import db
        from server.events import EventBus
        from server.runner_proc import JobProcess

        with patch(
            "server.routes.runs.resolve_workflow_input",
            return_value=WorkflowInput(
                repo=self.tmpdir,
                change_id="TEST-AGENT-LLM",
                intake_mode="synthetic",
                intake_source="",
            ),
        ):
            r = self.client.post(
                "/runs",
                json={
                    "repo": self.tmpdir,
                    "change_id": "TEST-AGENT-LLM",
                    "runner": "copilot",
                    "mode": "live",
                    "model": "gpt-5.4",
                    "agent_llm_overrides": {
                        "qa-engineer": {"runner": "codex", "model": "gpt-5.5"},
                        "qa-evaluator": {"model": "gpt-5.2"},
                    },
                },
            )

        self.assertEqual(r.status_code, 200)
        job = db.get_job(r.json()["job_id"])
        self.assertIn('"qa-engineer"', job["agent_llm_overrides"])
        cmd = JobProcess(job, EventBus(), None)._build_cmd()
        self.assertIn("qa-engineer=codex", cmd)
        self.assertIn("qa-evaluator=gpt-5.2", cmd)

    def test_medium__submit_run_rejects_invalid_agent_llm_overrides(self):
        invalid_payloads = [
            {"not-an-agent": {"runner": "codex"}},
            {"qa-engineer": {"runner": "bogus"}},
            {"qa-engineer": {"runner": "claude", "model": "definitely-not-a-valid-model"}},
        ]
        for overrides in invalid_payloads:
            with self.subTest(overrides=overrides):
                r = self.client.post(
                    "/runs",
                    json={
                        "repo": self.tmpdir,
                        "change_id": "TEST-AGENT-LLM-BAD",
                        "runner": "copilot",
                        "mode": "live",
                        "model": "gpt-5.4",
                        "agent_llm_overrides": overrides,
                    },
                )
                self.assertEqual(r.status_code, 400)

    def test_medium__submit_run_rejects_invalid_log_level(self):
        r = self.client.post(
            "/runs",
            json={
                "repo": self.tmpdir,
                "change_id": "TEST-S2-LOG",
                "runner": "claude",
                "mode": "live",
                "log_level": "verbose",
            },
        )
        self.assertEqual(r.status_code, 422)

    def test_medium__submit_run_rejects_both_ado_and_story(self):
        r = self.client.post(
            "/runs",
            json={
                "repo": self.tmpdir,
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
                "repo": self.tmpdir,
                "change_id": "EVAL-001",
                "runner": "claude",
                "mode": "live",
                "run_kind": "evaluation",
            },
        )
        self.assertEqual(r.status_code, 400)

    def test_medium__legacy_evaluate_runs_route_does_not_queue_jobs(self):
        from server import db

        before = len(db.list_jobs(run_kind="evaluation", limit=500))
        r = self.client.post(
            "/evaluate/runs",
            json={"repo": "/tmp/none", "story_id": "EVAL-DOES-NOT-EXIST", "runner": "claude", "mode": "live"},
        )
        self.assertEqual(r.status_code, 405)
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
                    "repo": self.tmpdir,
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

    def test_medium__submit_run_accepts_manual_story_and_persists_manual_story_file(self):
        from server import db
        from server.events import EventBus
        from server.runner_proc import JobProcess

        r = self.client.post(
            "/runs",
            json={
                "repo": self.tmpdir,
                "runner": "claude",
                "mode": "live",
                "manual_story": {
                    "work_item_id": "123456",
                    "title": "Manual story",
                    "description": "desc",
                    "acceptance_criteria": "- first\n- second",
                },
            },
        )
        self.assertEqual(r.status_code, 200)
        jid = r.json()["job_id"]
        detail = self.client.get(f"/runs/{jid}").json()
        self.assertEqual(detail["change_id"], "WI-123456")
        self.assertEqual(detail["story_source"], "manual")

        job = db.get_job(jid)
        self.assertIsNotNone(job)
        manual_story_file = Path(job["manual_story_file"]).resolve()
        self.assertTrue(str(manual_story_file).startswith(str((Path(self.tmpdir) / "job-inputs").resolve())))
        self.assertTrue(manual_story_file.is_file())
        cmd = JobProcess(job, EventBus(), None)._build_cmd()
        self.assertIn("--manual-story-file", cmd)
        self.assertNotIn("--story-file", cmd)
        self.assertNotIn("--ado-url", cmd)

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
                  repo TEXT NOT NULL,
                  ado_url TEXT,
                  story_file TEXT,
                  extra_context TEXT,
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
                    self.assertIn("log_level", columns)
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
            json={"repo": self.tmpdir, "change_id": "TEST-AC-001", "runner": "claude", "mode": "live"},
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
            json={"repo": self.tmpdir, "change_id": "TEST-AC-001", "runner": "claude", "mode": "live"},
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

    def test_medium__submit_run_rejects_missing_repo_path(self):
        r = self.client.post(
            "/runs",
            json={
                "repo": "/absolute/path/to/your/repo",
                "change_id": "TEST-AC-001",
                "runner": "claude",
                "mode": "live",
            },
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("Repository path not found", r.text)


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

    # -- model validation matrix tests ----------------------------------------

    def test_medium__submit_run_rejects_invalid_model(self):
        r = self.client.post(
            "/runs",
            json={
                "repo": self.tmpdir,
                "change_id": "TEST-MODEL-001",
                "runner": "claude",
                "mode": "live",
                "model": "definitely-not-a-valid-model",
            },
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("definitely-not-a-valid-model", r.json()["detail"])

    def test_medium__submit_run_with_valid_model_per_runner(self):
        from core.runner_models import RUNNER_MODEL_CHOICES
        from core.workflow_inputs import WorkflowInput

        for runner, models in RUNNER_MODEL_CHOICES.items():
            for model in models:
                with self.subTest(runner=runner, model=model):
                    with patch(
                        "server.routes.runs.resolve_workflow_input",
                        return_value=WorkflowInput(
                            repo=self.tmpdir,
                            change_id="TEST-MODEL-MTX",
                            intake_mode="synthetic",
                            intake_source="",
                        ),
                    ):
                        r = self.client.post(
                            "/runs",
                            json={
                                "repo": self.tmpdir,
                                "change_id": "TEST-MODEL-MTX",
                                "runner": runner,
                                "mode": "live",
                                "model": model,
                            },
                        )
                        self.assertEqual(
                            r.status_code, 200,
                            f"runner={runner} model={model}: expected 200, got {r.status_code} {r.json().get('detail', '')}"
                        )
                        self.assertIn("job_id", r.json())

    def test_medium__submit_benchmark_rejects_invalid_model(self):
        r = self.client.post(
            "/evaluate/benchmark-runs",
            json={
                "repo": self.tmpdir,
                "sha": "abc123",
                "runner": "claude",
                "difficulties": ["easy"],
                "model": "definitely-not-a-valid-model",
            },
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("definitely-not-a-valid-model", r.json()["detail"])

    def test_medium__submit_benchmark_with_valid_model_per_runner(self):
        from core.runner_models import RUNNER_MODEL_CHOICES

        for runner, models in RUNNER_MODEL_CHOICES.items():
            for model in models:
                with self.subTest(runner=runner, model=model):
                    r = self.client.post(
                        "/evaluate/benchmark-runs",
                        json={
                            "repo": self.tmpdir,
                            "sha": "abc123",
                            "runner": runner,
                            "difficulties": ["easy"],
                            "model": model,
                        },
                    )
                    self.assertEqual(
                        r.status_code, 200,
                        f"runner={runner} model={model}: expected 200, got {r.status_code} {r.json().get('detail', '')}"
                    )
                    self.assertIn("job_id", r.json())

    def test_medium__submit_run_accepts_arbitrary_openai_compat_model(self):
        from core.workflow_inputs import WorkflowInput

        with patch(
            "server.routes.runs.resolve_workflow_input",
            return_value=WorkflowInput(
                repo=self.tmpdir,
                change_id="TEST-OC-ARB",
                intake_mode="synthetic",
                intake_source="",
            ),
        ):
            r = self.client.post(
                "/runs",
                json={
                    "repo": self.tmpdir,
                    "change_id": "TEST-OC-ARB",
                    "runner": "openai-compat",
                    "mode": "live",
                    "model": "my-custom-model:latest",
                },
            )
            self.assertEqual(r.status_code, 200)
            self.assertIn("job_id", r.json())

    def test_medium__submit_benchmark_accepts_arbitrary_openai_compat_model(self):
        r = self.client.post(
            "/evaluate/benchmark-runs",
            json={
                "repo": self.tmpdir,
                "sha": "abc123",
                "runner": "openai-compat",
                "difficulties": ["easy"],
                "model": "ollama/llama3:70b",
            },
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn("job_id", r.json())

    def test_medium__settings_put_accepts_arbitrary_openai_compat_default(self):
        r = self.client.put("/settings", json={
            "agent_model_defaults": {
                "qa-evaluator": {"openai-compat": "my-local-model:latest"}
            }
        })
        self.assertEqual(r.status_code, 200)
        cfg = self.client.get("/settings").json()
        self.assertEqual(
            cfg["agent_model_defaults"]["qa-evaluator"]["openai-compat"],
            "my-local-model:latest",
        )

    def test_medium__settings_put_rejects_invalid_closed_runner_model(self):
        r = self.client.put("/settings", json={
            "agent_model_defaults": {
                "intake": {"claude": "definitely-not-a-model"}
            }
        })
        self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main()
