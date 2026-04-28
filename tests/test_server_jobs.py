"""Tests for SQLite schema, state transitions, and concurrency cap."""
import os
import tempfile
import threading
import unittest

# Point data dir to a temp dir so tests don't pollute ~/.agent-runner
_TMP_DIR = tempfile.mkdtemp()
os.environ["AGENT_RUNNER_DATA_DIR"] = _TMP_DIR

from server.db import insert_job, get_job, list_jobs, update_job, count_running_jobs, get_conn


def _make_job(job_id: str, status: str = "queued", change_id: str = "TEST-001") -> dict:
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


class SchemaTests(unittest.TestCase):
    def test_easy__insert_and_get_job(self):
        job = _make_job("job_schema_001")
        insert_job(job)
        fetched = get_job("job_schema_001")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["id"], "job_schema_001")
        self.assertEqual(fetched["status"], "queued")

    def test_easy__get_nonexistent_returns_none(self):
        result = get_job("job_does_not_exist_xyz")
        self.assertIsNone(result)

    def test_medium__list_jobs_by_status(self):
        insert_job(_make_job("job_list_run1", status="running"))
        insert_job(_make_job("job_list_que1", status="queued"))
        running = list_jobs(status="running")
        ids = [j["id"] for j in running]
        self.assertIn("job_list_run1", ids)
        self.assertNotIn("job_list_que1", ids)

    def test_medium__list_jobs_by_change_id(self):
        insert_job(_make_job("job_cid_001", change_id="CHANGE-ABC"))
        insert_job(_make_job("job_cid_002", change_id="CHANGE-XYZ"))
        results = list_jobs(change_id="CHANGE-ABC")
        ids = [j["id"] for j in results]
        self.assertIn("job_cid_001", ids)
        self.assertNotIn("job_cid_002", ids)

    def test_medium__update_job_status(self):
        insert_job(_make_job("job_update_001"))
        update_job("job_update_001", {"status": "running", "pid": 12345})
        fetched = get_job("job_update_001")
        self.assertEqual(fetched["status"], "running")
        self.assertEqual(fetched["pid"], 12345)


class StateTransitionTests(unittest.TestCase):
    def test_easy__queued_to_running(self):
        insert_job(_make_job("job_trans_001", status="queued"))
        update_job("job_trans_001", {"status": "running", "started_at": "2026-04-28T01:00:00"})
        job = get_job("job_trans_001")
        self.assertEqual(job["status"], "running")
        self.assertIsNotNone(job["started_at"])

    def test_easy__running_to_succeeded(self):
        insert_job(_make_job("job_trans_002", status="running"))
        update_job("job_trans_002", {
            "status": "succeeded",
            "finished_at": "2026-04-28T02:00:00",
            "exit_code": 0,
        })
        job = get_job("job_trans_002")
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["exit_code"], 0)

    def test_easy__running_to_failed(self):
        insert_job(_make_job("job_trans_003", status="running"))
        update_job("job_trans_003", {
            "status": "failed",
            "finished_at": "2026-04-28T03:00:00",
            "exit_code": 1,
            "error_message": "subprocess error",
        })
        job = get_job("job_trans_003")
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["exit_code"], 1)

    def test_medium__parent_child_linkage(self):
        insert_job(_make_job("job_parent_001", status="running"))
        child = _make_job("job_child_001", status="queued")
        child["parent_job_id"] = "job_parent_001"
        insert_job(child)
        fetched = get_job("job_child_001")
        self.assertEqual(fetched["parent_job_id"], "job_parent_001")


class ConcurrencyCapTests(unittest.TestCase):
    def test_medium__count_running_jobs(self):
        # Insert known running jobs and check count
        insert_job(_make_job("job_conc_001", status="running"))
        insert_job(_make_job("job_conc_002", status="running"))
        insert_job(_make_job("job_conc_003", status="queued"))
        count = count_running_jobs()
        # Should be at least 2 (there may be others from other tests)
        self.assertGreaterEqual(count, 2)

    def test_medium__list_jobs_pagination(self):
        for i in range(5):
            insert_job(_make_job(f"job_page_{i:03d}", change_id=f"PAGE-{i:03d}"))
        page1 = list_jobs(limit=3, offset=0)
        page2 = list_jobs(limit=3, offset=3)
        self.assertLessEqual(len(page1), 3)
        # page2 may have fewer items, just verify it doesn't contain page1 ids
        p1_ids = {j["id"] for j in page1}
        p2_ids = {j["id"] for j in page2}
        self.assertEqual(len(p1_ids & p2_ids), 0)

    def test_medium__thread_safe_insert(self):
        errors = []
        def insert_job_thread(i):
            try:
                insert_job(_make_job(f"job_thread_{i:04d}"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=insert_job_thread, args=(i,)) for i in range(10)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
