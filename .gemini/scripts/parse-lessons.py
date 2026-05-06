#!/usr/bin/env python3
"""Parse lessons.md, extract mistake signatures, and compute repeat rates."""

import argparse
import json
import re
import string
import sys
from collections import defaultdict
from pathlib import Path

try:
    import yaml
except ImportError:
    print("Error: pyyaml is required. Install with: pip install pyyaml", file=sys.stderr)
    sys.exit(2)


def normalize_mistake_pattern(pattern: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = pattern.lower()
    text = text.translate(str.maketrans("", "", string.punctuation))
    text = re.sub(r"\s+", " ", text).strip()
    return text


def make_signature(lesson: dict) -> tuple:
    """Build a normalized signature tuple from a lesson dict."""
    agent = (lesson.get("agent") or "").strip()
    pattern = normalize_mistake_pattern(lesson.get("mistake_pattern") or "")
    ctx = sorted(
        str(c).strip() for c in (lesson.get("trigger_context") or [])
    )
    return (agent, pattern, tuple(ctx))


def extract_lessons_from_yaml(text: str) -> list:
    """Try to parse text as YAML and return a list of lesson dicts."""
    docs = list(yaml.safe_load_all(text))
    lessons = []
    for doc in docs:
        if isinstance(doc, list):
            lessons.extend(d for d in doc if isinstance(d, dict))
        elif isinstance(doc, dict):
            lessons.append(doc)
    return lessons


def extract_yaml_from_markdown(text: str) -> list:
    """Extract YAML blocks from markdown fenced code blocks."""
    pattern = re.compile(r"```(?:ya?ml)?\s*\n(.*?)```", re.DOTALL)
    lessons = []
    for match in pattern.finditer(text):
        block = match.group(1)
        try:
            parsed = yaml.safe_load(block)
            if isinstance(parsed, list):
                lessons.extend(d for d in parsed if isinstance(d, dict))
            elif isinstance(parsed, dict):
                lessons.append(parsed)
        except yaml.YAMLError:
            continue
    return lessons


def extract_inline_yaml_items(text: str) -> list:
    """Extract YAML list items that start with '- lesson_id:' from raw text."""
    chunks = re.split(r"(?m)^- (?=lesson_id:)", text)
    lessons = []
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        yaml_text = "- " + chunk if not chunk.startswith("- ") else chunk
        try:
            parsed = yaml.safe_load(yaml_text)
            if isinstance(parsed, list):
                lessons.extend(d for d in parsed if isinstance(d, dict))
            elif isinstance(parsed, dict):
                lessons.append(parsed)
        except yaml.YAMLError:
            continue
    return lessons


def parse_lessons_file(path: Path) -> list:
    """Parse a lessons.md file and return a list of lesson dicts."""
    text = path.read_text(encoding="utf-8")

    # Strategy 1: try parsing the entire file as YAML
    try:
        lessons = extract_lessons_from_yaml(text)
        if lessons and any("lesson_id" in l for l in lessons):
            return [l for l in lessons if "lesson_id" in l]
    except yaml.YAMLError:
        pass

    # Strategy 2: extract from markdown fenced code blocks
    lessons = extract_yaml_from_markdown(text)
    if lessons and any("lesson_id" in l for l in lessons):
        return [l for l in lessons if "lesson_id" in l]

    # Strategy 3: extract inline YAML items starting with '- lesson_id:'
    lessons = extract_inline_yaml_items(text)
    if lessons and any("lesson_id" in l for l in lessons):
        return [l for l in lessons if "lesson_id" in l]

    return []


def compute_repeat_rates(lessons: list) -> dict:
    """Compute signatures and repeat rates from parsed lessons."""
    sig_map = defaultdict(list)
    for lesson in lessons:
        sig = make_signature(lesson)
        sig_map[sig].append(lesson.get("lesson_id", "unknown"))

    total = len(lessons)
    unique = len(sig_map)
    repeated = total - unique
    rate = repeated / total if total > 0 else 0.0

    signatures = []
    for (agent, pattern, ctx), ids in sorted(sig_map.items(), key=lambda x: -len(x[1])):
        signatures.append({
            "agent": agent,
            "mistake_pattern": pattern,
            "trigger_context": list(ctx),
            "occurrences": len(ids),
            "lesson_ids": ids,
        })

    return {
        "lessons_count": total,
        "unique_signatures": unique,
        "repeated_signatures": repeated,
        "baseline_repeat_rate": round(rate, 4),
        "signatures": signatures,
        "tracker_updated": False,
    }


def update_tracker(tracker_path: Path, result: dict) -> bool:
    """Merge signature data into the tracker JSON file."""
    tracker = {}
    if tracker_path.exists():
        try:
            tracker = json.loads(tracker_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            tracker = {}

    if "signatures" not in tracker:
        tracker["signatures"] = {}

    for sig in result["signatures"]:
        key = json.dumps(
            [sig["agent"], sig["mistake_pattern"], sig["trigger_context"]],
            sort_keys=True,
        )
        existing = tracker["signatures"].get(key, {"total_occurrences": 0, "lesson_ids": []})
        merged_ids = list(dict.fromkeys(existing["lesson_ids"] + sig["lesson_ids"]))
        tracker["signatures"][key] = {
            "agent": sig["agent"],
            "mistake_pattern": sig["mistake_pattern"],
            "trigger_context": sig["trigger_context"],
            "total_occurrences": len(merged_ids),
            "lesson_ids": merged_ids,
        }

    tracker["last_baseline_repeat_rate"] = result["baseline_repeat_rate"]
    tracker["last_lessons_count"] = result["lessons_count"]

    try:
        tracker_path.write_text(
            json.dumps(tracker, indent=2) + "\n", encoding="utf-8"
        )
        return True
    except OSError as exc:
        print(f"Warning: could not write tracker: {exc}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Parse lessons.md, extract mistake signatures, compute repeat rates."
    )
    parser.add_argument("lessons_md_path", help="Path to the lessons.md file")
    parser.add_argument(
        "--tracker",
        metavar="mistake_rate_tracker_json",
        help="Path to a JSON tracker file for cross-session signature counts",
    )
    args = parser.parse_args()

    lessons_path = Path(args.lessons_md_path)
    if not lessons_path.exists():
        print(f"Error: file not found: {lessons_path}", file=sys.stderr)
        sys.exit(2)

    try:
        lessons = parse_lessons_file(lessons_path)
    except Exception as exc:
        print(f"Error parsing lessons file: {exc}", file=sys.stderr)
        sys.exit(1)

    if not lessons:
        print("Warning: no lessons found in file", file=sys.stderr)

    result = compute_repeat_rates(lessons)

    if args.tracker:
        tracker_path = Path(args.tracker)
        if update_tracker(tracker_path, result):
            result["tracker_updated"] = True

    print(json.dumps(result, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
