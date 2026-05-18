"""Shared escalation engine for human-in-the-loop workflow pauses.

Used by:
  - the runner subprocess (via request_user_input blocking call)
  - the OpenAI-compatible tool runtime
  - the CLI helper script  agent-script-source/request-user-input.py
  - the FastAPI /respond route (write_user_response)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Per-job escalation lock — serialises escalations to one active prompt at a
# time in v1.  Keyed by change_id so parallel UoWs within the same job wait.
_JOB_ESCALATION_LOCKS: dict[str, threading.Lock] = {}
_META_LOCK = threading.Lock()

_POLL_INTERVAL_SECONDS = 1.0


def _job_lock(change_id: str) -> threading.Lock:
    with _META_LOCK:
        if change_id not in _JOB_ESCALATION_LOCKS:
            _JOB_ESCALATION_LOCKS[change_id] = threading.Lock()
        return _JOB_ESCALATION_LOCKS[change_id]


def _generate_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _normalize_questions(questions: list) -> list[dict]:
    """Ensure every question is a dict with at least ``id`` and ``label``."""
    out: list[dict] = []
    for idx, q in enumerate(questions):
        if isinstance(q, str):
            out.append({"id": f"q{idx + 1}", "label": q, "kind": "textarea", "required": True})
        elif isinstance(q, dict):
            q_copy = dict(q)
            q_copy.setdefault("id", f"q{idx + 1}")
            q_copy.setdefault("kind", "textarea")
            q_copy.setdefault("required", True)
            out.append(q_copy)
        else:
            out.append({"id": f"q{idx + 1}", "label": str(q), "kind": "textarea", "required": True})
    return out


def _append_transcript(change_id: str, conversation_id: str, record: dict) -> None:
    """Append a record to the conversation transcript JSONL."""
    from server.paths import escalation_transcript_path_for
    tp = escalation_transcript_path_for(change_id, conversation_id)
    tp.parent.mkdir(parents=True, exist_ok=True)
    with tp.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        fh.flush()


def _emit_event(type: str, **fields: Any) -> None:
    if not os.environ.get("AGENT_RUNNER_EVENT_LOG"):
        return
    try:
        from server.events import emit
        emit(type, **fields)
    except Exception:
        pass


def _count_pending(change_id: str) -> int:
    """Count request files that have no matching response file."""
    from server.paths import escalations_dir_for
    esc_root = escalations_dir_for(change_id)
    if not esc_root.exists():
        return 0
    count = 0
    for conv_dir in esc_root.iterdir():
        if not conv_dir.is_dir():
            continue
        turns_dir = conv_dir / "turns"
        if not turns_dir.is_dir():
            continue
        for req_file in turns_dir.glob("*.request.json"):
            resp_file = req_file.with_name(req_file.name.replace(".request.json", ".response.json"))
            if not resp_file.exists():
                count += 1
    return count


# ─────────────────────────────────────────────────────────────────────────────
# request_user_input — blocks until the user responds via the GUI or TTY
# ─────────────────────────────────────────────────────────────────────────────


def request_user_input(
    *,
    change_id: str,
    stage: str,
    agent: str,
    title: str,
    message: str,
    questions: list[dict] | list[str],
    severity: str = "blocking",
    conversation_id: str | None = None,
    uow_id: str | None = None,
    resolution_criteria: str | None = None,
    timeout_seconds: int | None = None,
) -> dict:
    """Ask the user a blocking question.  Blocks until a response file appears.

    Returns the response JSON dict.
    """
    if not change_id:
        raise ValueError("change_id is required for user escalation")
    if not questions:
        raise ValueError("at least one question is required")

    lock = _job_lock(change_id)
    lock.acquire()
    try:
        return _do_request(
            change_id=change_id,
            stage=stage,
            agent=agent,
            title=title,
            message=message,
            questions=questions,
            severity=severity,
            conversation_id=conversation_id,
            uow_id=uow_id,
            resolution_criteria=resolution_criteria,
            timeout_seconds=timeout_seconds,
        )
    finally:
        lock.release()


def _do_request(
    *,
    change_id: str,
    stage: str,
    agent: str,
    title: str,
    message: str,
    questions: list[dict] | list[str],
    severity: str,
    conversation_id: str | None,
    uow_id: str | None,
    resolution_criteria: str | None,
    timeout_seconds: int | None,
) -> dict:
    from server.paths import escalation_request_path_for

    if conversation_id is None:
        conversation_id = _generate_id("conv")
    escalation_id = _generate_id("esc")
    normalized_questions = _normalize_questions(questions)

    # Determine turn number from existing request files in this conversation.
    from server.paths import conversation_dir_for
    conv_dir = conversation_dir_for(change_id, conversation_id)
    turns_dir = conv_dir / "turns"
    existing_requests = list(turns_dir.glob("*.request.json")) if turns_dir.exists() else []
    turn = len(existing_requests) + 1

    job_id = os.environ.get("AGENT_RUNNER_JOB_ID", "")

    request_record: dict[str, Any] = {
        "type": "user.prompt",
        "change_id": change_id,
        "job_id": job_id,
        "conversation_id": conversation_id,
        "escalation_id": escalation_id,
        "turn": turn,
        "stage": stage,
        "agent": agent,
        "uow_id": uow_id,
        "severity": severity,
        "title": title,
        "message": message,
        "questions": normalized_questions,
        "resolution_criteria": resolution_criteria,
        "created_at": _now_iso(),
    }

    # Write request file.
    req_path = escalation_request_path_for(change_id, conversation_id, escalation_id)
    req_path.parent.mkdir(parents=True, exist_ok=True)
    req_path.write_text(json.dumps(request_record, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(
        "request_user_input: wrote request change_id=%s conv=%s esc=%s turn=%d",
        change_id, conversation_id, escalation_id, turn,
    )

    # Append to transcript.
    _append_transcript(change_id, conversation_id, request_record)

    # Emit event.
    _emit_event("user.prompt", **{k: v for k, v in request_record.items() if k != "type"})

    # Determine response mode.
    escalation_mode = os.environ.get("AGENT_RUNNER_USER_ESCALATION", "")
    event_log = os.environ.get("AGENT_RUNNER_EVENT_LOG", "")

    if escalation_mode == "gui" or event_log:
        # GUI mode — poll for response file.
        return _poll_for_response(
            change_id=change_id,
            conversation_id=conversation_id,
            escalation_id=escalation_id,
            request_record=request_record,
            timeout_seconds=timeout_seconds,
        )
    elif sys.stdin.isatty():
        # TTY fallback — ask interactively.
        return _tty_prompt(
            change_id=change_id,
            conversation_id=conversation_id,
            escalation_id=escalation_id,
            request_record=request_record,
        )
    else:
        raise RuntimeError(
            "User escalation required but no GUI/event log or TTY is available. "
            f"Title: {title}\nMessage: {message}"
        )


def _poll_for_response(
    *,
    change_id: str,
    conversation_id: str,
    escalation_id: str,
    request_record: dict,
    timeout_seconds: int | None,
) -> dict:
    from server.paths import escalation_response_path_for
    resp_path = escalation_response_path_for(change_id, conversation_id, escalation_id)
    logger.info("_poll_for_response: waiting for %s", resp_path)

    deadline = (time.monotonic() + timeout_seconds) if timeout_seconds else None
    while True:
        if resp_path.exists():
            try:
                response = json.loads(resp_path.read_text(encoding="utf-8"))
                logger.info(
                    "_poll_for_response: got response for conv=%s esc=%s",
                    conversation_id, escalation_id,
                )
                return response
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("_poll_for_response: error reading response file: %s", exc)
        if deadline is not None and time.monotonic() >= deadline:
            _emit_event(
                "user.prompt.timeout",
                conversation_id=conversation_id,
                escalation_id=escalation_id,
                change_id=change_id,
            )
            raise TimeoutError(
                f"User escalation timed out after {timeout_seconds}s "
                f"(conv={conversation_id}, esc={escalation_id})"
            )
        time.sleep(_POLL_INTERVAL_SECONDS)


def _tty_prompt(
    *,
    change_id: str,
    conversation_id: str,
    escalation_id: str,
    request_record: dict,
) -> dict:
    """Fallback: prompt the user interactively in the terminal."""
    print(f"\n{'=' * 60}")
    print(f"🛑 ESCALATION: {request_record['title']}")
    print(f"{'=' * 60}")
    print(f"Stage: {request_record['stage']} | Agent: {request_record['agent']}")
    if request_record.get("uow_id"):
        print(f"UoW: {request_record['uow_id']}")
    print(f"\n{request_record['message']}\n")

    responses: dict[str, str] = {}
    for q in request_record["questions"]:
        label = q.get("label", q.get("id", "?"))
        answer = input(f"  → {label}\n    > ")
        responses[q["id"]] = answer

    message = responses.get(request_record["questions"][0]["id"], "") if request_record["questions"] else ""

    response_data = write_user_response(
        change_id=change_id,
        job_id=request_record.get("job_id", ""),
        conversation_id=conversation_id,
        escalation_id=escalation_id,
        message=message,
        responses=responses,
    )
    return response_data


# ─────────────────────────────────────────────────────────────────────────────
# write_user_response — called by the server /respond route
# ─────────────────────────────────────────────────────────────────────────────


def write_user_response(
    *,
    change_id: str,
    job_id: str,
    conversation_id: str,
    escalation_id: str,
    message: str | None,
    responses: dict[str, str],
    allow_overwrite: bool = False,
) -> dict:
    """Write the user's response atomically and return response metadata.

    Raises ValueError if the matching request does not exist or the response
    has already been written (unless *allow_overwrite* is True).
    """
    from server.paths import (
        escalation_request_path_for,
        escalation_response_path_for,
    )

    req_path = escalation_request_path_for(change_id, conversation_id, escalation_id)
    if not req_path.exists():
        raise ValueError(
            f"No matching escalation request: conv={conversation_id} esc={escalation_id}"
        )

    resp_path = escalation_response_path_for(change_id, conversation_id, escalation_id)
    if resp_path.exists() and not allow_overwrite:
        raise ValueError(
            f"Response already exists for conv={conversation_id} esc={escalation_id}"
        )

    pending_after = max(0, _count_pending(change_id) - 1)

    response_record: dict[str, Any] = {
        "type": "user.response",
        "change_id": change_id,
        "job_id": job_id,
        "conversation_id": conversation_id,
        "escalation_id": escalation_id,
        "message": message,
        "responses": responses,
        "pending_count_after": pending_after,
        "responded_at": _now_iso(),
    }

    # Atomic write: write to temp, then rename.
    resp_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(resp_path.parent),
        prefix=".resp_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(response_record, fh, indent=2, ensure_ascii=False)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        Path(tmp_path).replace(resp_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    logger.info(
        "write_user_response: wrote response conv=%s esc=%s pending_after=%d",
        conversation_id, escalation_id, pending_after,
    )

    # Append to transcript.
    _append_transcript(change_id, conversation_id, response_record)

    return response_record


# ─────────────────────────────────────────────────────────────────────────────
# list_pending_escalations
# ─────────────────────────────────────────────────────────────────────────────


def list_pending_escalations(change_id: str) -> list[dict]:
    """Return request records that have no matching response file."""
    from server.paths import escalations_dir_for
    esc_root = escalations_dir_for(change_id)
    if not esc_root.exists():
        return []

    pending: list[dict] = []
    for conv_dir in sorted(esc_root.iterdir()):
        if not conv_dir.is_dir():
            continue
        turns_dir = conv_dir / "turns"
        if not turns_dir.is_dir():
            continue
        for req_file in sorted(turns_dir.glob("*.request.json")):
            resp_file = req_file.with_name(req_file.name.replace(".request.json", ".response.json"))
            if not resp_file.exists():
                try:
                    pending.append(json.loads(req_file.read_text(encoding="utf-8")))
                except (json.JSONDecodeError, OSError) as exc:
                    logger.warning("list_pending_escalations: error reading %s: %s", req_file, exc)
    return pending



