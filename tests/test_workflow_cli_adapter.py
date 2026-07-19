"""Deterministic tests for the Prompt 10 workflow CLI adapter (`run.py`).

Covers `run.run_spec_from_args` (the authoritative CLI-args -> `RunSpec`
mapping) and `run.execute_cli_args` (the CLI executable path: args ->
RunSpec -> WorkflowRunner, exactly once).

No real LLM calls, no subprocesses, and no `run.py::main` orchestration
body executes in these tests -- `WorkflowRunner`/its legacy delegate are
patched or replaced with fakes throughout.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import run
from workflow.models import RunSpec


# --------------------------------------------------------------------------
# run_spec_from_args -- the authoritative CLI-args -> RunSpec mapping
# --------------------------------------------------------------------------


def test_run_spec_from_args_maps_every_field():
    args = run.parse_args(
        [
            "--repo",
            "/tmp/repo",
            "--change-id",
            "1234",
            "--story-file",
            "/tmp/story.json",
            "--runner",
            "codex",
            "--model",
            "gpt-5.5",
            "--agent-runner",
            "qa-engineer=claude",
            "--agent-model",
            "qa-engineer=opus",
            "--extra-context",
            "see PR #9",
            "--calibration-fast-mode",
            "--headless",
            "--log-level",
            "info",
        ]
    )

    spec = run.run_spec_from_args(args)

    assert isinstance(spec, RunSpec)
    assert spec.repo_path == Path("/tmp/repo")
    assert spec.change_id == "1234"
    assert spec.ado_url is None
    assert spec.story_path == Path("/tmp/story.json")
    assert spec.manual_story_path is None
    assert spec.runner == "codex"
    assert spec.model == "gpt-5.5"
    assert spec.agent_llm_overrides == {"qa-engineer": {"runner": "claude", "model": "opus"}}
    assert spec.extra_context == "see PR #9"
    assert spec.calibration_fast_mode is True
    assert spec.headless is True
    assert spec.log_level == "info"


def test_run_spec_from_args_preserves_argparse_defaults_when_no_flags_given():
    args = run.parse_args([])
    spec = run.run_spec_from_args(args)

    # No story source at all is legacy-valid (defaults to a synthetic fixture
    # further downstream) -- RunSpec must not invent a requirement here.
    assert spec.repo_path is None
    assert spec.change_id is None
    assert spec.ado_url is None
    assert spec.story_path is None
    assert spec.manual_story_path is None
    assert spec.runner == "claude"
    assert spec.model is None
    assert spec.agent_llm_overrides == {}
    assert spec.extra_context is None
    # argparse default for --skip-lessons-optimizer (store_true) is False;
    # RunSpec's own default is True. The mapping must pass the CLI value
    # through explicitly rather than falling back to RunSpec's default.
    assert spec.skip_lessons_optimizer is False
    # --skip-materialize / --materialize mutually-exclusive group defaults
    # skip_materialize=True.
    assert spec.skip_materialize is True
    assert spec.calibration_fast_mode is False
    assert spec.headless is False
    assert spec.log_level == "warning"


def test_run_spec_from_args_materialize_flag_flips_skip_materialize():
    args = run.parse_args(["--materialize"])
    spec = run.run_spec_from_args(args)
    assert spec.skip_materialize is False


def test_run_spec_from_args_manual_story_path_mapped():
    args = run.parse_args(["--manual-story-file", "/tmp/manual.json"])
    spec = run.run_spec_from_args(args)
    assert spec.manual_story_path == Path("/tmp/manual.json")
    assert spec.story_path is None


def test_run_spec_from_args_raises_on_conflicting_story_sources():
    # RunSpec.__post_init__ enforces mutual exclusivity; run_spec_from_args
    # must not swallow or duplicate that validation.
    args = run.parse_args(
        ["--story-file", "/tmp/a.json", "--manual-story-file", "/tmp/b.json"]
    )
    with pytest.raises(ValueError):
        run.run_spec_from_args(args)


# --------------------------------------------------------------------------
# execute_cli_args -- calls WorkflowRunner exactly once
# --------------------------------------------------------------------------


def test_execute_cli_args_calls_workflow_runner_run_exactly_once():
    args = run.parse_args(["--runner", "claude"])

    with patch("workflow.WorkflowRunner") as MockRunner:
        instance = MockRunner.return_value
        instance.run.return_value = "sentinel-result"

        result = run.execute_cli_args(args)

    MockRunner.assert_called_once_with()
    instance.run.assert_called_once()
    (called_spec,), _ = instance.run.call_args
    assert isinstance(called_spec, RunSpec)
    assert result == "sentinel-result"


def test_execute_cli_args_propagates_workflow_exceptions():
    args = run.parse_args(["--runner", "claude"])

    with patch("workflow.WorkflowRunner") as MockRunner:
        instance = MockRunner.return_value
        instance.run.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            run.execute_cli_args(args)


def test_execute_cli_args_no_recursive_call_to_itself():
    # Guard against a WorkflowRunner -> execute_cli_args -> WorkflowRunner
    # cycle: the legacy delegate must call run.main directly, never
    # execute_cli_args.
    from workflow.runner import _default_legacy_workflow

    import inspect

    source = inspect.getsource(_default_legacy_workflow)
    assert "execute_cli_args" not in source
    assert "legacy_run.main(" in source


# --------------------------------------------------------------------------
# __main__ executable-path behavior: input validation -> exit code 1
# --------------------------------------------------------------------------


def test_executable_conflicting_story_sources_exits_1_without_running_workflow():
    proc = subprocess.run(
        [
            sys.executable,
            str(Path(run.__file__)),
            "--story-file",
            "/tmp/a.json",
            "--manual-story-file",
            "/tmp/b.json",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 1


def test_executable_help_flag_still_works():
    # Sanity: argparse's own exit path (e.g. --help -> SystemExit(0)) must
    # not be disturbed by the adapter rewrite of __main__.
    proc = subprocess.run(
        [sys.executable, str(Path(run.__file__)), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0
    assert "--repo" in proc.stdout


# --------------------------------------------------------------------------
# Unrelated exceptions are NOT remapped to exit 1 by execute_cli_args
# --------------------------------------------------------------------------


def test_unrelated_exception_type_is_not_an_input_validation_error():
    assert not isinstance(RuntimeError("boom"), run.INPUT_VALIDATION_ERRORS)
    assert isinstance(ValueError("boom"), run.INPUT_VALIDATION_ERRORS)
    assert isinstance(FileNotFoundError("boom"), run.INPUT_VALIDATION_ERRORS)


def test_system_exit_is_not_caught_by_input_validation_errors():
    assert not isinstance(SystemExit(143), run.INPUT_VALIDATION_ERRORS)


# --------------------------------------------------------------------------
# Programmatic run.main(...) compatibility
# --------------------------------------------------------------------------


def test_main_fn_compat_attribute_present_and_identical():
    assert run.main.fn is run.main


def test_default_legacy_workflow_calls_run_main_not_execute_cli_args():
    from workflow.runner import _default_legacy_workflow
    from workflow.models import RunSpec, RunContext

    spec = RunSpec(runner="claude")
    context = RunContext.for_spec(spec)

    with patch.object(run, "main") as mock_main:
        mock_main.return_value = "ok"
        result = _default_legacy_workflow(spec, context)

    mock_main.assert_called_once()
    assert result == "ok"
