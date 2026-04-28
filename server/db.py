"""SQLite schema + CRUD helpers for agent-runner job store."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from server.config import _data_dir

_LOCAL = threading.local()

_DDL = """
CREATE TABLE IF NOT EXISTS jobs (
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
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_submitted ON jobs(submitted_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_parent ON jobs(parent_job_id);
"""


def db_path() -> Path:
    return _data_dir() / "jobs.db"


def get_conn() -> sqlite3.Connection:
    """Return a per-thread SQLite connection, creating the schema on first use."""
    if not hasattr(_LOCAL, "conn") or _LOCAL.conn is None:
        path = db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_DDL)
        conn.commit()
        _LOCAL.conn = conn
    return _LOCAL.conn


def close_conn() -> None:
    if hasattr(_LOCAL, "conn") and _LOCAL.conn is not None:
        _LOCAL.conn.close()
        _LOCAL.conn = None


# ── CRUD helpers ──────────────────────────────────────────────────────────────

def insert_job(job: dict[str, Any]) -> None:
    conn = get_conn()
    cols = ", ".join(job.keys())
    placeholders = ", ".join("?" for _ in job)
    conn.execute(f"INSERT INTO jobs ({cols}) VALUES ({placeholders})", list(job.values()))
    conn.commit()


def get_job(job_id: str) -> dict[str, Any] | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def list_jobs(
    status: str | None = None,
    change_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    conn = get_conn()
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if change_id:
        clauses.append("change_id = ?")
        params.append(change_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.extend([limit, offset])
    rows = conn.execute(
        f"SELECT * FROM jobs {where} ORDER BY submitted_at DESC LIMIT ? OFFSET ?",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def update_job(job_id: str, updates: dict[str, Any]) -> None:
    if not updates:
        return
    conn = get_conn()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE jobs SET {set_clause} WHERE id = ?",
        list(updates.values()) + [job_id],
    )
    conn.commit()


def count_running_jobs() -> int:
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'running'").fetchone()
    return row[0] if row else 0
