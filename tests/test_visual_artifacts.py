"""Tests for core/visual_artifacts.py."""

import os
import textwrap
import webbrowser
from pathlib import Path
from unittest import mock

import pytest
import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_files(tmp_path: Path, change_id: str) -> Path:
    """Write minimal fixture artifacts for a change."""
    base = tmp_path / change_id
    (base / "intake").mkdir(parents=True)
    (base / "planning").mkdir(parents=True)
    (base / "pr").mkdir(parents=True)
    (base / "execution" / "uow001").mkdir(parents=True)
    (base / "qa").mkdir(parents=True)

    (base / "intake" / "story.yaml").write_text(yaml.dump({
        "title": "Add user search feature",
        "goal": "Users can search for other users by name or email.",
        "acceptance_criteria": [
            {"id": "AC1", "description": "Search returns results within 500ms"},
            {"id": "AC2", "description": "Empty query shows recent contacts"},
        ],
    }), encoding="utf-8")

    (base / "planning" / "tasks.yaml").write_text(yaml.dump({
        "tasks": [
            {
                "id": "T1",
                "title": "Build search API endpoint",
                "description": "POST /search with name/email filters",
                "depends_on": [],
                "covers_acs": ["AC1"],
            },
            {
                "id": "T2",
                "title": "UI search component",
                "description": "Debounced input, results list",
                "depends_on": ["T1"],
                "covers_acs": ["AC1", "AC2"],
            },
        ]
    }), encoding="utf-8")

    (base / "execution" / "uow001" / "impl_report.yaml").write_text(yaml.dump({
        "status": "completed",
        "summary": "Search endpoint and UI component implemented.",
    }), encoding="utf-8")

    (base / "qa" / "qa_report.yaml").write_text(yaml.dump({
        "overall_result": "pass",
        "summary": "All acceptance criteria verified.",
    }), encoding="utf-8")

    return base


# ---------------------------------------------------------------------------
# pr_web_url
# ---------------------------------------------------------------------------

class TestPrWebUrl:
    def _import(self):
        from core.visual_artifacts import pr_web_url
        return pr_web_url

    def test_builds_from_repository_and_pr_id(self):
        pr_web_url = self._import()
        payload = {
            "pullRequestId": 42,
            "repository": {"webUrl": "https://dev.azure.com/org/proj/_git/repo"},
        }
        assert pr_web_url(payload) == "https://dev.azure.com/org/proj/_git/repo/pullrequest/42"

    def test_falls_back_to_links_web(self):
        pr_web_url = self._import()
        payload = {
            "_links": {"web": {"href": "https://dev.azure.com/org/proj/_git/repo/pullrequest/7"}},
        }
        assert pr_web_url(payload) == "https://dev.azure.com/org/proj/_git/repo/pullrequest/7"

    def test_empty_payload_returns_empty(self):
        pr_web_url = self._import()
        assert pr_web_url({}) == ""

    def test_trailing_slash_normalised(self):
        pr_web_url = self._import()
        payload = {
            "pullRequestId": 9,
            "repository": {"webUrl": "https://dev.azure.com/org/proj/_git/repo/"},
        }
        url = pr_web_url(payload)
        assert url.endswith("/pullrequest/9")
        assert "//" not in url.split("//", 1)[1]  # no double slash after scheme


# ---------------------------------------------------------------------------
# generate_recap_html
# ---------------------------------------------------------------------------

class TestGenerateRecapHtml:
    def test_writes_file_with_pr_url(self, tmp_path):
        change_id = "TEST-001"
        _write_files(tmp_path, change_id)

        with mock.patch("core.runtime_paths.agent_context_root", return_value=tmp_path):
            from core.visual_artifacts import generate_recap_html

            pr_payload = {
                "pullRequestId": 99,
                "title": "Add user search feature",
                "repository": {"webUrl": "https://dev.azure.com/org/proj/_git/myrepo"},
                "sourceRefName": "refs/heads/feature/test",
            }
            with mock.patch("core.visual_artifacts._git_changed_files", return_value=[
                ("A", "src/search.py"),
                ("M", "src/ui.ts"),
            ]):
                out = generate_recap_html(change_id, tmp_path, pr_payload)

        assert out.exists()
        content = out.read_text(encoding="utf-8")

        # Must include a direct PR link
        assert "https://dev.azure.com/org/proj/_git/myrepo/pullrequest/99" in content
        # Must include the PR title
        assert "Add user search feature" in content
        # Must include changed files
        assert "src/search.py" in content
        assert "src/ui.ts" in content
        # Must include impl summary
        assert "Search endpoint" in content
        # Must include QA summary
        assert "acceptance criteria verified" in content

    def test_output_path_is_pr_dir(self, tmp_path):
        change_id = "TEST-002"
        _write_files(tmp_path, change_id)

        with mock.patch("core.runtime_paths.agent_context_root", return_value=tmp_path):
            from core.visual_artifacts import generate_recap_html

            with mock.patch("core.visual_artifacts._git_changed_files", return_value=[]):
                out = generate_recap_html(change_id, tmp_path, {})

        assert out == tmp_path / change_id / "pr" / "recap.html"

    def test_html_is_valid_doctype(self, tmp_path):
        change_id = "TEST-003"
        _write_files(tmp_path, change_id)

        with mock.patch("core.runtime_paths.agent_context_root", return_value=tmp_path):
            from core.visual_artifacts import generate_recap_html

            with mock.patch("core.visual_artifacts._git_changed_files", return_value=[]):
                out = generate_recap_html(change_id, tmp_path, {})

        content = out.read_text(encoding="utf-8")
        assert content.strip().startswith("<!DOCTYPE html>")

    def test_xss_escaped(self, tmp_path):
        """User-controlled values must be HTML-escaped."""
        change_id = "TEST-004"
        _write_files(tmp_path, change_id)

        with mock.patch("core.runtime_paths.agent_context_root", return_value=tmp_path):
            from core.visual_artifacts import generate_recap_html

            pr_payload = {"title": '<script>alert("xss")</script>'}
            with mock.patch("core.visual_artifacts._git_changed_files", return_value=[]):
                out = generate_recap_html(change_id, tmp_path, pr_payload)

        content = out.read_text(encoding="utf-8")
        assert "<script>" not in content
        assert "&lt;script&gt;" in content


# ---------------------------------------------------------------------------
# generate_plan_html
# ---------------------------------------------------------------------------

class TestGeneratePlanHtml:
    def test_writes_file_with_tasks(self, tmp_path):
        change_id = "TEST-010"
        _write_files(tmp_path, change_id)

        with mock.patch("core.runtime_paths.agent_context_root", return_value=tmp_path):
            from core.visual_artifacts import generate_plan_html

            out = generate_plan_html(change_id)

        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "Build search API endpoint" in content
        assert "UI search component" in content
        assert "T1" in content
        assert "T2" in content

    def test_output_path_is_planning_dir(self, tmp_path):
        change_id = "TEST-011"
        _write_files(tmp_path, change_id)

        with mock.patch("core.runtime_paths.agent_context_root", return_value=tmp_path):
            from core.visual_artifacts import generate_plan_html

            out = generate_plan_html(change_id)

        assert out == tmp_path / change_id / "planning" / "plan.html"

    def test_shows_story_goal(self, tmp_path):
        change_id = "TEST-012"
        _write_files(tmp_path, change_id)

        with mock.patch("core.runtime_paths.agent_context_root", return_value=tmp_path):
            from core.visual_artifacts import generate_plan_html

            out = generate_plan_html(change_id)

        content = out.read_text(encoding="utf-8")
        assert "Users can search for other users" in content

    def test_ac_table_present(self, tmp_path):
        change_id = "TEST-013"
        _write_files(tmp_path, change_id)

        with mock.patch("core.runtime_paths.agent_context_root", return_value=tmp_path):
            from core.visual_artifacts import generate_plan_html

            out = generate_plan_html(change_id)

        content = out.read_text(encoding="utf-8")
        assert "AC1" in content
        assert "AC2" in content
        assert "Acceptance Criteria" in content

    def test_approved_badge_present(self, tmp_path):
        change_id = "TEST-014"
        _write_files(tmp_path, change_id)

        with mock.patch("core.runtime_paths.agent_context_root", return_value=tmp_path):
            from core.visual_artifacts import generate_plan_html

            out = generate_plan_html(change_id)

        content = out.read_text(encoding="utf-8")
        assert "APPROVED" in content

    def test_doctype(self, tmp_path):
        change_id = "TEST-015"
        _write_files(tmp_path, change_id)

        with mock.patch("core.runtime_paths.agent_context_root", return_value=tmp_path):
            from core.visual_artifacts import generate_plan_html

            out = generate_plan_html(change_id)

        assert out.read_text(encoding="utf-8").strip().startswith("<!DOCTYPE html>")


# ---------------------------------------------------------------------------
# open_in_browser
# ---------------------------------------------------------------------------

class TestOpenInBrowser:
    def _call(self, path: Path, *, new_tab: bool, env: dict | None = None):
        with mock.patch.dict(os.environ, env or {}, clear=False):
            with mock.patch("core.visual_artifacts.webbrowser.open") as m_open:
                from core.visual_artifacts import open_in_browser
                open_in_browser(path, new_tab=new_tab)
        return m_open

    def test_new_tab_uses_new_2(self, tmp_path):
        p = tmp_path / "plan.html"
        p.write_text("x")
        m = self._call(p, new_tab=True, env={"WORKBENCH_OPEN_BROWSER": "1"})
        m.assert_called_once()
        # webbrowser.open(url, new=2) — new is the second positional arg
        call_args = m.call_args
        new_val = call_args[1].get("new") if call_args[1] else None
        if new_val is None and len(call_args[0]) > 1:
            new_val = call_args[0][1]
        assert new_val == 2

    def test_no_new_tab_uses_new_0(self, tmp_path):
        p = tmp_path / "recap.html"
        p.write_text("x")
        m = self._call(p, new_tab=False, env={"WORKBENCH_OPEN_BROWSER": "1"})
        m.assert_called_once()
        call_args = m.call_args
        new_val = call_args[1].get("new") if call_args[1] else None
        if new_val is None and len(call_args[0]) > 1:
            new_val = call_args[0][1]
        assert new_val == 0

    def test_suppressed_when_zero(self, tmp_path):
        p = tmp_path / "recap.html"
        p.write_text("x")
        m = self._call(p, new_tab=False, env={"WORKBENCH_OPEN_BROWSER": "0"})
        m.assert_not_called()

    def test_suppressed_in_ci(self, tmp_path):
        p = tmp_path / "recap.html"
        p.write_text("x")
        m = self._call(
            p, new_tab=False,
            env={"CI": "true", "WORKBENCH_OPEN_BROWSER": ""},
        )
        m.assert_not_called()

    def test_force_open_overrides_ci(self, tmp_path):
        p = tmp_path / "recap.html"
        p.write_text("x")
        m = self._call(
            p, new_tab=False,
            env={"CI": "true", "WORKBENCH_OPEN_BROWSER": "1"},
        )
        m.assert_called_once()

    def test_browser_exception_does_not_raise(self, tmp_path):
        p = tmp_path / "recap.html"
        p.write_text("x")
        with mock.patch.dict(os.environ, {"WORKBENCH_OPEN_BROWSER": "1"}):
            with mock.patch("core.visual_artifacts.webbrowser.open", side_effect=OSError("no browser")):
                from core.visual_artifacts import open_in_browser
                open_in_browser(p, new_tab=False)  # must not raise

    def test_url_uses_file_scheme(self, tmp_path):
        p = tmp_path / "plan.html"
        p.write_text("x")
        m = self._call(p, new_tab=True, env={"WORKBENCH_OPEN_BROWSER": "1"})
        url_arg = m.call_args[0][0]
        assert url_arg.startswith("file://")


def test_generate_plan_html_can_render_not_approved_badge(tmp_path):
    change_id = "TEST-014"
    _write_files(tmp_path, change_id)
    with mock.patch("core.runtime_paths.agent_context_root", return_value=tmp_path):
        from core.visual_artifacts import generate_plan_html

        out = generate_plan_html(change_id, approved=False)

    content = out.read_text(encoding="utf-8")
    assert ">NOT APPROVED<" in content
    assert ">APPROVED<" not in content
