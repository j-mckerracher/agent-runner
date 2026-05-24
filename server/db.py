"""SQLite jobs store."""
from __future__ import annotations

import logging
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
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
  eval_runner_args TEXT,
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
  current_stage TEXT,
  original_ac_count INTEGER,
  normalized_ac_count INTEGER,
  story_source TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_submitted ON jobs(submitted_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_parent ON jobs(parent_job_id);
CREATE INDEX IF NOT EXISTS idx_jobs_change ON jobs(change_id);
CREATE TABLE IF NOT EXISTS telemetry_events (
  job_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  ts TEXT NOT NULL,
  type TEXT NOT NULL,
  stage TEXT,
  agent TEXT,
  runner TEXT,
  model TEXT,
  level TEXT,
  kind TEXT,
  status TEXT,
  exit_code INTEGER,
  duration_ms REAL,
  tokens_in INTEGER DEFAULT 0,
  tokens_out INTEGER DEFAULT 0,
  cost_usd REAL DEFAULT 0,
  message TEXT,
  payload_json TEXT NOT NULL,
  PRIMARY KEY (job_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_telemetry_events_job_seq ON telemetry_events(job_id, seq);
CREATE INDEX IF NOT EXISTS idx_telemetry_events_type ON telemetry_events(type);
CREATE INDEX IF NOT EXISTS idx_telemetry_events_ts ON telemetry_events(ts);
CREATE INDEX IF NOT EXISTS idx_telemetry_events_stage ON telemetry_events(stage);
CREATE INDEX IF NOT EXISTS idx_telemetry_events_agent ON telemetry_events(agent);
CREATE INDEX IF NOT EXISTS idx_telemetry_events_runner_model ON telemetry_events(runner, model);
CREATE INDEX IF NOT EXISTS idx_telemetry_events_level ON telemetry_events(level);
CREATE INDEX IF NOT EXISTS idx_telemetry_events_kind ON telemetry_events(kind);
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
    if "eval_runner_args" not in columns:
        logger.info("_ensure_schema: adding jobs.eval_runner_args column")
        conn.execute("ALTER TABLE jobs ADD COLUMN eval_runner_args TEXT")
    if "original_ac_count" not in columns:
        logger.info("_ensure_schema: adding jobs.original_ac_count column")
        conn.execute("ALTER TABLE jobs ADD COLUMN original_ac_count INTEGER")
    if "normalized_ac_count" not in columns:
        logger.info("_ensure_schema: adding jobs.normalized_ac_count column")
        conn.execute("ALTER TABLE jobs ADD COLUMN normalized_ac_count INTEGER")
    if "story_source" not in columns:
        logger.info("_ensure_schema: adding jobs.story_source column")
        conn.execute("ALTER TABLE jobs ADD COLUMN story_source TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_run_kind ON jobs(run_kind)")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS telemetry_events (
          job_id TEXT NOT NULL,
          seq INTEGER NOT NULL,
          ts TEXT NOT NULL,
          type TEXT NOT NULL,
          stage TEXT,
          agent TEXT,
          runner TEXT,
          model TEXT,
          level TEXT,
          kind TEXT,
          status TEXT,
          exit_code INTEGER,
          duration_ms REAL,
          tokens_in INTEGER DEFAULT 0,
          tokens_out INTEGER DEFAULT 0,
          cost_usd REAL DEFAULT 0,
          message TEXT,
          payload_json TEXT NOT NULL,
          PRIMARY KEY (job_id, seq)
        );
        CREATE INDEX IF NOT EXISTS idx_telemetry_events_job_seq ON telemetry_events(job_id, seq);
        CREATE INDEX IF NOT EXISTS idx_telemetry_events_type ON telemetry_events(type);
        CREATE INDEX IF NOT EXISTS idx_telemetry_events_ts ON telemetry_events(ts);
        CREATE INDEX IF NOT EXISTS idx_telemetry_events_stage ON telemetry_events(stage);
        CREATE INDEX IF NOT EXISTS idx_telemetry_events_agent ON telemetry_events(agent);
        CREATE INDEX IF NOT EXISTS idx_telemetry_events_runner_model ON telemetry_events(runner, model);
        CREATE INDEX IF NOT EXISTS idx_telemetry_events_level ON telemetry_events(level);
        CREATE INDEX IF NOT EXISTS idx_telemetry_events_kind ON telemetry_events(kind);
        """
    )


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


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _event_message(event: dict[str, Any]) -> str | None:
    for key in ("message", "msg", "error", "title"):
        value = event.get(key)
        if value is not None:
            text = str(value).strip()
            if text:
                return text
    return None


def _fallback_ts(job: dict[str, Any] | None, events_path: Path | None = None) -> str:
    submitted_at = (job or {}).get("submitted_at")
    if isinstance(submitted_at, str) and submitted_at.strip():
        return submitted_at.strip()
    if events_path is not None:
        try:
            return datetime.fromtimestamp(events_path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except OSError:
            pass
    return now_iso()


def _normalize_telemetry_event(
    job_id: str,
    event: dict[str, Any],
    *,
    fallback_seq: int | None = None,
    fallback_ts: str | None = None,
    seq_override: int | None = None,
) -> dict[str, Any]:
    raw_seq = seq_override if seq_override is not None else event.get("seq", fallback_seq)
    seq = _coerce_int(raw_seq)
    if seq <= 0:
        seq = int(fallback_seq or 0)
    if seq <= 0:
        raise ValueError("telemetry event requires a positive seq")
    ts = str(event.get("ts") or fallback_ts or now_iso())
    event_type = str(event.get("type") or "unknown")
    payload = dict(event)
    return {
        "job_id": job_id,
        "seq": seq,
        "ts": ts,
        "type": event_type,
        "stage": event.get("stage"),
        "agent": event.get("agent") or event.get("logger"),
        "runner": event.get("runner"),
        "model": event.get("model"),
        "level": event.get("level"),
        "kind": event.get("kind"),
        "status": event.get("status"),
        "exit_code": event.get("exit_code"),
        "duration_ms": event.get("duration_ms") or event.get("latency_ms"),
        "tokens_in": _coerce_int(event.get("tokens_in")),
        "tokens_out": _coerce_int(event.get("tokens_out")),
        "cost_usd": _coerce_float(event.get("cost_usd")),
        "message": _event_message(event),
        "payload_json": json.dumps(payload, ensure_ascii=False, sort_keys=True),
    }


_TELEMETRY_INSERTABLE = (
    "job_id", "seq", "ts", "type", "stage", "agent", "runner", "model", "level", "kind",
    "status", "exit_code", "duration_ms", "tokens_in", "tokens_out", "cost_usd", "message", "payload_json",
)


def insert_telemetry_event(job_id: str, event: dict[str, Any]) -> None:
    record = _normalize_telemetry_event(job_id, event)
    placeholders = ",".join("?" for _ in _TELEMETRY_INSERTABLE)
    sql = (
        f"INSERT OR IGNORE INTO telemetry_events ({','.join(_TELEMETRY_INSERTABLE)}) "
        f"VALUES ({placeholders})"
    )
    with cursor() as cur:
        cur.execute(sql, [record[c] for c in _TELEMETRY_INSERTABLE])


def list_telemetry_events(
    job_ids: list[str] | None = None,
    start_ts: str | None = None,
    end_ts: str | None = None,
) -> list[dict]:
    sql = "SELECT * FROM telemetry_events WHERE 1=1"
    params: list[Any] = []
    if job_ids is not None:
        if not job_ids:
            return []
        placeholders = ",".join("?" for _ in job_ids)
        sql += f" AND job_id IN ({placeholders})"
        params.extend(job_ids)
    if start_ts:
        sql += " AND ts>=?"
        params.append(start_ts)
    if end_ts:
        sql += " AND ts<=?"
        params.append(end_ts)
    sql += " ORDER BY job_id ASC, seq ASC"
    with cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    out: list[dict] = []
    for row in rows:
        item = dict(row)
        try:
            payload = json.loads(item.pop("payload_json") or "{}")
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("job_id", item["job_id"])
        payload["seq"] = item["seq"]
        payload["ts"] = payload.get("ts") or item["ts"]
        payload["type"] = payload.get("type") or item["type"]
        out.append(payload)
    return out


def count_telemetry_events(job_id: str) -> int:
    with cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM telemetry_events WHERE job_id=?", (job_id,))
        (count,) = cur.fetchone()
    return int(count)


def _stable_backfill_seq(raw_seq: Any, line_number: int, used: set[int]) -> int:
    seq = _coerce_int(raw_seq)
    if seq > 0 and seq not in used:
        used.add(seq)
        return seq
    candidate = max(1, line_number)
    while candidate in used:
        candidate += 1
    used.add(candidate)
    return candidate


def backfill_telemetry_events_for_job(job: dict) -> int:
    job_id = str(job.get("id") or "")
    events_path_value = job.get("events_path")
    if not job_id or not events_path_value:
        return 0
    events_path = Path(events_path_value)
    if not events_path.is_file():
        logger.info("backfill_telemetry_events_for_job: missing events file for %s: %s", job_id, events_path)
        return 0
    fallback_ts = _fallback_ts(job, events_path)
    inserted = 0
    used_seqs: set[int] = set()
    try:
        with events_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    logger.warning(
                        "backfill_telemetry_events_for_job: skipping malformed JSON for %s line %d: %s",
                        job_id, line_number, exc,
                    )
                    continue
                if not isinstance(event, dict):
                    logger.warning(
                        "backfill_telemetry_events_for_job: skipping non-object event for %s line %d",
                        job_id, line_number,
                    )
                    continue
                seq = _stable_backfill_seq(event.get("seq"), line_number, used_seqs)
                record = _normalize_telemetry_event(job_id, event, fallback_seq=seq, fallback_ts=fallback_ts, seq_override=seq)
                placeholders = ",".join("?" for _ in _TELEMETRY_INSERTABLE)
                sql = (
                    f"INSERT OR IGNORE INTO telemetry_events ({','.join(_TELEMETRY_INSERTABLE)}) "
                    f"VALUES ({placeholders})"
                )
                with cursor() as cur:
                    cur.execute(sql, [record[c] for c in _TELEMETRY_INSERTABLE])
                    inserted += int(cur.rowcount or 0)
    except OSError as exc:
        logger.warning("backfill_telemetry_events_for_job: failed reading %s for %s: %s", events_path, job_id, exc)
        return inserted
    return inserted


def backfill_telemetry_events_for_jobs(jobs: list[dict]) -> int:
    total = 0
    for job in jobs:
        try:
            total += backfill_telemetry_events_for_job(job)
        except Exception as exc:
            logger.warning("backfill_telemetry_events_for_jobs: skipped job %s: %s", job.get("id"), exc)
    return total


_INSERTABLE = (
    "id", "change_id", "parent_job_id", "status", "run_kind", "mode", "runner", "model", "log_level",
    "repo", "ado_url", "story_file", "extra_context", "eval_runner_args",
    "submitted_at", "events_path", "cassette_path", "original_ac_count", "normalized_ac_count", "story_source",
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
