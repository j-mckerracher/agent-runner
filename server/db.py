"""SQLite jobs store."""
from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .paths import db_path

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  change_id TEXT NOT NULL,
  parent_job_id TEXT,
  status TEXT NOT NULL,
  run_kind TEXT NOT NULL DEFAULT 'regular',
  mode TEXT NOT NULL,
  runner TEXT NOT NULL,
  model TEXT,
  log_level TEXT NOT NULL DEFAULT 'warning',
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
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_submitted ON jobs(submitted_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_parent ON jobs(parent_job_id);
CREATE INDEX IF NOT EXISTS idx_jobs_change ON jobs(change_id);
"""

_lock = threading.RLock()
_conns: dict[str, sqlite3.Connection] = {}


def _ensure_schema(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "run_kind" not in columns:
        logger.info("_ensure_schema: adding jobs.run_kind column")
        conn.execute("ALTER TABLE jobs ADD COLUMN run_kind TEXT NOT NULL DEFAULT 'regular'")
    if "log_level" not in columns:
        logger.info("_ensure_schema: adding jobs.log_level column")
        conn.execute("ALTER TABLE jobs ADD COLUMN log_level TEXT NOT NULL DEFAULT 'warning'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_run_kind ON jobs(run_kind)")


def _get_conn() -> sqlite3.Connection:
    key = str(db_path())
    with _lock:
        conn = _conns.get(key)
        if conn is None:
            logger.debug("_get_conn: opening new SQLite connection at %s", key)
            conn = sqlite3.connect(key, check_same_thread=False, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(_SCHEMA)
            _ensure_schema(conn)
            _conns[key] = conn
            logger.debug("_get_conn: schema applied; connection cached")
        return conn


def reset_for_tests() -> None:
    """Close cached connections so a new path takes effect."""
    logger.debug("reset_for_tests: closing %d cached connection(s)", len(_conns))
    with _lock:
        for c in _conns.values():
            try:
                c.close()
            except Exception:
                pass
        _conns.clear()
    logger.debug("reset_for_tests: connection cache cleared")


@contextmanager
def cursor() -> Iterator[sqlite3.Cursor]:
    conn = _get_conn()
    with _lock:
        cur = conn.cursor()
        try:
            yield cur
        finally:
            cur.close()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_INSERTABLE = (
    "id", "change_id", "parent_job_id", "status", "run_kind", "mode", "runner", "model", "log_level",
    "repo", "ado_url", "story_file", "extra_context",
    "submitted_at", "events_path", "cassette_path",
)


def insert_job(record: dict[str, Any]) -> None:
    cols = [c for c in _INSERTABLE if c in record]
    placeholders = ",".join("?" for _ in cols)
    sql = f"INSERT INTO jobs ({','.join(cols)}) VALUES ({placeholders})"
    logger.debug("insert_job: id=%s change_id=%s runner=%s", record.get("id"), record.get("change_id"), record.get("runner"))
    with cursor() as cur:
        cur.execute(sql, [record[c] for c in cols])
    logger.info("insert_job: job %s inserted (change_id=%s)", record.get("id"), record.get("change_id"))


def update_job(job_id: str, **fields: Any) -> None:
    if not fields:
        logger.debug("update_job: no fields to update for %s", job_id)
        return
    logger.debug("update_job: %s  fields=%s", job_id, list(fields.keys()))
    sets = ",".join(f"{k}=?" for k in fields)
    with cursor() as cur:
        cur.execute(f"UPDATE jobs SET {sets} WHERE id=?", [*fields.values(), job_id])
    logger.debug("update_job: %s updated", job_id)


def get_job(job_id: str) -> dict | None:
    logger.debug("get_job: %s", job_id)
    with cursor() as cur:
        cur.execute("SELECT * FROM jobs WHERE id=?", (job_id,))
        row = cur.fetchone()
    result = dict(row) if row else None
    if result is None:
        logger.debug("get_job: %s not found", job_id)
    return result


def list_jobs(
    status: str | None = None,
    change_id: str | None = None,
    run_kind: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    logger.debug(
        "list_jobs: status=%s change_id=%s run_kind=%s limit=%d offset=%d",
        status, change_id, run_kind, limit, offset,
    )
    sql = "SELECT * FROM jobs WHERE 1=1"
    params: list[Any] = []
    if status:
        sql += " AND status=?"
        params.append(status)
    if change_id:
        sql += " AND change_id=?"
        params.append(change_id)
    if run_kind:
        sql += " AND run_kind=?"
        params.append(run_kind)
    sql += " ORDER BY submitted_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    with cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    logger.debug("list_jobs: returned %d row(s)", len(rows))
    return [dict(r) for r in rows]


def list_children(parent_job_id: str) -> list[dict]:
    logger.debug("list_children: parent_job_id=%s", parent_job_id)
    with cursor() as cur:
        cur.execute("SELECT * FROM jobs WHERE parent_job_id=? ORDER BY submitted_at ASC", (parent_job_id,))
        rows = cur.fetchall()
    logger.debug("list_children: %d child job(s) for parent %s", len(rows), parent_job_id)
    return [dict(r) for r in rows]


def count_running() -> int:
    with cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM jobs WHERE status='running'")
        (n,) = cur.fetchone()
    logger.debug("count_running: %d", n)
    return int(n)
