#!/usr/bin/env python3
"""Verify every acceptance criterion maps to at least one task."""

import argparse
import json
import sys

try:
    import yaml
except ImportError:
    print("Error: pyyaml is required. Install with: pip install pyyaml", file=sys.stderr)
    sys.exit(2)


def parse_acs(story: dict) -> list[str]:
    """Extract AC IDs from a story dict.

    Supports:
      - acceptance_criteria or acceptance_criteria_raw as field name
      - List of dicts with 'id' key
      - List of plain strings (auto-assigned AC1, AC2, …)
    """
    raw = story.get("acceptance_criteria") or story.get("acceptance_criteria_raw")
    if not raw:
        return []

    ac_ids: list[str] = []
    for i, item in enumerate(raw, start=1):
        if isinstance(item, dict) and "id" in item:
            ac_ids.append(str(item["id"]))
        else:
            ac_ids.append(f"AC{i}")
    return ac_ids


def parse_tasks(tasks_data: dict) -> list[dict]:
    """Extract task list from tasks dict."""
    return tasks_data.get("tasks") or []


def compute_coverage(ac_ids: list[str], tasks: list[dict]) -> dict:
    """Build the full coverage report."""
    ac_coverage_map: dict[str, list[str]] = {ac: [] for ac in ac_ids}
    tasks_with_no_ac_mapping: list[str] = []

    for task in tasks:
        task_id = str(task.get("id", ""))
        mapping = task.get("ac_mapping") or []
        if not mapping:
            tasks_with_no_ac_mapping.append(task_id)
        for ac in mapping:
            ac_str = str(ac)
            if ac_str in ac_coverage_map:
                ac_coverage_map[ac_str].append(task_id)

    unmapped_acs = [ac for ac, covered_by in ac_coverage_map.items() if not covered_by]
    status = "fail" if unmapped_acs or tasks_with_no_ac_mapping else "pass"

    return {
        "status": status,
        "total_acs": len(ac_ids),
        "total_tasks": len(tasks),
        "unmapped_acs": unmapped_acs,
        "tasks_with_no_ac_mapping": tasks_with_no_ac_mapping,
        "ac_coverage_map": ac_coverage_map,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check that every acceptance criterion is covered by at least one task."
    )
    parser.add_argument("story_yaml", help="Path to the story YAML file")
    parser.add_argument("tasks_yaml", help="Path to the tasks YAML file")
    args = parser.parse_args()

    try:
        with open(args.story_yaml, "r") as f:
            story_data = yaml.safe_load(f) or {}
        with open(args.tasks_yaml, "r") as f:
            tasks_data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        print(f"Error reading input files: {exc}", file=sys.stderr)
        return 2

    ac_ids = parse_acs(story_data)
    tasks = parse_tasks(tasks_data)
    report = compute_coverage(ac_ids, tasks)

    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
