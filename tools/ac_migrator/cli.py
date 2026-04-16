"""AC Migrator CLI.

Parses an ADO work item JSON payload, extracts acceptance criteria from
the HTML description, and writes a draft task.yaml + criteria files.

Usage:
    python -m tools.ac_migrator.cli --from-ado payload.json --out task-corpus/my-task
    python -m tools.ac_migrator.cli --from-ado payload.json --out task-corpus/my-task --yes
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import textwrap
from pathlib import Path
from typing import Any

import yaml


_AC_HEADING_RE = re.compile(
    r"(?i)(?:acceptance\s+criteria|\bac\b)\s*:?\s*(?:<[^>]+>)*",
)

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_LI_RE = re.compile(r"<li[^>]*>(.*?)</li>", re.DOTALL | re.IGNORECASE)
_HTML_ENTITY_RE = re.compile(r"&(?:amp|lt|gt|nbsp|quot);")

_HTML_ENTITIES = {
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&nbsp;": " ",
    "&quot;": '"',
}


def _unescape_html(text: str) -> str:
    """Replace common HTML entities."""
    for ent, ch in _HTML_ENTITIES.items():
        text = text.replace(ent, ch)
    return text


def _strip_tags(html: str) -> str:
    """Strip HTML tags from a string."""
    return _HTML_TAG_RE.sub("", html)


def extract_ac_candidates(html: str) -> list[str]:
    """Extract acceptance criteria candidates from an HTML string.

    Looks for a section headed 'Acceptance Criteria' and extracts
    list items below it. Falls back to all list items if no heading found.

    Args:
        html: HTML content from an ADO work item description.

    Returns:
        List of plain-text AC candidate strings.
    """
    candidates: list[str] = []

    # Find heading position
    heading_match = _AC_HEADING_RE.search(html)
    if heading_match:
        # Only extract content after the heading
        content = html[heading_match.end():]
    else:
        content = html

    # Extract <li> items
    for match in _HTML_LI_RE.finditer(content):
        text = _strip_tags(match.group(1)).strip()
        text = _unescape_html(text)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            candidates.append(text)

    # If no list items, try splitting on newlines after the heading
    if not candidates and heading_match:
        plain = _strip_tags(content)
        plain = _unescape_html(plain)
        for line in plain.splitlines():
            line = line.strip().lstrip("-•*").strip()
            if line and len(line) > 10:
                candidates.append(line)

    return candidates


def _prompt_ac_kind(ac_text: str, index: int) -> str:
    """Ask user whether an AC should be deterministic or rubric."""
    print(f"\n[AC {index + 1}] {ac_text}")
    print("  (d) deterministic  (r) rubric  (s) skip")
    while True:
        choice = input("  Choice [d/r/s]: ").strip().lower()
        if choice in ("d", "r", "s", ""):
            return choice or "r"
        print("  Please enter d, r, or s.")


def _slugify(text: str) -> str:
    """Convert text to a slug suitable for an AC id."""
    slug = text.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug[:50] or "criterion"


def build_task_dict(
    task_id: str,
    title: str,
    deterministic_acs: list[str],
    rubric_acs: list[str],
) -> dict[str, Any]:
    """Build a task.yaml dict from parsed ACs.

    Args:
        task_id: Slugified task identifier.
        title: Task title from ADO work item.
        deterministic_acs: List of plain-text deterministic AC descriptions.
        rubric_acs: List of plain-text rubric AC descriptions.

    Returns:
        Dict suitable for YAML serialization as task.yaml.
    """
    ac: dict[str, Any] = {}

    if deterministic_acs:
        det_list = []
        for i, desc in enumerate(deterministic_acs):
            ac_id = f"det-{i + 1}-{_slugify(desc)}"
            det_list.append({
                "id": ac_id,
                "description": desc,
                "kind": "file_exists",
                "path": "story.yaml",  # placeholder
            })
        ac["deterministic"] = det_list

    if rubric_acs:
        rub_list = []
        for i, desc in enumerate(rubric_acs):
            ac_id = f"rub-{i + 1}-{_slugify(desc)}"
            rub_list.append({
                "id": ac_id,
                "description": desc,
                "scale": "0-3",
                "threshold": 2,
                "judge_prompt": (
                    f"Evaluate whether the agent's output satisfies: {desc}\n\n"
                    "Score 0-3: 0=missing, 1=partial, 2=mostly correct, 3=fully correct"
                ),
            })
        ac["rubric"] = rub_list

    return {
        "id": task_id,
        "version": 1,
        "title": title,
        "difficulty": "medium",
        "tags": ["ado", "migrated"],
        "substrate": {"ref": "baseline-2026-04-16"},
        "workflow": {"id": "standard", "version": 1},
        "agents": [],
        "inputs": {},
        "models": {"worker": "claude-sonnet-4-5", "judge": "gpt-5.4-high"},
        "acceptance_criteria": ac,
    }


def run(argv: list[str] | None = None) -> int:
    """Run the AC migrator CLI.

    Args:
        argv: Argument list (defaults to sys.argv[1:]).

    Returns:
        Exit code.
    """
    parser = argparse.ArgumentParser(
        prog="ac-migrator",
        description="Migrate an ADO work item payload to a task corpus entry",
    )
    parser.add_argument("--from-ado", required=True, metavar="PAYLOAD.json",
                        help="Path to ADO work item JSON payload")
    parser.add_argument("--out", required=True, metavar="TASK_DIR",
                        help="Output task directory (will be created)")
    parser.add_argument("--yes", action="store_true",
                        help="Non-interactive: make all criteria rubric by default")
    args = parser.parse_args(argv)

    payload_path = Path(args.from_ado)
    if not payload_path.exists():
        print(f"ERROR: Payload not found: {payload_path}", file=sys.stderr)
        return 1

    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    fields = payload.get("fields", {})

    # Extract title and description
    title = fields.get("System.Title", "Untitled ADO Task")
    description = fields.get("System.Description", "")
    embedded_ac = fields.get("Microsoft.VSTS.Common.AcceptanceCriteria", "")

    # Combine both sources
    combined_html = description + "\n" + embedded_ac
    candidates = extract_ac_candidates(combined_html)

    if not candidates:
        print("WARNING: No acceptance criteria found in payload.", file=sys.stderr)
        candidates = ["Placeholder AC — fill in manually"]

    print(f"Found {len(candidates)} AC candidate(s) in payload.")

    det_acs: list[str] = []
    rub_acs: list[str] = []

    if args.yes:
        rub_acs = candidates
    else:
        for i, ac_text in enumerate(candidates):
            choice = _prompt_ac_kind(ac_text, i)
            if choice == "d":
                det_acs.append(ac_text)
            elif choice == "r":
                rub_acs.append(ac_text)
            # skip: do nothing

    # Derive task id from output dir
    out_dir = Path(args.out)
    task_id = out_dir.name
    task_id = re.sub(r"[^a-z0-9-]", "-", task_id.lower()).strip("-") or "migrated-task"

    task_dict = build_task_dict(task_id, title, det_acs, rub_acs)

    out_dir.mkdir(parents=True, exist_ok=True)
    task_file = out_dir / "task.yaml"
    task_file.write_text(
        yaml.dump(task_dict, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    print(f"\nWrote task.yaml to {task_file}")

    # Write placeholder deterministic scripts
    if det_acs:
        criteria_dir = out_dir / "criteria" / "deterministic"
        criteria_dir.mkdir(parents=True, exist_ok=True)
        for i, desc in enumerate(det_acs):
            script_name = f"check_{i + 1}.py"
            script_path = criteria_dir / script_name
            script_path.write_text(
                textwrap.dedent(f"""\
                    #!/usr/bin/env python3
                    \"\"\"Deterministic check: {desc}\"\"\"
                    import os, sys
                    from pathlib import Path

                    artifact_dir = Path(os.environ.get("ARTIFACT_DIR", "."))
                    # TODO: implement check
                    print("STUB: implement check for: {desc}")
                    sys.exit(0)
                """),
                encoding="utf-8",
            )
        # Update task.yaml scripts to point to actual files
        task_dict_updated = task_dict.copy()
        det_list = task_dict_updated["acceptance_criteria"].get("deterministic", [])
        for i, ac_entry in enumerate(det_list):
            ac_entry["kind"] = "script"
            ac_entry["script"] = f"criteria/deterministic/check_{i + 1}.py"
            del ac_entry["path"]
        task_file.write_text(
            yaml.dump(task_dict_updated, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        print(f"Wrote {len(det_acs)} deterministic check script(s) to {criteria_dir}")

    print("\nDone. Review and edit task.yaml before committing.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
