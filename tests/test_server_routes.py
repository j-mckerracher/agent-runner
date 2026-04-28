"""Tests for FastAPI routes via TestClient."""
import os
import tempfile
import unittest

# Redirect data dir to temp dir before importing server modules
_TMP_DIR = tempfile.mkdtemp()
os.environ["AGENT_RUNNER_DATA_DIR"] = _TMP_DIR

from fastapi.testclient import TestClient

from server.app import create_app
from server.db import insert_job


def _make_job(job_id: str, status: str = "queued", change_id: str = "TEST-ROUTE-001") -> dict:
    return {
        "id": job_id,
        "change_id": change_id,
        "parent_job_id": None,
        "status": status,
        "mode": "live",
        "runner": "claude",
        "model": "claude-haiku-4-5-20251001",
        "copilot_effort": None,
        "repo": "/tmp/repo",
        "ado_url": None,
        "story_file": None,
        "extra_context": None,
        "skip_materialize": 0,
        "submitted_at": "2026-04-28T00:00:00",
        "started_at": None,
        "finished_at": None,
        "exit_code": None,
        "error_message": None,
        "pid": None,
        "events_path": None,
        "cassette_path": None,
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_usd": 0,
        "current_stage": None,
    }


def _get_app():
    return create_app()


class HealthRouteTests(unittest.TestCase):
    def test_easy__health_returns_ok(self):
        with TestClient(_get_app()) as client:
            r = client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

    def test_easy__health_returns_version(self):
        with TestClient(_get_app()) as client:
            r = client.get("/health")
        self.assertIn("version", r.json())


class RunsRouteTests(unittest.TestCase):
    def test_easy__list_runs_returns_list(self):
        with TestClient(_get_app()) as client:
            r = client.get("/runs")
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json(), list)

    def test_easy__get_nonexistent_run_returns_404(self):
        with TestClient(_get_app()) as client:
            r = client.get("/runs/job_does_not_exist_xyz")
        self.assertEqual(r.status_code, 404)

    def test_medium__submit_run_returns_job_id(self):
        payload = {
            "repo": "/tmp/test-repo",
            "runner": "claude",
            "model": "claude-haiku-4-5-20251001",
            "mode": "live",
        }
        with TestClient(_get_app()) as client:
            r = client.post("/runs", json=payload)
        self.assertEqual(r.status_code, 201)
        data = r.json()
        self.assertIn("job_id", data)
        self.assertEqual(data["status"], "queued")

    def test_medium__get_run_returns_correct_fields(self):
        # Insert a job directly
        job = _make_job("job_route_get_001", status="succeeded")
        insert_job(job)
        with TestClient(_get_app()) as client:
            r = client.get("/runs/job_route_get_001")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["id"], "job_route_get_001")
        self.assertEqual(data["status"], "succeeded")

    def test_medium__get_run_events_returns_list(self):
        job = _make_job("job_route_events_001", status="succeeded")
        insert_job(job)
        with TestClient(_get_app()) as client:
            r = client.get("/runs/job_route_events_001/events")
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json(), list)

    def test_medium__cancel_queued_run_sets_cancelled(self):
        job = _make_job("job_route_cancel_001b", status="queued")
        insert_job(job)
        with TestClient(_get_app()) as client:
            r = client.post("/runs/job_route_cancel_001b/cancel")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["cancelled"])
        from server.db import get_job
        updated = get_job("job_route_cancel_001b")
        self.assertEqual(updated["status"], "cancelled")

    def test_medium__cancel_nonexistent_run_returns_404(self):
        with TestClient(_get_app()) as client:
            r = client.post("/runs/job_not_found_xyz/cancel")
        self.assertEqual(r.status_code, 404)

    def test_medium__cancel_terminal_run_returns_409(self):
        job = _make_job("job_route_cancel_term_001b", status="succeeded")
        insert_job(job)
        with TestClient(_get_app()) as client:
            r = client.post("/runs/job_route_cancel_term_001b/cancel")
        self.assertEqual(r.status_code, 409)

    def test_medium__list_runs_filter_by_status(self):
        insert_job(_make_job("job_filter_run2", status="running", change_id="FILTER-003"))
        insert_job(_make_job("job_filter_que2", status="queued", change_id="FILTER-004"))
        with TestClient(_get_app()) as client:
            r = client.get("/runs?status=running")
        self.assertEqual(r.status_code, 200)
        ids = [j["id"] for j in r.json()]
        self.assertIn("job_filter_run2", ids)
        self.assertNotIn("job_filter_que2", ids)


class AgentsRouteTests(unittest.TestCase):
    def test_easy__agents_returns_list(self):
        with TestClient(_get_app()) as client:
            r = client.get("/agents")
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json(), list)

    def test_medium__agents_have_required_fields(self):
        with TestClient(_get_app()) as client:
            r = client.get("/agents")
        agents = r.json()
        if agents:
            a = agents[0]
            for field in ("name", "version", "bundle_hash", "tags"):
                self.assertIn(field, a, f"Missing field: {field}")


class CorpusRouteTests(unittest.TestCase):
    def test_easy__corpus_returns_list(self):
        with TestClient(_get_app()) as client:
            r = client.get("/corpus")
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json(), list)

    def test_medium__corpus_detail_404_for_unknown(self):
        with TestClient(_get_app()) as client:
            r = client.get("/corpus/EVAL-NONEXISTENT-XYZ")
        self.assertEqual(r.status_code, 404)

    def test_medium__corpus_detail_returns_story_fields(self):
        with TestClient(_get_app()) as client:
            r = client.get("/corpus")
            items = r.json()
            if not items:
                return  # No stories to test
            story_id = items[0]["id"]
            r2 = client.get(f"/corpus/{story_id}")
        self.assertEqual(r2.status_code, 200)
        data = r2.json()
        self.assertIn("acceptance_criteria", data)
        self.assertIn("pass_rate_history", data)


class EvaluateRouteTests(unittest.TestCase):
    def test_easy__evaluate_summary_returns_expected_shape(self):
        with TestClient(_get_app()) as client:
            r = client.get("/evaluate/summary")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        for field in ("total_runs", "succeeded", "failed", "overall_pass_rate", "per_change", "regressions"):
            self.assertIn(field, data, f"Missing field: {field}")

    def test_medium__evaluate_summary_pass_rate_is_none_or_float(self):
        with TestClient(_get_app()) as client:
            r = client.get("/evaluate/summary")
        rate = r.json()["overall_pass_rate"]
        self.assertTrue(rate is None or isinstance(rate, float))


class SettingsRouteTests(unittest.TestCase):
    def test_easy__get_settings_returns_config_shape(self):
        with TestClient(_get_app()) as client:
            r = client.get("/settings")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("api", data)
        self.assertIn("defaults", data)
        self.assertIn("concurrency", data)

    def test_medium__put_settings_persists_changes(self):
        with TestClient(_get_app()) as client:
            cfg = client.get("/settings").json()
            cfg["concurrency"]["max_running_jobs"] = 3
            r2 = client.put("/settings", json=cfg)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["concurrency"]["max_running_jobs"], 3)

    def test_medium__put_settings_rejects_invalid_port(self):
        with TestClient(_get_app()) as client:
            cfg = client.get("/settings").json()
            cfg["api"]["port"] = 99999
            r2 = client.put("/settings", json=cfg)
        self.assertEqual(r2.status_code, 422)

    def test_medium__put_settings_rejects_zero_port(self):
        with TestClient(_get_app()) as client:
            cfg = client.get("/settings").json()
            cfg["api"]["port"] = 0
            r2 = client.put("/settings", json=cfg)
        self.assertEqual(r2.status_code, 422)

    def test_medium__put_settings_rejects_zero_max_jobs(self):
        with TestClient(_get_app()) as client:
            cfg = client.get("/settings").json()
            cfg["concurrency"]["max_running_jobs"] = 0
            r2 = client.put("/settings", json=cfg)
        self.assertEqual(r2.status_code, 422)


if __name__ == "__main__":
    unittest.main()
