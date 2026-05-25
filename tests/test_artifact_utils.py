"""
Tests for workflow artifact integrity helpers.

Difficulty rubric for this file:
  easy   = single helper success/failure assertions.
  medium = cross-artifact impl_report validation behavior.
  hard   = (none in this file)
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from core.artifact_utils import (
    ImplReportValidationError,
    normalize_impl_report_file,
    snapshot_impl_report_attempt,
    validate_impl_report_alignment,
)


class ImplReportArtifactIntegrityTests(unittest.TestCase):
    def _write_yaml(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    def test_medium__validation_passes_when_report_matches_uow_spec_domain(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "agent-context"
            uow_dir = root / "5012920" / "execution" / "UOW-001"
            self._write_yaml(
                uow_dir / "uow_spec.yaml",
                {
                    "uow_id": "UOW-001",
                    "title": "Verify readonly binding for TestPillHelper",
                    "implementation_hints": [
                        "libs/pearls/specimen-accessioning/ui/orders-ui/src/lib/components/test-pills/test-pill-helper.ts"
                    ],
                },
            )
            self._write_yaml(
                uow_dir / "impl_report.yaml",
                {
                    "change_id": "5012920",
                    "uow_id": "UOW-001",
                    "implementation_summary": "Verified the test-pill readonly chain and TestPillHelper behavior.",
                    "files_modified": [],
                },
            )

            result = validate_impl_report_alignment(
                agent_context_root=root,
                change_id="5012920",
                uow_id="UOW-001",
            )

            self.assertEqual(result["status"], "pass")
            self.assertIn("testpillhelper", result["matched_domain_terms"])
            self.assertTrue((uow_dir / "impl_report_validation.yaml").is_file())

    def test_medium__validation_fails_when_report_describes_wrong_domain(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "agent-context"
            uow_dir = root / "5012920" / "execution" / "UOW-002"
            self._write_yaml(
                uow_dir / "uow_spec.yaml",
                {
                    "uow_id": "UOW-002",
                    "title": "Verify child specimen readonly propagation",
                    "implementation_hints": [
                        "libs/pearls/specimen-accessioning/ui/orders-ui/src/lib/components/test-pills/test-pill-child-specimen.component.ts"
                    ],
                },
            )
            self._write_yaml(
                uow_dir / "impl_report.yaml",
                {
                    "change_id": "5012920",
                    "uow_id": "UOW-002",
                    "implementation_summary": "Created LabResultsFilterControlsComponent in feature-lab-results.",
                    "files_modified": ["libs/lab-results/feature-lab-results/src/index.ts"],
                },
            )

            with self.assertRaises(ImplReportValidationError):
                validate_impl_report_alignment(
                    agent_context_root=root,
                    change_id="5012920",
                    uow_id="UOW-002",
                )

            validation = yaml.safe_load((uow_dir / "impl_report_validation.yaml").read_text(encoding="utf-8"))
            self.assertEqual(validation["status"], "fail")
            self.assertTrue(validation["errors"])

    def test_medium__validation_does_not_pass_on_generic_architecture_terms_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "agent-context"
            uow_dir = root / "CHANGE-1" / "execution" / "UOW-003"
            self._write_yaml(
                uow_dir / "uow_spec.yaml",
                {
                    "uow_id": "UOW-003",
                    "title": "Implement alpha ordering helper",
                    "implementation_hints": ["libs/orders/alpha-order/alpha-order-helper.ts"],
                },
            )
            self._write_yaml(
                uow_dir / "impl_report.yaml",
                {
                    "change_id": "CHANGE-1",
                    "uow_id": "UOW-003",
                    "implementation_summary": "Implemented beta billing helper.",
                    "files_modified": ["libs/billing/beta-billing/beta-billing-helper.ts"],
                },
            )

            with self.assertRaises(ImplReportValidationError):
                validate_impl_report_alignment(
                    agent_context_root=root,
                    change_id="CHANGE-1",
                    uow_id="UOW-003",
                )

    def test_medium__validation_write_failure_does_not_mask_domain_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "agent-context"
            uow_dir = root / "CHANGE-1" / "execution" / "UOW-004"
            self._write_yaml(
                uow_dir / "uow_spec.yaml",
                {
                    "uow_id": "UOW-004",
                    "title": "Implement alpha ordering",
                    "implementation_hints": ["libs/orders/alpha-order/alpha-order.component.ts"],
                },
            )
            self._write_yaml(
                uow_dir / "impl_report.yaml",
                {
                    "change_id": "CHANGE-1",
                    "uow_id": "UOW-004",
                    "implementation_summary": "Implemented beta billing.",
                },
            )

            with patch.object(Path, "write_text", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(ImplReportValidationError, "impl_report domain"):
                    validate_impl_report_alignment(
                        agent_context_root=root,
                        change_id="CHANGE-1",
                        uow_id="UOW-004",
                    )

    def test_easy__snapshot_impl_report_attempt_copies_current_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "agent-context"
            uow_dir = root / "CHANGE-1" / "execution" / "UOW-001"
            self._write_yaml(uow_dir / "impl_report.yaml", {"uow_id": "UOW-001", "status": "complete"})

            snapshot = snapshot_impl_report_attempt(
                agent_context_root=root,
                change_id="CHANGE-1",
                uow_id="UOW-001",
                attempt=2,
            )

            self.assertEqual(snapshot, uow_dir / "attempts" / "attempt-002" / "impl_report.yaml")
            self.assertTrue(snapshot.is_file())
            self.assertEqual(yaml.safe_load(snapshot.read_text(encoding="utf-8"))["status"], "complete")

    def test_easy__normalize_impl_report_repairs_unquoted_colon_in_dod_item(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "impl_report.yaml"
            report_path.write_text(
                "\n".join(
                    [
                        'change_id: "CHANGE-1"',
                        'uow_id: "UOW-001"',
                        'status: "complete"',
                        "definition_of_done_status:",
                        "  - item: Output format preserved: status remains visible",
                        "    met: true",
                        "    evidence: verified",
                    ]
                ),
                encoding="utf-8",
            )

            self.assertTrue(normalize_impl_report_file(report_path))

            report = yaml.safe_load(report_path.read_text(encoding="utf-8"))
            self.assertEqual(
                report["definition_of_done_status"][0]["item"],
                "Output format preserved: status remains visible",
            )

    def test_easy__normalize_impl_report_repairs_nested_list_mapping_value(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "impl_report.yaml"
            report_path.write_text(
                "\n".join(
                    [
                        'change_id: "CHANGE-1"',
                        'uow_id: "UOW-001"',
                        "files_modified:",
                        "  - path: src/components/Example.tsx",
                        "    change_type: modified",
                        "    change_summary: Added confirmation behavior, dismissible: boolean flag supported",
                    ]
                ),
                encoding="utf-8",
            )

            self.assertTrue(normalize_impl_report_file(report_path))

            report = yaml.safe_load(report_path.read_text(encoding="utf-8"))
            self.assertEqual(
                report["files_modified"][0]["change_summary"],
                "Added confirmation behavior, dismissible: boolean flag supported",
            )

    def test_easy__normalize_impl_report_canonicalizes_valid_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "impl_report.yaml"
            report_path.write_text(
                '{"change_id": "CHANGE-1", "uow_id": "UOW-001", "status": "complete"}\n',
                encoding="utf-8",
            )

            self.assertTrue(normalize_impl_report_file(report_path))

            self.assertEqual(
                yaml.safe_load(report_path.read_text(encoding="utf-8")),
                {"change_id": "CHANGE-1", "uow_id": "UOW-001", "status": "complete"},
            )
            self.assertIn("change_id: CHANGE-1", report_path.read_text(encoding="utf-8"))

    def test_easy__normalize_impl_report_fails_for_unrecoverable_yaml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "impl_report.yaml"
            report_path.write_text(
                "\n".join(
                    [
                        'change_id: "CHANGE-1"',
                        "files_modified:",
                        "  - path: [unterminated",
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "not valid YAML"):
                normalize_impl_report_file(report_path)

    def test_medium__validation_reports_yaml_parse_error_for_invalid_impl_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "agent-context"
            uow_dir = root / "CHANGE-1" / "execution" / "UOW-005"
            self._write_yaml(
                uow_dir / "uow_spec.yaml",
                {
                    "uow_id": "UOW-005",
                    "title": "Implement alpha ordering",
                    "implementation_hints": ["libs/orders/alpha-order/alpha-order.component.ts"],
                },
            )
            (uow_dir / "impl_report.yaml").write_text(
                "\n".join(
                    [
                        'change_id: "CHANGE-1"',
                        "files_modified:",
                        "  - path: [unterminated",
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ImplReportValidationError, "YAML parse error"):
                validate_impl_report_alignment(
                    agent_context_root=root,
                    change_id="CHANGE-1",
                    uow_id="UOW-005",
                )


if __name__ == "__main__":
    unittest.main()
