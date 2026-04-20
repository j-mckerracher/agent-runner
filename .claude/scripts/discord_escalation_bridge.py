#!/usr/bin/env python3
"""Discord Escalation Bridge.

Posts escalation notices to Discord and writes resume.json when an
authorized user replies with a RESUME: message in the dedicated thread.

Usage:
    python discord_escalation_bridge.py \\
        --escalated-path /path/to/escalated.json \\
        --status-dir /path/to/status \\
        --change-id WI-12345 [--dry-run]

Environment variables:
    DISCORD_BOT_TOKEN        Required (unless --dry-run)
    DISCORD_GUILD_NAME       Server name (default: Agent-Escalations)
    DISCORD_CHANNEL_NAME     Channel name (default: general)
    DISCORD_ALLOWED_USER_IDS Comma-separated Discord user IDs that may resume
    DISCORD_POLL_SECONDS     Poll interval in seconds (default: 5)

Dry-run mode:
    Pass --dry-run to simulate without a real Discord connection.
    Create status/discord_simulated_message.txt containing e.g.:
        RESUME: Q1=my answer, Q2=another answer
    and the bridge will parse it and write resume.json.

Exit codes:
    0  resume.json was written — workflow should continue
    1  fatal configuration / network error — caller falls back to manual wait
    2  resume.json already existed when bridge started
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------
DEFAULT_GUILD_NAME = "arigato-mr-roboto"
DEFAULT_CHANNEL_NAME = "agent-escalations"
DEFAULT_POLL_SECONDS = 5
DISCORD_API_BASE = "https://discord.com/api/v10"
RESUME_PREFIX = "RESUME:"


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(msg: str) -> None:
    print(f"[bridge] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Discord REST helpers (stdlib urllib — zero extra deps)
# ---------------------------------------------------------------------------


class DiscordAPIError(RuntimeError):
    pass


def _discord_request(
    method: str,
    endpoint: str,
    token: str,
    payload: dict | None = None,
) -> object:
    url = f"{DISCORD_API_BASE}{endpoint}"
    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
        "User-Agent": "DiscordEscalationBridge/1.0",
    }
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise DiscordAPIError(
            f"Discord API {method} {endpoint} -> HTTP {exc.code}: {body}"
        ) from exc
    except urllib.error.URLError as exc:
        raise DiscordAPIError(
            f"Discord API {method} {endpoint} -> network error: {exc.reason}"
        ) from exc


def get_guild_id(token: str, guild_name: str) -> str:
    guilds = _discord_request("GET", "/users/@me/guilds", token)
    if not isinstance(guilds, list):
        raise DiscordAPIError("Unexpected response from /users/@me/guilds")
    for g in guilds:
        if g.get("name") == guild_name:
            return str(g["id"])
    names = [g.get("name") for g in guilds]
    raise DiscordAPIError(
        f"Guild {guild_name!r} not found. Bot is in: {names}"
    )


def get_channel_id(token: str, guild_id: str, channel_name: str) -> str:
    channels = _discord_request("GET", f"/guilds/{guild_id}/channels", token)
    if not isinstance(channels, list):
        raise DiscordAPIError("Unexpected response from /guilds/.../channels")
    # type 0 = GUILD_TEXT, type 5 = GUILD_ANNOUNCEMENT
    for c in channels:
        if c.get("name") == channel_name and c.get("type") in (0, 5):
            return str(c["id"])
    available = [c.get("name") for c in channels if c.get("type") in (0, 5)]
    raise DiscordAPIError(
        f"Channel #{channel_name!r} not found. Available text channels: {available}"
    )


def post_message(token: str, channel_id: str, content: str) -> dict:
    result = _discord_request(
        "POST", f"/channels/{channel_id}/messages", token, {"content": content}
    )
    assert isinstance(result, dict)
    return result


def create_thread_from_message(
    token: str, channel_id: str, message_id: str, name: str
) -> dict:
    result = _discord_request(
        "POST",
        f"/channels/{channel_id}/messages/{message_id}/threads",
        token,
        {"name": name[:100], "auto_archive_duration": 10080},
    )
    assert isinstance(result, dict)
    return result


def get_thread_messages(
    token: str, thread_id: str, after_message_id: str | None = None
) -> list[dict]:
    endpoint = f"/channels/{thread_id}/messages?limit=50"
    if after_message_id:
        endpoint += f"&after={after_message_id}"
    result = _discord_request("GET", endpoint, token)
    return result if isinstance(result, list) else []


def post_to_thread(token: str, thread_id: str, content: str) -> dict:
    result = _discord_request(
        "POST", f"/channels/{thread_id}/messages", token, {"content": content}
    )
    assert isinstance(result, dict)
    return result


def build_message_permalink(guild_id: str, channel_id: str, message_id: str) -> str:
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


# ---------------------------------------------------------------------------
# Escalation message builder
# ---------------------------------------------------------------------------


def build_escalation_message(
    escalation: dict, change_id: str, escalated_path: Path
) -> str:
    stage = escalation.get("stage_key", "unknown")
    reason = escalation.get("reason", "No reason specified")
    questions: list[str] = escalation.get("blocking_questions", [])

    lines = [
        "## Workflow Paused — Human Input Required",
        "",
        f"**Change:** `{change_id}`",
        f"**Stage:** `{stage}`",
        f"**Reason:** {reason}",
        "",
        "**Blocking Questions:**",
    ]
    for i, q in enumerate(questions, 1):
        lines.append(f"- Q{i}: {q}")

    lines += [
        "",
        f"**Escalation file:** `{escalated_path}`",
        "",
        "---",
        "**To resume**, reply in this thread with `RESUME:` and your answers.",
        "Example: `RESUME: Q1=my answer, Q2=another answer`",
        "Or: `RESUME: <free-form explanation>`",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# RESUME message parser
# ---------------------------------------------------------------------------


def is_resume_message(content: str) -> bool:
    return content.strip().upper().startswith(RESUME_PREFIX.upper())


def parse_resume_message(content: str, questions: list[str]) -> dict:
    """Parse a RESUME: message into structured answer dict.

    Supports:
      RESUME: Q1=answer1, Q2=answer2
      RESUME: free-form text
      RESUME:
      Q1=answer1
      Q2=answer2
    """
    body = content.strip()
    if body.upper().startswith(RESUME_PREFIX.upper()):
        body = body[len(RESUME_PREFIX):].strip()

    answers: dict[str, str] = {}
    if body:
        lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        for line in lines:
            parts = [p.strip() for p in line.split(",") if p.strip()]
            for part in parts:
                if "=" in part:
                    key, _, value = part.partition("=")
                    answers[key.strip()] = value.strip()
        # If no key=value pairs found, put whole body under Q1
        if not answers:
            if questions:
                answers["Q1"] = body
    return {"answers": answers, "raw": content}


def check_missing_answers(answers: dict, questions: list[str]) -> list[str]:
    missing = []
    for i, q in enumerate(questions, 1):
        if f"Q{i}" not in answers:
            missing.append(f"Q{i}: {q}")
    return missing


# ---------------------------------------------------------------------------
# Dry-run mode
# ---------------------------------------------------------------------------


def run_dry_run_loop(status_dir: Path, questions: list[str]) -> dict | None:
    """Watch for discord_simulated_message.txt and process it as a Discord reply."""
    sim_path = status_dir / "discord_simulated_message.txt"
    resume_path = status_dir / "resume.json"

    _log(f"DRY-RUN mode: watching for {sim_path}")
    _log("Create that file with content like: RESUME: Q1=your answer")

    while True:
        if resume_path.exists():
            _log("resume.json already exists — exiting")
            return None

        if sim_path.exists():
            content = sim_path.read_text(encoding="utf-8").strip()
            _log(f"Simulated message: {content!r}")
            sim_path.unlink()

            if not is_resume_message(content):
                _log("Message does not start with RESUME: — ignoring")
                continue

            parsed = parse_resume_message(content, questions)
            return {
                "responder": "dry-run-user",
                "timestamp": iso_now(),
                "answers": parsed["answers"],
                "constraints": [],
                "extra_context": content,
                "discord": {
                    "guild_id": "dry-run",
                    "channel_id": "dry-run",
                    "thread_id": "dry-run",
                    "message_id": "dry-run",
                    "user_id": "dry-run",
                    "permalink": None,
                },
            }

        time.sleep(2)


# ---------------------------------------------------------------------------
# Main bridge logic
# ---------------------------------------------------------------------------


def load_escalation(escalated_path: Path) -> dict:
    try:
        return json.loads(escalated_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _log(f"Warning: could not read escalated.json: {exc}")
        return {}


def write_resume(status_dir: Path, payload: dict) -> Path:
    resume_path = status_dir / "resume.json"
    resume_path.parent.mkdir(parents=True, exist_ok=True)
    resume_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _log(f"resume.json written: {resume_path}")
    return resume_path


def _find_repo_root(start: Path) -> Path | None:
    current = start
    for _ in range(15):
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def run_discord_bridge(
    escalated_path: Path,
    status_dir: Path,
    change_id: str,
    token: str,
    guild_name: str,
    channel_name: str,
    allowed_user_ids: set[str],
    poll_seconds: int,
) -> int:
    """Core bridge logic. Returns 0 on success, 1 on fatal error, 2 if already done."""
    resume_path = status_dir / "resume.json"
    if resume_path.exists():
        _log("resume.json already exists — nothing to do")
        return 2

    escalation = load_escalation(escalated_path)
    questions: list[str] = escalation.get("blocking_questions", [])

    notified_path = status_dir / "discord_notified.json"
    discord_ctx_path = status_dir / "discord_context.json"

    guild_id: str | None = None
    channel_id: str | None = None
    thread_id: str | None = None
    root_message_id: str | None = None
    last_seen_message_id: str | None = None

    # ── Try to reattach to existing Discord conversation ──────────────────────
    if notified_path.exists():
        try:
            ctx = json.loads(notified_path.read_text(encoding="utf-8"))
            guild_id = ctx.get("guild_id")
            channel_id = ctx.get("channel_id")
            thread_id = ctx.get("thread_id")
            root_message_id = ctx.get("message_id")
            last_seen_message_id = ctx.get("last_seen_message_id")
            _log(f"Reattaching to existing thread: {thread_id}")
        except (json.JSONDecodeError, OSError) as exc:
            _log(f"Warning: could not read discord_notified.json: {exc}")
            thread_id = None

    # ── First-time notification ───────────────────────────────────────────────
    if not thread_id:
        try:
            _log(f"Resolving guild '{guild_name}'...")
            guild_id = get_guild_id(token, guild_name)
            _log(f"Guild ID: {guild_id}")

            _log(f"Resolving channel '#{channel_name}'...")
            channel_id = get_channel_id(token, guild_id, channel_name)
            _log(f"Channel ID: {channel_id}")

            msg_content = build_escalation_message(escalation, change_id, escalated_path)
            _log("Posting escalation message to Discord...")
            msg = post_message(token, channel_id, msg_content)
            root_message_id = str(msg["id"])
            _log(f"Message posted: {root_message_id}")

            thread_name = f"escalation-{change_id}-{escalation.get('stage_key', 'unknown')}"
            thread = create_thread_from_message(
                token, channel_id, root_message_id, thread_name
            )
            thread_id = str(thread["id"])
            _log(f"Thread created: {thread_id}")

            permalink = build_message_permalink(guild_id, channel_id, root_message_id)
            ctx_data = {
                "guild_id": guild_id,
                "channel_id": channel_id,
                "thread_id": thread_id,
                "message_id": root_message_id,
                "permalink": permalink,
                "posted_at": iso_now(),
                "change_id": change_id,
                "stage_key": escalation.get("stage_key"),
                "last_seen_message_id": None,
            }
            notified_path.parent.mkdir(parents=True, exist_ok=True)
            notified_path.write_text(json.dumps(ctx_data, indent=2) + "\n", encoding="utf-8")
            discord_ctx_path.write_text(json.dumps(ctx_data, indent=2) + "\n", encoding="utf-8")
            _log(f"Discord context saved: {notified_path}")

        except DiscordAPIError as exc:
            _log(f"ERROR: {exc}")
            return 1

    # ── Poll loop ─────────────────────────────────────────────────────────────
    _log(f"Polling thread {thread_id} every {poll_seconds}s for RESUME: message...")
    if not allowed_user_ids:
        _log("WARNING: DISCORD_ALLOWED_USER_IDS not set — any user may resume!")

    while True:
        if resume_path.exists():
            _log("resume.json detected externally — exiting")
            return 2

        if not escalated_path.exists():
            _log("escalated.json was removed externally — exiting")
            return 0

        try:
            messages = get_thread_messages(
                token, thread_id, after_message_id=last_seen_message_id
            )
        except DiscordAPIError as exc:
            _log(f"Warning: failed to fetch messages: {exc}")
            time.sleep(poll_seconds)
            continue

        # Process oldest-first
        messages_sorted = sorted(messages, key=lambda m: str(m.get("id", "")))

        for msg in messages_sorted:
            msg_id = str(msg.get("id", ""))
            author = msg.get("author", {})
            user_id = str(author.get("id", ""))
            username = str(author.get("username", "unknown"))
            discriminator = str(author.get("discriminator", "0"))
            is_bot = bool(author.get("bot", False))
            content = str(msg.get("content", ""))

            if is_bot:
                last_seen_message_id = msg_id
                continue

            if allowed_user_ids and user_id not in allowed_user_ids:
                _log(f"Ignoring unauthorized user: {username}#{discriminator} ({user_id})")
                try:
                    post_to_thread(
                        token,
                        thread_id,
                        f"Sorry <@{user_id}>, you are not authorized to resume this workflow.",
                    )
                except DiscordAPIError:
                    pass
                last_seen_message_id = msg_id
                continue

            if not is_resume_message(content):
                try:
                    post_to_thread(
                        token,
                        thread_id,
                        (
                            f"<@{user_id}> To resume, please reply with `RESUME:` followed by your answers.\n"
                            "Example: `RESUME: Q1=my answer, Q2=another answer`"
                        ),
                    )
                except DiscordAPIError:
                    pass
                last_seen_message_id = msg_id
                continue

            # Parse the RESUME message
            parsed = parse_resume_message(content, questions)
            answers = parsed["answers"]

            if questions and not answers:
                missing_text = "\n".join(
                    f"- Q{i}: {q}" for i, q in enumerate(questions, 1)
                )
                try:
                    post_to_thread(
                        token,
                        thread_id,
                        (
                            f"<@{user_id}> Please answer these questions:\n{missing_text}\n\n"
                            "Reply with `RESUME: Q1=answer, Q2=answer`"
                        ),
                    )
                except DiscordAPIError:
                    pass
                last_seen_message_id = msg_id
                continue

            # ── Authorized RESUME — write resume.json ─────────────────────────
            responder = (
                f"{username}#{discriminator}" if discriminator not in ("0", "") else username
            )
            permalink = build_message_permalink(
                guild_id or "", thread_id, msg_id
            )

            resume_payload = {
                "responder": responder,
                "timestamp": iso_now(),
                "answers": answers,
                "constraints": [],
                "extra_context": content,
                "discord": {
                    "guild_id": guild_id,
                    "channel_id": channel_id,
                    "thread_id": thread_id,
                    "message_id": msg_id,
                    "user_id": user_id,
                    "permalink": permalink,
                },
            }

            write_resume(status_dir, resume_payload)
            last_seen_message_id = msg_id

            # Persist last_seen
            try:
                ctx = json.loads(notified_path.read_text(encoding="utf-8"))
                ctx["last_seen_message_id"] = last_seen_message_id
                notified_path.write_text(
                    json.dumps(ctx, indent=2) + "\n", encoding="utf-8"
                )
            except Exception:
                pass

            # Acknowledge in Discord
            try:
                post_to_thread(
                    token,
                    thread_id,
                    f"Resume acknowledged. Workflow will continue shortly.",
                )
            except DiscordAPIError:
                pass

            return 0

        # After processing all messages, persist last_seen
        if messages_sorted:
            last_seen_message_id = str(messages_sorted[-1].get("id", ""))
            try:
                ctx = json.loads(notified_path.read_text(encoding="utf-8"))
                ctx["last_seen_message_id"] = last_seen_message_id
                notified_path.write_text(
                    json.dumps(ctx, indent=2) + "\n", encoding="utf-8"
                )
            except Exception:
                pass

        time.sleep(poll_seconds)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Discord Escalation Bridge")
    parser.add_argument("--escalated-path", required=True, help="Path to escalated.json")
    parser.add_argument("--status-dir", required=True, help="Path to status directory")
    parser.add_argument("--change-id", required=True, help="Change ID (e.g. WI-12345)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate Discord: watch for discord_simulated_message.txt",
    )
    args = parser.parse_args(argv)

    escalated_path = Path(args.escalated_path)
    status_dir = Path(args.status_dir)
    change_id = args.change_id

    if not escalated_path.exists():
        _log(f"escalated.json not found: {escalated_path}")
        return 1

    escalation = load_escalation(escalated_path)
    questions: list[str] = escalation.get("blocking_questions", [])

    # ── Dry-run mode ──────────────────────────────────────────────────────────
    if args.dry_run:
        _log("Running in DRY-RUN mode")
        result = run_dry_run_loop(status_dir, questions)
        if result:
            write_resume(status_dir, result)
        return 0

    # ── Read configuration from environment ───────────────────────────────────
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token:
        _log(
            "DISCORD_BOT_TOKEN not set — Discord escalation disabled.\n"
            "[bridge] Falling back to manual resume.json creation."
        )
        return 1

    guild_name = os.environ.get("DISCORD_GUILD_NAME", DEFAULT_GUILD_NAME)
    channel_name = os.environ.get("DISCORD_CHANNEL_NAME", DEFAULT_CHANNEL_NAME)
    poll_seconds = int(os.environ.get("DISCORD_POLL_SECONDS", str(DEFAULT_POLL_SECONDS)))

    raw_allowed = os.environ.get("DISCORD_ALLOWED_USER_IDS", "")

    # Also check .claude/discord_config.json for allowlist
    repo_root = _find_repo_root(escalated_path)
    if repo_root and not raw_allowed:
        config_file = repo_root / ".claude" / "discord_config.json"
        if config_file.exists():
            try:
                cfg = json.loads(config_file.read_text(encoding="utf-8"))
                raw_allowed = ",".join(cfg.get("allowed_user_ids", []))
            except (json.JSONDecodeError, OSError):
                pass

    allowed_user_ids: set[str] = (
        {uid.strip() for uid in raw_allowed.split(",") if uid.strip()}
        if raw_allowed
        else set()
    )

    return run_discord_bridge(
        escalated_path=escalated_path,
        status_dir=status_dir,
        change_id=change_id,
        token=token,
        guild_name=guild_name,
        channel_name=channel_name,
        allowed_user_ids=allowed_user_ids,
        poll_seconds=poll_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())

