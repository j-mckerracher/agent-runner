from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

CheckFn = Callable[[], bool]
DEFAULT_STORY_ID = "EVAL-001"


@dataclass(frozen=True)
class CheckDefinition:
    name: str
    evaluator: CheckFn


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _contains(path: Path, needle: str) -> CheckFn:
    def evaluator() -> bool:
        return needle in _read_text(path)

    return evaluator


def _matches(path: Path, pattern: str) -> CheckFn:
    regex = re.compile(pattern, re.MULTILINE | re.DOTALL)

    def evaluator() -> bool:
        return bool(regex.search(_read_text(path)))

    return evaluator


def _command(command: list[str], cwd: Path, timeout: int) -> CheckFn:
    def evaluator() -> bool:
        try:
            proc = subprocess.run(
                command,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
        return proc.returncode == 0

    return evaluator


def build_eval_001_checks(mono_root: str | Path, timeout: int = 600) -> list[CheckDefinition]:
    mono_root_path = Path(mono_root)
    comp_dir = mono_root_path / "libs/pearls/sendouts/ui/manifest-ui/src/lib/components/manifest-header"
    header_ts = comp_dir / "manifest-header.component.ts"
    header_html = comp_dir / "manifest-header.component.html"
    harness_ts = comp_dir / "manifest-header.component.test-harness.ts"
    cy_ts = comp_dir / "manifest-header.component.cy.ts"
    locators_ts = mono_root_path / "libs/pearls/sendouts/common/src/testing/locators/manifestCreateEdit.locators.ts"

    return [
        CheckDefinition("badge_data_test_id", _contains(header_html, 'data-test-id="specimen-count-badge"')),
        CheckDefinition(
            "specimen_count_computed",
            _matches(header_ts, r"specimenCount\s*=\s*(computed|signal)|(computed|signal)[^;]*specimenCount"),
        ),
        CheckDefinition("badge_import", _contains(header_ts, "from 'primeng/badge'")),
        CheckDefinition(
            "nx_build",
            _command(["npx", "nx", "build", "rls-sendouts-ui-manifest-ui", "--skip-nx-cache"], mono_root_path, timeout),
        ),
        CheckDefinition("locator_entry_name", _contains(locators_ts, "specimenCountBadge")),
        CheckDefinition("locator_data_test_id", _contains(locators_ts, "specimen-count-badge")),
        CheckDefinition("harness_assertion", _matches(harness_ts, r"specimenCount|specimen.*[Cc]ount")),
        CheckDefinition("cypress_test_case", _matches(cy_ts, r"specimen count")),
    ]


def build_eval_002_checks(mono_root: str | Path, timeout: int = 600) -> list[CheckDefinition]:
    mono_root_path = Path(mono_root)
    comp_dir = mono_root_path / "libs/pearls/sendouts/ui/manifest-ui/src/lib/components/manifest-header"
    header_ts = comp_dir / "manifest-header.component.ts"
    header_html = comp_dir / "manifest-header.component.html"
    harness_ts = comp_dir / "manifest-header.component.test-harness.ts"
    cy_ts = comp_dir / "manifest-header.component.cy.ts"
    locators_ts = mono_root_path / "libs/pearls/sendouts/common/src/testing/locators/manifestCreateEdit.locators.ts"

    return [
        CheckDefinition("summary_container_data_test_id", _contains(header_html, 'data-test-id="manifest-specimen-summary"')),
        CheckDefinition("status_data_test_id", _contains(header_html, 'data-test-id="manifest-specimen-status"')),
        CheckDefinition(
            "specimen_summary_text_computed",
            _matches(header_ts, r"specimenSummaryText\s*=\s*computed"),
        ),
        CheckDefinition("has_specimens_computed", _matches(header_ts, r"hasSpecimens\s*=\s*computed")),
        CheckDefinition("template_uses_has_specimens", _contains(header_html, "hasSpecimens()")),
        CheckDefinition("template_empty_state_class", _matches(header_html, r"is-empty|empty-state")),
        CheckDefinition("locator_manifest_specimen_summary", _contains(locators_ts, "manifestSpecimenSummary")),
        CheckDefinition("locator_manifest_specimen_status", _contains(locators_ts, "manifestSpecimenStatus")),
        CheckDefinition("harness_summary_assertion", _contains(harness_ts, "shouldDisplaySpecimenSummary")),
        CheckDefinition("harness_empty_state_assertion", _contains(harness_ts, "shouldShowEmptyState")),
        CheckDefinition(
            "cypress_summary_test_name",
            _contains(cy_ts, "should display specimen summary text"),
        ),
        CheckDefinition(
            "cypress_empty_state_test_name",
            _contains(cy_ts, "should show empty specimen state"),
        ),
        CheckDefinition(
            "nx_component_test",
            _command(
                [
                    "npx",
                    "nx",
                    "component-test",
                    "rls-sendouts-ui-manifest-ui",
                    "--browser=chrome",
                    "--skip-nx-cache",
                ],
                mono_root_path,
                timeout,
            ),
        ),
        CheckDefinition(
            "nx_build",
            _command(["npx", "nx", "build", "rls-sendouts-ui-manifest-ui", "--skip-nx-cache"], mono_root_path, timeout),
        ),
    ]


def build_eval_003_checks(mono_root: str | Path, timeout: int = 600) -> list[CheckDefinition]:
    mono_root_path = Path(mono_root)
    orders_ui = mono_root_path / "libs/pearls/specimen-accessioning/ui/orders-ui/src/lib"
    pill_dir = orders_ui / "components/test-pills"

    helper_ts  = pill_dir / "test-pill-helpers/test-pill-helper.ts"
    comp_ts    = pill_dir / "test-pill/test-pill.component.ts"
    comp_html  = pill_dir / "test-pill/test-pill.component.html"
    harness_ts = pill_dir / "test-pill/test-pill.component.test-harness.ts"
    cy_ts      = pill_dir / "test-pill/test-pill.component.cy.ts"
    locators   = mono_root_path / "libs/pearls/specimen-accessioning/common/src/testing/locators/test-pill.locators.ts"

    return [
        CheckDefinition("helper_method_exists",       _matches(helper_ts, r"getSpecimenCountState")),
        CheckDefinition("helper_reads_specimens",      _matches(helper_ts, r"specimens[?\.]*(length|\.length)")),
        CheckDefinition("helper_checks_preferred",     _matches(helper_ts, r"preferredNumOfSpecimens")),
        CheckDefinition("helper_is_static",            _matches(helper_ts, r"static\s+getSpecimenCountState")),
        CheckDefinition("component_uses_helper",       _contains(comp_ts,  "getSpecimenCountState")),
        CheckDefinition("component_count_signal",      _matches(comp_ts,   r"specimenCount.*=\s*(computed|signal)\(")),
        CheckDefinition("template_badge_data_test_id", _contains(comp_html, 'data-test-id="tp-specimen-count"')),
        CheckDefinition("template_overflow_class",     _contains(comp_html, "specimen-count-overflow")),
        CheckDefinition("template_empty_class",        _contains(comp_html, "specimen-count-empty")),
        CheckDefinition("locator_entry_exists",        _contains(locators,  "specimenCountBadge")),
        CheckDefinition("locator_uses_select_utility", _matches(locators,  r"selectByDataTestId\(['\"]tp-specimen-count")),
        CheckDefinition("harness_badge_getter",        _contains(harness_ts, "getSpecimenCountBadge")),
        CheckDefinition("harness_overflow_method",     _contains(harness_ts, "shouldShowSpecimenOverflow")),
        CheckDefinition("harness_empty_method",        _contains(harness_ts, "shouldShowSpecimenEmpty")),
        CheckDefinition("cypress_count_test",          _contains(cy_ts, "should display specimen count badge")),
        CheckDefinition("cypress_overflow_test",       _contains(cy_ts, "should show overflow when specimens exceed preferred")),
        CheckDefinition("cypress_empty_test",          _contains(cy_ts, "should show empty state with no specimens")),
        CheckDefinition("cypress_mock_preferred",      _matches(cy_ts,  r"preferredNumOfSpecimens\s*:\s*\d")),
        CheckDefinition(
            "nx_component_test",
            _command(
                ["npx", "nx", "component-test", "specimen-accessioning-orders-ui", "--browser=chrome", "--skip-nx-cache"],
                mono_root_path,
                timeout,
            ),
        ),
        CheckDefinition(
            "nx_build",
            _command(["npx", "nx", "build", "specimen-accessioning-orders-ui", "--skip-nx-cache"], mono_root_path, timeout),
        ),
    ]


STORY_CHECK_BUILDERS: dict[str, Callable[[str | Path, int], list[CheckDefinition]]] = {
    "EVAL-001": build_eval_001_checks,
    "EVAL-002": build_eval_002_checks,
    "EVAL-003": build_eval_003_checks,
}


def resolve_story_change_id(change_id: str | None = None, story_file: str | Path | None = None) -> str:
    if change_id:
        return change_id
    if story_file:
        story = json.loads(Path(story_file).read_text(encoding="utf-8"))
        resolved = story.get("change_id")
        if not isinstance(resolved, str) or not resolved.strip():
            raise ValueError(f"Story file {story_file} is missing a non-empty change_id")
        return resolved
    return DEFAULT_STORY_ID


def get_story_checks(
    mono_root: str | Path,
    change_id: str | None = None,
    story_file: str | Path | None = None,
    timeout: int = 600,
) -> tuple[str, list[CheckDefinition]]:
    resolved_change_id = resolve_story_change_id(change_id=change_id, story_file=story_file)
    try:
        builder = STORY_CHECK_BUILDERS[resolved_change_id]
    except KeyError as exc:
        known = ", ".join(sorted(STORY_CHECK_BUILDERS))
        raise ValueError(f"Unsupported eval story '{resolved_change_id}'. Known stories: {known}") from exc
    return resolved_change_id, builder(mono_root, timeout)


def run_story_checks(
    mono_root: str | Path,
    change_id: str | None = None,
    story_file: str | Path | None = None,
    timeout: int = 600,
) -> dict:
    resolved_change_id, checks = get_story_checks(
        mono_root=mono_root,
        change_id=change_id,
        story_file=story_file,
        timeout=timeout,
    )

    results = []
    passing = 0
    total = len(checks)
    for index, check in enumerate(checks, start=1):
        passed = bool(check.evaluator())
        if passed:
            passing += 1
        results.append({"id": index, "name": check.name, "passed": passed})

    score = round((passing / total) * 100) if total else 0
    return {
        "story": resolved_change_id,
        "checks": results,
        "passing": passing,
        "total": total,
        "score": score,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run story-specific evaluation checks.")
    parser.add_argument("--mono-root", required=True, help="Absolute path to the target monorepo.")
    parser.add_argument("--change-id", default=None, help="Evaluation story id, e.g. EVAL-001.")
    parser.add_argument("--story-file", default=None, help="Path to an eval story JSON file.")
    parser.add_argument("--timeout", type=int, default=600, help="Per-command timeout in seconds.")
    args = parser.parse_args()

    results = run_story_checks(
        mono_root=args.mono_root,
        change_id=args.change_id,
        story_file=args.story_file,
        timeout=args.timeout,
    )
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()

