#!/usr/bin/env python3
"""Knowledge broker: orchestrates Reference Librarian and Information Explorer.

This script is invoked by the /rls-query skill. It runs Librarian and
(optionally) Explorer as separate top-level `claude -p` processes to avoid
the nested-subagent constraint.

Usage:
    rls_query.py <question> [--change-id ID] [--repo-root DIR]
                             [--artifact-root DIR] [--timeout SECS]

Outputs compact final-answer JSON to stdout. All detailed evidence and
exploration content is stored on disk, never printed to the caller's context.

Exit codes: 0 = success, 1 = error.
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_TIMEOUT = 300  # seconds per CLI call
MAX_JSON_RETRIES = 2
ARTIFACT_ROOT_DEFAULT = "agent-context"
SCHEMAS_DIR = "agent-context/schemas"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def timestamp_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def timestamp_file() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def find_repo_root() -> Path | None:
    """Walk up from cwd to find .git directory."""
    current = Path.cwd()
    for parent in [current, *current.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def find_change_id(artifact_root: Path) -> str | None:
    """Detect active CHANGE-ID from env var or most-recent intake directory."""
    cid = os.environ.get("CHANGE_ID")
    if cid:
        return cid
    if artifact_root.is_dir():
        for child in sorted(
            artifact_root.iterdir(),
            key=lambda p: p.stat().st_mtime if p.is_dir() else 0,
            reverse=True,
        ):
            if child.is_dir() and (child / "intake" / "config.yaml").exists():
                return child.name
    return None


def log_entry(log_dir: Path, label: str, data: dict) -> Path:
    """Write a timestamped JSON log entry and return its path."""
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = timestamp_file()
    path = log_dir / f"{ts}_{label}.json"
    path.write_text(json.dumps({"timestamp": timestamp_iso(), **data}, indent=2))
    return path


def invoke_claude(
    *,
    agent: str,
    schema_path: Path,
    prompt: str,
    repo_root: Path,
    timeout: int,
    add_dirs: list[Path] | None = None,
) -> dict:
    """Invoke claude -p with --agent and --json-schema, return parsed JSON.

    Retries up to MAX_JSON_RETRIES times if the output is not valid JSON.
    """
    cmd = [
        "claude",
        "-p",
        "--agent", agent,
        "--output-format", "json",
    ]

    # Add schema file reference
    if schema_path.exists():
        cmd += ["--json-schema", f"@{schema_path}"]

    # Add directories for context
    if add_dirs:
        for d in add_dirs:
            if d.exists():
                cmd += ["--add-dir", str(d)]

    cmd.append(prompt)

    last_output = ""
    for attempt in range(1 + MAX_JSON_RETRIES):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(repo_root),
            )
            raw = result.stdout.strip()
            last_output = raw

            # Try to extract JSON from the output
            parsed = _extract_json(raw)
            if parsed is not None:
                return parsed

            # If first attempt failed, retry with a fix prompt
            if attempt < MAX_JSON_RETRIES:
                fix_prompt = (
                    f"Your previous response was not valid JSON. "
                    f"Please respond ONLY with valid JSON matching the schema. "
                    f"Original question: {prompt}"
                )
                cmd_copy = list(cmd)
                cmd_copy[-1] = fix_prompt
                cmd = cmd_copy

        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"claude -p --agent {agent} timed out after {timeout}s"
            )
        except FileNotFoundError:
            raise RuntimeError(
                "claude CLI not found on PATH. Ensure Claude Code is installed."
            )

    raise RuntimeError(
        f"Failed to get valid JSON from {agent} after {1 + MAX_JSON_RETRIES} attempts. "
        f"Last output (truncated): {last_output[:500]}"
    )


def _extract_json(text: str) -> dict | None:
    """Try to extract a JSON object from text, handling markdown fences."""
    # Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON inside markdown code fences
    import re
    fence_pattern = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL)
    for match in fence_pattern.finditer(text):
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            continue

    # Try to find first { ... } block
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i, ch in enumerate(text[start:], start=start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break

    return None


def apply_knowledge_updates(
    updates: list[dict], knowledge_dir: Path, log_dir: Path
) -> None:
    """Apply knowledge_updates from librarian response to disk."""
    if not updates:
        return

    for update in updates:
        target = update.get("target_file", "")
        action = update.get("action", "append")
        content = update.get("content", "")
        section_key = update.get("section_key")

        if not target or not content:
            continue

        target_path = knowledge_dir / target
        target_path.parent.mkdir(parents=True, exist_ok=True)

        if action == "append":
            with open(target_path, "a") as f:
                f.write(f"\n{content}\n")
        elif action == "upsert" and section_key:
            _upsert_section(target_path, section_key, content)
        elif action == "replace_section" and section_key:
            _upsert_section(target_path, section_key, content)
        else:
            with open(target_path, "a") as f:
                f.write(f"\n{content}\n")

    log_entry(log_dir, "knowledge_updates_applied", {"count": len(updates)})


def _upsert_section(path: Path, key: str, content: str) -> None:
    """Upsert a section identified by key in a markdown file."""
    marker_start = f"<!-- section:{key} -->"
    marker_end = f"<!-- /section:{key} -->"

    if path.exists():
        text = path.read_text()
    else:
        text = ""

    new_block = f"{marker_start}\n{content}\n{marker_end}"

    if marker_start in text and marker_end in text:
        import re
        pattern = re.escape(marker_start) + r".*?" + re.escape(marker_end)
        text = re.sub(pattern, new_block, text, flags=re.DOTALL)
    else:
        text += f"\n{new_block}\n"

    path.write_text(text)


# ---------------------------------------------------------------------------
# Main broker flow
# ---------------------------------------------------------------------------


def run_broker(
    question: str,
    repo_root: Path,
    artifact_root: Path,
    change_id: str | None,
    timeout: int,
) -> dict:
    """Execute the three-step broker flow: Librarian → Explorer → Synthesis."""

    schemas_dir = repo_root / SCHEMAS_DIR
    librarian_schema = schemas_dir / "librarian_response.schema.json"
    explorer_schema = schemas_dir / "explorer_response.schema.json"
    knowledge_dir = artifact_root / "knowledge"
    claude_dir = repo_root / ".claude"

    # Resolve log directories
    if change_id:
        lib_log_dir = artifact_root / change_id / "logs" / "reference_librarian"
        exp_log_dir = artifact_root / change_id / "logs" / "information_explorer"
        exploration_dir = artifact_root / change_id / "knowledge" / "exploration"
    else:
        lib_log_dir = artifact_root / "logs" / "reference_librarian"
        exp_log_dir = artifact_root / "logs" / "information_explorer"
        exploration_dir = artifact_root / "knowledge" / "exploration"

    add_dirs = [repo_root, claude_dir, knowledge_dir, schemas_dir]

    # ── Step 1: Librarian ────────────────────────────────────────────────
    librarian_prompt = textwrap.dedent(f"""\
        You are the Reference Librarian answering a knowledge query.
        Respond ONLY with a JSON object matching the librarian_response schema.
        Keep your answer SHORT and factual. No code dumps.

        Question: {question}

        If you are confident in the answer, set requires_exploration to false.
        If you need the Information Explorer to search the codebase, set
        requires_exploration to true and fill exploration_request with
        search_hints, files_to_check, and expected_report_format.
    """)

    librarian_resp = invoke_claude(
        agent="reference-librarian",
        schema_path=librarian_schema,
        prompt=librarian_prompt,
        repo_root=repo_root,
        timeout=timeout,
        add_dirs=add_dirs,
    )

    log_entry(lib_log_dir, "step1_query", {
        "question": question,
        "response": librarian_resp,
    })

    # If librarian is fully confident, return immediately
    if not librarian_resp.get("requires_exploration", False):
        final = {
            "answer": librarian_resp.get("answer"),
            "confidence": librarian_resp.get("confidence", "none"),
            "sources": librarian_resp.get("sources", []),
            "recommended_next_step": "Use the answer directly.",
        }
        # Apply knowledge updates if any
        apply_knowledge_updates(
            librarian_resp.get("knowledge_updates", []),
            knowledge_dir,
            lib_log_dir,
        )
        log_entry(lib_log_dir, "final_answer", {"answer": final})
        return final

    # ── Step 2: Explorer ─────────────────────────────────────────────────
    exploration_req = librarian_resp.get("exploration_request", {}) or {}
    search_hints = exploration_req.get("search_hints", [])
    files_to_check = exploration_req.get("files_to_check", [])
    expected_format = exploration_req.get("expected_report_format", "JSON evidence list")

    explorer_prompt = textwrap.dedent(f"""\
        You are the Information Explorer performing a targeted codebase search.
        Respond ONLY with a JSON object matching the explorer_response schema.

        Original question: {question}

        Librarian's partial answer: {librarian_resp.get('answer', 'None')}

        Search hints: {json.dumps(search_hints)}
        Files to check: {json.dumps(files_to_check)}
        Expected report format: {expected_format}

        Keep excerpts SHORT (max 5 lines each). Focus on the most relevant evidence.
    """)

    explorer_resp = invoke_claude(
        agent="information-explorer",
        schema_path=explorer_schema,
        prompt=explorer_prompt,
        repo_root=repo_root,
        timeout=timeout,
        add_dirs=add_dirs,
    )

    log_entry(exp_log_dir, "step2_exploration", {
        "question": question,
        "exploration_request": exploration_req,
        "response": explorer_resp,
    })

    # Store detailed evidence on disk (not in caller's context)
    exploration_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = exploration_dir / f"{timestamp_file()}_evidence.json"
    evidence_path.write_text(json.dumps(explorer_resp, indent=2))

    # ── Step 3: Librarian synthesis ──────────────────────────────────────
    synthesis_prompt = textwrap.dedent(f"""\
        You are the Reference Librarian synthesizing a final answer.
        Respond ONLY with a JSON object matching the librarian_response schema.

        Original question: {question}

        Your earlier partial answer: {librarian_resp.get('answer', 'None')}

        Explorer findings summary: {explorer_resp.get('findings_summary', 'None')}
        Explorer recommended answer: {explorer_resp.get('recommended_answer', 'None')}
        Explorer confidence: {explorer_resp.get('confidence', 0)}
        Explorer evidence count: {len(explorer_resp.get('evidence', []))}

        Produce a final, concise answer combining your knowledge with the
        explorer's findings. Set requires_exploration to false.
        Include knowledge_updates if you have new information to persist.
    """)

    synthesis_resp = invoke_claude(
        agent="reference-librarian",
        schema_path=librarian_schema,
        prompt=synthesis_prompt,
        repo_root=repo_root,
        timeout=timeout,
        add_dirs=add_dirs,
    )

    log_entry(lib_log_dir, "step3_synthesis", {
        "question": question,
        "response": synthesis_resp,
    })

    # Apply knowledge updates
    apply_knowledge_updates(
        synthesis_resp.get("knowledge_updates", []),
        knowledge_dir,
        lib_log_dir,
    )

    final = {
        "answer": synthesis_resp.get("answer"),
        "confidence": synthesis_resp.get("confidence", "none"),
        "sources": synthesis_resp.get("sources", []),
        "recommended_next_step": (
            "Proceed with implementation."
            if synthesis_resp.get("confidence") == "full"
            else "Consider manual verification of the answer."
        ),
    }

    log_entry(lib_log_dir, "final_answer", {"answer": final})
    return final


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Knowledge broker: query the Reference Librarian and Information Explorer."
    )
    parser.add_argument("question", help="The knowledge query to answer.")
    parser.add_argument(
        "--change-id",
        default=None,
        help="Active CHANGE-ID (auto-detected from intake if omitted).",
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        help="Repository root directory (auto-detected via git if omitted).",
    )
    parser.add_argument(
        "--artifact-root",
        default=None,
        help="Artifact root directory (defaults to <repo-root>/agent-context).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"Timeout in seconds per CLI call (default: {DEFAULT_TIMEOUT}).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    # Resolve repo root
    if args.repo_root:
        repo_root = Path(args.repo_root).resolve()
    else:
        repo_root = find_repo_root()
        if repo_root is None:
            print(
                json.dumps({"error": "Could not detect repo root. Pass --repo-root."}),
                file=sys.stderr,
            )
            return 1

    # Resolve artifact root
    if args.artifact_root:
        artifact_root = Path(args.artifact_root).resolve()
    else:
        artifact_root = repo_root / ARTIFACT_ROOT_DEFAULT

    if not artifact_root.is_dir():
        print(
            json.dumps({
                "error": f"Artifact root does not exist: {artifact_root}. "
                         "Run init-artifact-dirs.py first."
            }),
            file=sys.stderr,
        )
        return 1

    # Resolve change-id
    change_id = args.change_id or find_change_id(artifact_root)

    try:
        result = run_broker(
            question=args.question,
            repo_root=repo_root,
            artifact_root=artifact_root,
            change_id=change_id,
            timeout=args.timeout,
        )
        # Print ONLY the compact final answer to stdout
        print(json.dumps(result, indent=2))
        return 0

    except RuntimeError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    except Exception as exc:
        print(
            json.dumps({"error": f"Unexpected error: {exc}"}),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
