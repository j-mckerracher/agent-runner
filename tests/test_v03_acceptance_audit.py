"""Machine verification of `docs/refactor/v0.3-acceptance-audit.json`.

This audit file is the human-authored claim that every v0.3 acceptance criterion
(AC-v0.3-01..17) is satisfied. A claim like that is only worth something if it is checked
mechanically: this test confirms every AC id is present exactly once, every status is one of
the allowed values, every `pass` entry carries evidence, every plain file/doc evidence path
actually exists on disk, and -- the check most likely to silently rot -- every
`path/to/test.py::node::id`-shaped evidence entry is still a real, collectable pytest item and
resolves to exactly one of them (not zero because the test was renamed/deleted, not more than
one because the reference was accidentally left at the class level).
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
AUDIT_PATH = REPO_ROOT / "docs" / "refactor" / "v0.3-acceptance-audit.json"
ALLOWED_STATUSES = {"pass", "exception", "fail"}
EXPECTED_IDS = {f"AC-v0.3-{i:02d}" for i in range(1, 18)}


def _load_audit() -> dict:
    return json.loads(AUDIT_PATH.read_text(encoding="utf-8"))


def _collect_count(node_id: str) -> int:
    """Return how many pytest items `node_id` collects to, via a real subprocess.

    A subprocess (not `pytest.main` in-process) is used deliberately: collection has
    process-global side effects (module import caching) that make repeated in-process
    `pytest.main(["--collect-only", ...])` calls unreliable within the same test run.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", node_id],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    collected = [
        line for line in result.stdout.splitlines() if line.strip() and "::" in line and line.startswith("tests/")
    ]
    return len(collected)


class AuditStructureTests(unittest.TestCase):
    def test_easy__audit_file_exists_and_parses(self):
        self.assertTrue(AUDIT_PATH.exists(), f"missing {AUDIT_PATH}")
        data = _load_audit()
        self.assertIn("criteria", data)
        self.assertIn("overall_status", data)

    def test_easy__all_seventeen_ac_ids_present_exactly_once(self):
        data = _load_audit()
        ids = [c["id"] for c in data["criteria"]]
        self.assertEqual(len(ids), len(set(ids)), f"duplicate AC ids found: {ids}")
        self.assertEqual(set(ids), EXPECTED_IDS, f"AC id set mismatch: {set(ids)} != {EXPECTED_IDS}")

    def test_easy__every_status_is_one_of_the_allowed_values(self):
        data = _load_audit()
        for c in data["criteria"]:
            self.assertIn(
                c["status"], ALLOWED_STATUSES, f"{c['id']} has invalid status {c['status']!r}"
            )

    def test_medium__every_passing_criterion_has_nonempty_evidence(self):
        data = _load_audit()
        for c in data["criteria"]:
            if c["status"] == "pass":
                self.assertTrue(c.get("evidence"), f"{c['id']} is pass but has no evidence")

    def test_medium__every_exception_criterion_documents_incompleteness_in_notes(self):
        data = _load_audit()
        for c in data["criteria"]:
            if c["status"] == "exception":
                self.assertTrue(c.get("notes"), f"{c['id']} is exception but has no notes")

    def test_medium__overall_status_is_not_pass_if_any_criterion_failed(self):
        data = _load_audit()
        any_failed = any(c["status"] == "fail" for c in data["criteria"])
        if any_failed:
            self.assertNotEqual(
                data["overall_status"], "pass", "overall_status is pass despite a failing criterion"
            )

    def test_hard__overall_status_currently_reflects_all_criteria_passing(self):
        # This is the actual closure gate for Prompt 11: as of this test, every criterion
        # must be `pass` and the file must self-report `pass` overall. If a future criterion
        # regresses to `exception`/`fail`, this test is expected to fail until the audit (and
        # this assertion, deliberately) are revisited together.
        data = _load_audit()
        failing = [c["id"] for c in data["criteria"] if c["status"] != "pass"]
        self.assertEqual(failing, [], f"non-passing criteria: {failing}")
        self.assertEqual(data["overall_status"], "pass")


class AuditEvidenceResolutionTests(unittest.TestCase):
    """Every evidence string must point at something real."""

    def _all_evidence(self):
        data = _load_audit()
        for c in data["criteria"]:
            for e in c["evidence"]:
                yield c["id"], e

    def test_medium__plain_path_evidence_exists_on_disk(self):
        missing = []
        for ac_id, e in self._all_evidence():
            if e.startswith("tests/") and "::" in e:
                continue  # handled by the pytest-collection tests below
            # Evidence for implementation symbols is written as "path.py::Symbol" or
            # "path.py (free-form note)" -- strip both trailing shapes to the bare path.
            base = e.split("::")[0].split(" (")[0].strip()
            if not (REPO_ROOT / base).exists():
                missing.append((ac_id, e, base))
        self.assertEqual(missing, [], f"evidence paths that do not exist: {missing}")

    def test_hard__every_test_node_id_evidence_collects_to_exactly_one_item(self):
        node_ids = sorted(
            {e for _, e in self._all_evidence() if e.startswith("tests/") and "::" in e}
        )
        self.assertTrue(node_ids, "expected at least one test::node_id evidence entry")
        bad = {}
        for node_id in node_ids:
            count = _collect_count(node_id)
            if count != 1:
                bad[node_id] = count
        self.assertEqual(
            bad,
            {},
            "evidence test node ids that do not collect to exactly one item "
            f"(likely renamed/deleted test or a class-level, not method-level, reference): {bad}",
        )


if __name__ == "__main__":
    unittest.main()
