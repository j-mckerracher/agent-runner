#!/usr/bin/env python3
"""Validate YAML/JSON artifacts against expected schema definitions.

Usage:
    validate-artifact-schema.py --type <artifact_type> <artifact_path>

Supported artifact types: tasks, assignments, impl_report, qa_report
Exit codes: 0 = valid, 1 = violations found, 2 = usage/parse error
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Union

try:
    import yaml
except ImportError:
    sys.stderr.write("Error: pyyaml is required. Install with: pip install pyyaml\n")
    sys.exit(2)

ARTIFACT_TYPES = ("tasks", "assignments", "impl_report", "qa_report")


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

class SchemaValidator:
    """Collects validation issues while walking a data structure."""

    def __init__(self):
        self.issues: List[dict] = []

    def _add(self, path: str, issue: str, severity: str = "critical"):
        self.issues.append({"path": path, "issue": issue, "severity": severity})

    # -- primitive checks ---------------------------------------------------

    def require_key(self, data: dict, key: str, path: str) -> bool:
        if key not in data:
            self._add(f"{path}.{key}", f"Required field '{key}' is missing")
            return False
        return True

    def check_type(self, value, expected, path: str) -> bool:
        # bool is subclass of int in Python; handle explicitly
        if expected is int and isinstance(value, bool):
            self._add(path, f"Expected type int, got bool")
            return False
        if expected is str and not isinstance(value, str):
            self._add(path, f"Expected type str, got {type(value).__name__}")
            return False
        if expected is int and not isinstance(value, int):
            self._add(path, f"Expected type int, got {type(value).__name__}")
            return False
        if expected is bool and not isinstance(value, bool):
            self._add(path, f"Expected type bool, got {type(value).__name__}")
            return False
        if expected is list and not isinstance(value, list):
            self._add(path, f"Expected type list, got {type(value).__name__}")
            return False
        if expected is dict and not isinstance(value, dict):
            self._add(path, f"Expected type dict, got {type(value).__name__}")
            return False
        return True

    def check_non_empty(self, value, path: str) -> bool:
        if isinstance(value, (str, list, dict)) and len(value) == 0:
            self._add(path, "Required field is empty")
            return False
        return True

    def check_enum(self, value, allowed: tuple | list, path: str) -> bool:
        if value not in allowed:
            self._add(path, f"Value '{value}' not in allowed values: {allowed}",
                       severity="critical")
            return False
        return True

    def check_list_of(self, value: list, elem_type, path: str):
        if not isinstance(value, list):
            return
        for i, item in enumerate(value):
            self.check_type(item, elem_type, f"{path}[{i}]")


# ---------------------------------------------------------------------------
# Per-type validators
# ---------------------------------------------------------------------------

def validate_tasks(data, v: SchemaValidator):
    root = "tasks"
    if not v.require_key(data, "tasks", ""):
        return
    tasks_list = data["tasks"]
    if not v.check_type(tasks_list, list, root):
        return
    if not v.check_non_empty(tasks_list, root):
        return

    for i, task in enumerate(tasks_list):
        tp = f"{root}[{i}]"
        if not isinstance(task, dict):
            v._add(tp, f"Expected dict, got {type(task).__name__}")
            continue

        # id – required, str, non-empty
        if v.require_key(task, "id", tp):
            if v.check_type(task["id"], str, f"{tp}.id"):
                v.check_non_empty(task["id"], f"{tp}.id")

        # title – required, str, non-empty
        if v.require_key(task, "title", tp):
            if v.check_type(task["title"], str, f"{tp}.title"):
                v.check_non_empty(task["title"], f"{tp}.title")

        # description – optional, str
        if "description" in task and task["description"] is not None:
            v.check_type(task["description"], str, f"{tp}.description")

        # ac_mapping – required, list of str, non-empty
        if v.require_key(task, "ac_mapping", tp):
            ac = task["ac_mapping"]
            if v.check_type(ac, list, f"{tp}.ac_mapping"):
                if v.check_non_empty(ac, f"{tp}.ac_mapping"):
                    v.check_list_of(ac, str, f"{tp}.ac_mapping")

        # dependencies – optional, list of str
        if "dependencies" in task and task["dependencies"] is not None:
            deps = task["dependencies"]
            if v.check_type(deps, list, f"{tp}.dependencies"):
                v.check_list_of(deps, str, f"{tp}.dependencies")

        # priority – optional, enum
        if "priority" in task and task["priority"] is not None:
            if v.check_type(task["priority"], str, f"{tp}.priority"):
                v.check_enum(task["priority"], ("high", "medium", "low"),
                             f"{tp}.priority")

        # complexity – optional, enum
        if "complexity" in task and task["complexity"] is not None:
            if v.check_type(task["complexity"], str, f"{tp}.complexity"):
                v.check_enum(task["complexity"],
                             ("simple", "moderate", "complex"),
                             f"{tp}.complexity")

        # definition_of_done – optional, list of str
        if "definition_of_done" in task and task["definition_of_done"] is not None:
            dod = task["definition_of_done"]
            if v.check_type(dod, list, f"{tp}.definition_of_done"):
                v.check_list_of(dod, str, f"{tp}.definition_of_done")


def validate_assignments(data, v: SchemaValidator):
    root = "batches"
    if not v.require_key(data, "batches", ""):
        return
    batches = data["batches"]
    if not v.check_type(batches, list, root):
        return
    if not v.check_non_empty(batches, root):
        return

    for i, batch in enumerate(batches):
        bp = f"{root}[{i}]"
        if not isinstance(batch, dict):
            v._add(bp, f"Expected dict, got {type(batch).__name__}")
            continue

        # batch_id – required, int
        if v.require_key(batch, "batch_id", bp):
            v.check_type(batch["batch_id"], int, f"{bp}.batch_id")

        # uows – required, non-empty list
        if not v.require_key(batch, "uows", bp):
            continue
        uows = batch["uows"]
        if not v.check_type(uows, list, f"{bp}.uows"):
            continue
        if not v.check_non_empty(uows, f"{bp}.uows"):
            continue

        for j, uow in enumerate(uows):
            up = f"{bp}.uows[{j}]"
            if not isinstance(uow, dict):
                v._add(up, f"Expected dict, got {type(uow).__name__}")
                continue

            # uow_id – required, str, non-empty
            if v.require_key(uow, "uow_id", up):
                if v.check_type(uow["uow_id"], str, f"{up}.uow_id"):
                    v.check_non_empty(uow["uow_id"], f"{up}.uow_id")

            # source_task_id – required, str
            if v.require_key(uow, "source_task_id", up):
                v.check_type(uow["source_task_id"], str,
                             f"{up}.source_task_id")

            # title – optional, str
            if "title" in uow and uow["title"] is not None:
                v.check_type(uow["title"], str, f"{up}.title")

            # dependencies – optional, list of str
            if "dependencies" in uow and uow["dependencies"] is not None:
                deps = uow["dependencies"]
                if v.check_type(deps, list, f"{up}.dependencies"):
                    v.check_list_of(deps, str, f"{up}.dependencies")

            # definition_of_done – optional, list of str
            if "definition_of_done" in uow and uow["definition_of_done"] is not None:
                dod = uow["definition_of_done"]
                if v.check_type(dod, list, f"{up}.definition_of_done"):
                    v.check_list_of(dod, str, f"{up}.definition_of_done")


def validate_impl_report(data, v: SchemaValidator):
    if not isinstance(data, dict):
        v._add("", f"Expected top-level dict, got {type(data).__name__}")
        return

    # uow_id – required, str
    if v.require_key(data, "uow_id", ""):
        v.check_type(data["uow_id"], str, "uow_id")

    # status – required, enum
    if v.require_key(data, "status", ""):
        if v.check_type(data["status"], str, "status"):
            v.check_enum(data["status"], ("complete", "partial", "blocked"),
                         "status")

    # implementation_summary – required, str
    if v.require_key(data, "implementation_summary", ""):
        v.check_type(data["implementation_summary"], str,
                     "implementation_summary")

    # files_modified – required, list of dicts with path (str) + change_type (str)
    if v.require_key(data, "files_modified", ""):
        fm = data["files_modified"]
        if v.check_type(fm, list, "files_modified"):
            for i, entry in enumerate(fm):
                ep = f"files_modified[{i}]"
                if not isinstance(entry, dict):
                    v._add(ep, f"Expected dict, got {type(entry).__name__}")
                    continue
                if v.require_key(entry, "path", ep):
                    v.check_type(entry["path"], str, f"{ep}.path")
                if v.require_key(entry, "change_type", ep):
                    v.check_type(entry["change_type"], str,
                                 f"{ep}.change_type")

    # definition_of_done_status – required, list of dicts with item (str) + met (bool)
    if v.require_key(data, "definition_of_done_status", ""):
        dods = data["definition_of_done_status"]
        if v.check_type(dods, list, "definition_of_done_status"):
            for i, entry in enumerate(dods):
                ep = f"definition_of_done_status[{i}]"
                if not isinstance(entry, dict):
                    v._add(ep, f"Expected dict, got {type(entry).__name__}")
                    continue
                if v.require_key(entry, "item", ep):
                    v.check_type(entry["item"], str, f"{ep}.item")
                if v.require_key(entry, "met", ep):
                    v.check_type(entry["met"], bool, f"{ep}.met")

    # Optional fields
    if "library_research" in data and data["library_research"] is not None:
        v.check_type(data["library_research"], dict, "library_research")
    if "test_results" in data and data["test_results"] is not None:
        v.check_type(data["test_results"], dict, "test_results")
    if "risks_identified" in data and data["risks_identified"] is not None:
        v.check_type(data["risks_identified"], list, "risks_identified")
    if "revision_history" in data and data["revision_history"] is not None:
        v.check_type(data["revision_history"], list, "revision_history")


def validate_qa_report(data, v: SchemaValidator):
    if not isinstance(data, dict):
        v._add("", f"Expected top-level dict, got {type(data).__name__}")
        return

    # change_id – required, str
    if v.require_key(data, "change_id", ""):
        v.check_type(data["change_id"], str, "change_id")

    # overall_status – required, enum
    if v.require_key(data, "overall_status", ""):
        if v.check_type(data["overall_status"], str, "overall_status"):
            v.check_enum(data["overall_status"],
                         ("pass", "fail", "conditional_pass"),
                         "overall_status")

    # ac_validations – required, list of dicts
    if v.require_key(data, "ac_validations", ""):
        acv = data["ac_validations"]
        if v.check_type(acv, list, "ac_validations"):
            for i, entry in enumerate(acv):
                ep = f"ac_validations[{i}]"
                if not isinstance(entry, dict):
                    v._add(ep, f"Expected dict, got {type(entry).__name__}")
                    continue
                if v.require_key(entry, "ac_id", ep):
                    v.check_type(entry["ac_id"], str, f"{ep}.ac_id")
                if v.require_key(entry, "status", ep):
                    v.check_type(entry["status"], str, f"{ep}.status")
                if v.require_key(entry, "evidence", ep):
                    ev = entry["evidence"]
                    if not isinstance(ev, (str, list)):
                        v._add(f"{ep}.evidence",
                               f"Expected str or list, got {type(ev).__name__}")

    # Optional fields
    if "regression_risk" in data and data["regression_risk"] is not None:
        v.check_type(data["regression_risk"], dict, "regression_risk")
    if "release_notes" in data and data["release_notes"] is not None:
        rn = data["release_notes"]
        if not isinstance(rn, (str, list)):
            v._add("release_notes",
                   f"Expected str or list, got {type(rn).__name__}")
    if "remediation_items" in data and data["remediation_items"] is not None:
        v.check_type(data["remediation_items"], list, "remediation_items")


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

VALIDATORS = {
    "tasks": validate_tasks,
    "assignments": validate_assignments,
    "impl_report": validate_impl_report,
    "qa_report": validate_qa_report,
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_artifact(path: Path):
    """Load a YAML or JSON file and return its parsed content."""
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        return yaml.safe_load(text)
    if suffix == ".json":
        return json.loads(text)
    # Fall back: try YAML first (superset of JSON), then JSON
    try:
        return yaml.safe_load(text)
    except Exception:
        return json.loads(text)


def main():
    parser = argparse.ArgumentParser(
        description="Validate YAML/JSON artifacts against schema definitions."
    )
    parser.add_argument(
        "--type",
        required=True,
        choices=ARTIFACT_TYPES,
        dest="artifact_type",
        help="Artifact type to validate against.",
    )
    parser.add_argument(
        "artifact_path",
        help="Path to the artifact file.",
    )
    args = parser.parse_args()

    artifact_path = Path(args.artifact_path)
    if not artifact_path.is_file():
        sys.stderr.write(f"Error: file not found: {artifact_path}\n")
        sys.exit(2)

    try:
        data = load_artifact(artifact_path)
    except Exception as exc:
        sys.stderr.write(f"Error: failed to parse {artifact_path}: {exc}\n")
        sys.exit(2)

    if data is None:
        sys.stderr.write(f"Error: {artifact_path} is empty or null\n")
        sys.exit(2)

    validator = SchemaValidator()
    VALIDATORS[args.artifact_type](data, validator)

    result = {
        "status": "fail" if validator.issues else "pass",
        "artifact_type": args.artifact_type,
        "artifact_path": str(artifact_path),
        "issues": validator.issues,
    }
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")

    sys.exit(1 if validator.issues else 0)


if __name__ == "__main__":
    main()
