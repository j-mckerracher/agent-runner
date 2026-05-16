"""Steady-state evaluation suite runner."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

if __package__ in {None, ""}:  # pragma: no cover - exercised by direct CLI use.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.workflow_inputs import resolve_workflow_input

if __package__ in {None, ""}:  # pragma: no cover - exercised by direct CLI use.
    from eval.check_helpers import run_check
    from eval.metrics import EvalScoreMetric, eval_score_results
    from eval.models import CheckResult, EvalStory, ScoreSummary, SuiteManifest
    from eval.scoring import summarize_scores
    from eval.story_checks import get_story_checks, run_story_checks
    from eval.suite_io import load_eval_story, load_suite_manifest, workflow_fixture_from_story
else:
    from .check_helpers import run_check
    from .metrics import EvalScoreMetric, eval_score_results
    from .models import CheckResult, EvalStory, ScoreSummary, SuiteManifest
    from .scoring import summarize_scores
    from .story_checks import get_story_checks, run_story_checks
    from .suite_io import load_eval_story, load_suite_manifest, workflow_fixture_from_story

DEFAULT_TESTING_BRANCH = "codex/eval-run"
REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_DIR = Path(__file__).resolve().parent / ".baselines"
TRANSIENT_FIXTURE_DIR = Path(__file__).resolve().parent / ".run_fixtures"
REPO_CHECK_SUBJECT = "repo"
FALLBACK_COPY_IGNORES = frozenset(
    {
        ".DS_Store",
        ".angular",
        ".idea",
        ".next",
        ".pytest_cache",
        ".turbo",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "out",
        "reports",
        "storybook-static",
        "tmp",
    }
)


@dataclass(frozen=True)
class StoryRun:
    story: EvalStory
    story_path: Path
    fixture_path: Path
    change_id: str
    subprocess_returncode: int
    artifact_text: str
    checks: list[CheckResult]
    summary: ScoreSummary


@dataclass(frozen=True)
class EvalRunResult:
    suite_tier: str
    stories: list[StoryRun]
    summary: ScoreSummary
    baseline_path: Path
    baseline_written: bool
    baseline_updated: bool
    regression: float
    regression_breached: bool
    opik_logged: bool
    workflow_failed: bool = False


@dataclass(frozen=True)
class TrialRunSpec:
    run_index: int
    runner: str
    model: str | None = None


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected positive integer, got {value!r}") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError(f"expected positive integer, got {value!r}")
    return parsed


def _non_negative_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected non-negative float, got {value!r}") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError(f"expected non-negative float, got {value!r}")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an eval suite or story against a repository.")
    target = parser.add_mutually_exclusive_group(required=False)
    target.add_argument("--suite", help="Path to an eval suite directory or suite manifest")
    target.add_argument("--story", help="Path to a single eval story manifest or workflow JSON fixture")
    parser.add_argument("--change-id", help="Compatibility lookup for eval/stories/<id>.json or suite story YAML")
    parser.add_argument("--repo", "--mono-root", dest="repo", required=True, help="Target repository path")
    parser.add_argument("--runner", default="claude", metavar="RUNNER",
                        help="Runner: 'claude', 'copilot', 'gemini', or a copilot alias like 'copilot-gemma4'")
    parser.add_argument("--model")
    parser.add_argument("--runs", type=_positive_int, default=1)
    parser.add_argument("--max-concurrent", type=_positive_int, default=1)
    parser.add_argument("--skip-pipeline", action="store_true")
    
    parser.add_argument("--skip-opik", action="store_true")
    parser.add_argument("--regression-threshold", type=_non_negative_float, default=0.0)
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--ci", action="store_true")
    return parser


def discover_story_paths(
    *,
    suite: str | None = None,
    story: str | None = None,
    change_id: str | None = None,
) -> tuple[list[Path], SuiteManifest | None, str]:
    if story:
        path = Path(story)
        return [path], None, "single"

    if suite:
        suite_path = Path(suite)
        manifest_path = suite_path / "suite_manifest.yaml" if suite_path.is_dir() else suite_path
        manifest = load_suite_manifest(manifest_path)
        base = manifest_path.parent
        story_paths = [base / item for item in manifest.stories]
        if change_id:
            story_paths = [path for path in story_paths if path.stem == change_id or path.name == change_id]
        return story_paths, manifest, manifest.suite_tier

    if change_id:
        json_path = REPO_ROOT / "eval" / "stories" / f"{change_id}.json"
        if json_path.exists():
            return [json_path], None, "single"
        matches = sorted((REPO_ROOT / "eval" / "suites").glob(f"**/{change_id}.yaml"))
        if not matches:
            matches = sorted((REPO_ROOT / "eval" / "suites").glob(f"**/*{change_id}*.yaml"))
        if matches:
            return [matches[0]], None, "single"

    raise ValueError("Provide --suite, --story, or --change-id")


def _story_from_workflow_json(path: Path) -> EvalStory:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    try:
        return EvalStory.from_dict(payload)
    except Exception:
        suite_story = _story_from_raw_metadata_suite_yaml(payload, path)
        if suite_story is not None:
            return suite_story
        return EvalStory(
            story_id=payload.get("metadata", {}).get("eval_story_id") or payload.get("change_id") or path.stem,
            change_id=payload.get("change_id") or path.stem,
            title=payload.get("title", path.stem),
            description=payload.get("description", ""),
            suite_tier=payload.get("metadata", {}).get("suite_tier"),
            dataset_id=payload.get("metadata", {}).get("dataset_id"),
            metadata=payload.get("metadata", {}),
        )


def _story_from_raw_metadata_suite_yaml(payload: dict[str, Any], json_path: Path) -> EvalStory | None:
    raw_metadata = payload.get("raw_metadata")
    if not isinstance(raw_metadata, dict):
        return None
    suite_yaml = raw_metadata.get("suite_yaml")
    if not isinstance(suite_yaml, str) or not suite_yaml.strip():
        return None
    suite_yaml_path = _resolve_suite_yaml_reference(suite_yaml, json_path)
    if suite_yaml_path is None:
        return None
    return load_eval_story(suite_yaml_path)


def _resolve_suite_yaml_reference(reference: str, json_path: Path) -> Path | None:
    ref_path = Path(reference)
    allowed_roots = [REPO_ROOT.resolve(), json_path.parent.resolve()]
    candidates: list[Path]
    if ref_path.is_absolute():
        candidates = [ref_path]
    else:
        candidates = [json_path.parent / ref_path, REPO_ROOT / ref_path]

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if not any(_is_relative_to(resolved, root) for root in allowed_roots):
            continue
        if resolved.suffix.lower() in {".yaml", ".yml"} and resolved.is_file():
            return resolved
    return None


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def load_story(path: Path) -> EvalStory:
    if path.suffix.lower() == ".json":
        return _story_from_workflow_json(path)
    return load_eval_story(path)


def _safe_slug(value: str, *, limit: int = 48) -> str:
    slug = "".join(char if char.isalnum() else "-" for char in value).strip("-")
    slug = slug[:limit].strip("-")
    return slug or "eval-run"


def _build_preflight_failure(
    *,
    story: EvalStory,
    story_path: Path,
    fixture_path: Path,
    change_id: str,
    checks: Sequence[Any],
    message: str,
) -> StoryRun:
    synthetic_results = [
        CheckResult(
            check_id=check.id,
            passed=False,
            attempted=False,
            mechanism=check.mechanism,
            subject=check.subject,
            difficulty=check.difficulty,
            failure_reason="NO_ATTEMPT",
            message=message,
            metadata={"label": check.label, **dict(check.metadata)},
        )
        for check in checks
    ]
    return StoryRun(
        story=story,
        story_path=story_path,
        fixture_path=fixture_path,
        change_id=change_id,
        subprocess_returncode=2,
        artifact_text=message,
        checks=synthetic_results,
        summary=summarize_scores(synthetic_results),
    )


def _repo_grounded_command_checks(
    *,
    story: EvalStory,
    plugin: object | None,
    difficulty_overrides: dict[str, Any],
) -> tuple[list[Any], list[Any]]:
    all_checks = list(
        get_story_checks(
            story.story_id,
            plugin=plugin,
            difficulty_overrides=difficulty_overrides,
            suite_story=story,
        )
    )
    repo_checks = [
        check
        for check in all_checks
        if check.mechanism == "command" and check.subject == REPO_CHECK_SUBJECT
    ]
    return all_checks, repo_checks


def _calibration_actionability_error(
    *,
    story: EvalStory,
    plugin: object | None,
    difficulty_overrides: dict[str, Any],
    repo_path: str,
) -> str | None:
    all_checks, repo_checks = _repo_grounded_command_checks(
        story=story,
        plugin=plugin,
        difficulty_overrides=difficulty_overrides,
    )
    if not all_checks:
        return (
            "Multi-run pipeline evaluation requires at least one acceptance check. "
            f"Story {story.story_id} defined none."
        )
    if len(repo_checks) != len(all_checks):
        return (
            "Multi-run pipeline evaluation requires every acceptance criterion to be a repo-grounded "
            "command check (check_mechanism=command, check_subject=repo) so the runner can detect "
            f"stale or already-satisfied stories before calibration. Story {story.story_id} exposed "
            f"{len(repo_checks)}/{len(all_checks)} repo-grounded command checks."
        )
    preflight_results = [run_check(check, repo_path=repo_path) for check in repo_checks]
    if preflight_results and all(result.passed for result in preflight_results):
        return (
            "Calibration preflight rejected the story because every repo-grounded acceptance check "
            f"already passes against the starting repository for {story.story_id}. "
            "Regenerate the story with checks that fail on the pristine repo and turn green only "
            "after the requested change."
        )
    return None


def _copy_repo_for_run(repo: str, *, change_id: str) -> tuple[str, Path]:
    source_repo = Path(repo).expanduser().resolve()
    try:
        temp_root = Path(
            tempfile.mkdtemp(
                prefix=f"{_safe_slug(change_id)}-repo-",
                dir=str(source_repo.parent),
            )
        )
    except OSError:
        temp_root = Path(tempfile.mkdtemp(prefix=f"{_safe_slug(change_id)}-repo-"))
    destination = temp_root / source_repo.name
    if _clone_repo_tree(source_repo, destination):
        return str(destination), temp_root
    shutil.copytree(
        source_repo,
        destination,
        symlinks=True,
        ignore=shutil.ignore_patterns(*sorted(FALLBACK_COPY_IGNORES)),
    )
    return str(destination), temp_root


def _clone_repo_tree(source_repo: Path, destination: Path) -> bool:
    clone_commands: list[list[str]] = []
    if sys.platform == "darwin":
        clone_commands.append(["cp", "-cR", str(source_repo), str(destination)])
    else:
        clone_commands.append(["cp", "--reflink=auto", "-a", str(source_repo), str(destination)])

    for command in clone_commands:
        try:
            completed = subprocess.run(command, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            continue
        if completed.returncode == 0:
            return True
        if destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
    return False


def _workflow_change_id(story: EvalStory, run_index: int, *, isolate: bool) -> str:
    base_change_id = story.change_id or story.story_id
    if isolate:
        return f"{base_change_id}-RUN-{run_index:02d}"
    return base_change_id


def _write_workflow_fixture(story: EvalStory, run_index: int, change_id: str) -> Path:
    TRANSIENT_FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    fixture_path = TRANSIENT_FIXTURE_DIR / f"{change_id}_run{run_index}.json"
    fixture = workflow_fixture_from_story(story)
    fixture["change_id"] = change_id
    with fixture_path.open("w", encoding="utf-8") as handle:
        json.dump(fixture, handle, indent=2, sort_keys=True)
    return fixture_path


def _write_json_fixture_copy(source_path: Path, run_index: int, change_id: str) -> Path:
    TRANSIENT_FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    fixture_path = TRANSIENT_FIXTURE_DIR / f"{change_id}_run{run_index}.json"
    with source_path.open("r", encoding="utf-8") as handle:
        fixture = json.load(handle)
    fixture["change_id"] = change_id
    with fixture_path.open("w", encoding="utf-8") as handle:
        json.dump(fixture, handle, indent=2, sort_keys=True)
    return fixture_path


def _validate_fixture(fixture_path: Path, repo: str, change_id: str) -> None:
    resolve_workflow_input(repo=repo, change_id=change_id, story_file=str(fixture_path))


def _run_workflow(
    *,
    fixture_path: Path,
    repo: str,
    runner: str,
    model: str | None,
    skip_lessons_optimizer: bool = False,
    calibration_fast_mode: bool = False,
    stream_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    venv_python = REPO_ROOT / "venv" / "bin" / "python3"
    python_exe = str(venv_python) if venv_python.is_file() else sys.executable
    command = [
        python_exe,
        str(REPO_ROOT / "run.py"),
        "--story-file",
        str(fixture_path),
        "--repo",
        repo,
        "--runner",
        runner,
    ]
    if model:
        command.extend(["--model", model])
    if skip_lessons_optimizer:
        command.append("--skip-lessons-optimizer")
    if calibration_fast_mode:
        command.append("--calibration-fast-mode")
    if stream_output:
        return _run_subprocess_live(command, cwd=str(REPO_ROOT))
    return subprocess.run(command, cwd=str(REPO_ROOT), text=True, capture_output=True, check=False)


def _run_subprocess_live(command: list[str], *, cwd: str) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    def _pump(pipe, chunks: list[str], target) -> None:
        if pipe is None:
            return
        try:
            for line in iter(pipe.readline, ""):
                chunks.append(line)
                print(line, end="", file=target, flush=True)
        finally:
            pipe.close()

    stdout_thread = threading.Thread(target=_pump, args=(process.stdout, stdout_chunks, sys.stdout))
    stderr_thread = threading.Thread(target=_pump, args=(process.stderr, stderr_chunks, sys.stderr))
    stdout_thread.start()
    stderr_thread.start()
    returncode = process.wait()
    stdout_thread.join()
    stderr_thread.join()
    return subprocess.CompletedProcess(
        args=command,
        returncode=returncode,
        stdout="".join(stdout_chunks),
        stderr="".join(stderr_chunks),
    )


def _candidate_artifact_files(change_id: str) -> list[Path]:
    root = REPO_ROOT / "agent-context" / change_id
    if not root.exists():
        return []
    preferred_names = ("impl_report.yaml", "qa_report.yaml", "summary.md", "story.yaml", "constraints.md")
    preferred = [path for name in preferred_names for path in root.rglob(name) if path.is_file()]
    suffixes = {".md", ".yaml", ".yml", ".json", ".txt", ".log"}
    fallback = [
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in suffixes and path.name != "workflow_status.yaml"
    ]
    seen: set[Path] = set()
    ordered = []
    for path in [*preferred, *sorted(fallback)]:
        if path not in seen:
            seen.add(path)
            ordered.append(path)
    return ordered


def load_best_artifact_text(
    change_id: str,
    process_text: str = "",
    *,
    workflow_succeeded: bool = True,
) -> str:
    parts: list[str] = []
    for path in _candidate_artifact_files(change_id):
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if text:
            parts.append(f"--- {path.relative_to(REPO_ROOT)} ---\n{text}")
    if process_text.strip() and (workflow_succeeded or parts):
        parts.append(f"--- workflow_output ---\n{process_text.strip()}")
    return "\n\n".join(parts)


def _metadata_plugin(story: EvalStory, suite: SuiteManifest | None, story_path: Path) -> object | None:
    plugin = None
    if suite is not None:
        plugin = suite.metadata.get("plugin") or suite.metadata.get("plugin_path")
    plugin = story.metadata.get("plugin") or story.metadata.get("plugin_path") or plugin
    if isinstance(plugin, str) and not Path(plugin).is_absolute():
        return story_path.parent / plugin
    return plugin


def _difficulty_overrides(story: EvalStory, suite: SuiteManifest | None) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    if suite is not None:
        overrides.update(dict(suite.metadata.get("difficulty_overrides", {})))
    overrides.update(dict(story.metadata.get("difficulty_overrides", {})))
    return overrides


def _baseline_payload(summary: ScoreSummary, suite_tier: str) -> dict[str, Any]:
    return {
        "suite_tier": suite_tier,
        "summary": summary.to_dict(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _load_baseline(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_baseline(path: Path, summary: ScoreSummary, suite_tier: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(_baseline_payload(summary, suite_tier), handle, indent=2, sort_keys=True)


def _compare_baseline(
    *,
    path: Path,
    summary: ScoreSummary,
    suite_tier: str,
    threshold: float,
    update: bool,
) -> tuple[bool, bool, float, bool]:
    baseline = _load_baseline(path)
    if baseline is None:
        _write_baseline(path, summary, suite_tier)
        return True, False, 0.0, False

    old_score = float(baseline.get("summary", {}).get("weighted_composite", 0.0))
    regression = max(0.0, old_score - summary.weighted_composite)
    breached = regression > threshold
    if update:
        _write_baseline(path, summary, suite_tier)
        return False, True, regression, breached
    return False, False, regression, breached


def _story_suite_tier(story: EvalStory) -> str | None:
    if story.suite_tier:
        return story.suite_tier
    metadata_tier = story.metadata.get("suite_tier")
    if isinstance(metadata_tier, str) and metadata_tier:
        return metadata_tier
    return None


def _run_story_once(
    *,
    story_path: Path,
    suite: SuiteManifest | None,
    run_index: int,
    args: argparse.Namespace,
    run_spec: TrialRunSpec | None = None,
) -> StoryRun:
    story = load_story(story_path)
    isolate_workflow = not args.skip_pipeline and args.runs > 1
    change_id = _workflow_change_id(story, run_index, isolate=isolate_workflow)
    effective_runner = run_spec.runner if run_spec is not None else args.runner
    effective_model = run_spec.model if run_spec is not None else args.model
    # Stories with structured acceptance_criteria (AcceptanceCriterion objects) must be
    # converted to plain-string fixture format before being passed to run.py.
    needs_fixture_conversion = bool(story.acceptance_criteria)
    if story_path.suffix.lower() == ".json" and not isolate_workflow and not needs_fixture_conversion:
        fixture_path = story_path
    elif story_path.suffix.lower() == ".json" and not needs_fixture_conversion:
        fixture_path = _write_json_fixture_copy(story_path, run_index, change_id)
    else:
        fixture_path = _write_workflow_fixture(story, run_index, change_id)
    transient_path = fixture_path if fixture_path.parent == TRANSIENT_FIXTURE_DIR else None
    transient_repo_root: Path | None = None
    plugin = _metadata_plugin(story, suite, story_path)
    difficulty_overrides = _difficulty_overrides(story, suite)
    try:
        if getattr(args, "enforce_repo_actionability", False):
            all_checks, _ = _repo_grounded_command_checks(
                story=story,
                plugin=plugin,
                difficulty_overrides=difficulty_overrides,
            )
            actionability_error = _calibration_actionability_error(
                story=story,
                plugin=plugin,
                difficulty_overrides=difficulty_overrides,
                repo_path=args.repo,
            )
            if actionability_error is not None:
                return _build_preflight_failure(
                    story=story,
                    story_path=story_path,
                    fixture_path=fixture_path,
                    change_id=change_id,
                    checks=all_checks,
                    message=actionability_error,
                )

        repo_path = args.repo
        if getattr(args, "isolate_repo", False):
            repo_path, transient_repo_root = _copy_repo_for_run(repo_path, change_id=change_id)

        _validate_fixture(fixture_path, repo_path, change_id)
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        if not args.skip_pipeline:
            completed = _run_workflow(
                fixture_path=fixture_path,
                repo=repo_path,
                runner=effective_runner,
                model=effective_model,
                skip_lessons_optimizer=True,
                calibration_fast_mode=getattr(args, "calibration_fast_mode", False),
                stream_output=getattr(args, "stream_output", False),
            )
        process_text = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        if completed.returncode != 0 and not getattr(args, "stream_output", False):
            print(f"\n[FAIL] Workflow failed for {change_id} (exit {completed.returncode}):",
                  file=sys.stderr)
            if completed.stderr:
                print(completed.stderr.strip()[-2000:], file=sys.stderr)
            elif completed.stdout:
                print(completed.stdout.strip()[-2000:], file=sys.stderr)
        artifact_text = load_best_artifact_text(
            change_id,
            process_text,
            workflow_succeeded=completed.returncode == 0,
        )
        checks = run_story_checks(
            story.story_id,
            artifact_text,
            plugin=plugin,
            difficulty_overrides=difficulty_overrides,
            suite_story=story,
            repo_path=repo_path,
        )
        summary = summarize_scores(checks)
        return StoryRun(
            story=story,
            story_path=story_path,
            fixture_path=fixture_path,
            change_id=change_id,
            subprocess_returncode=completed.returncode,
            artifact_text=artifact_text,
            checks=checks,
            summary=summary,
        )
    finally:
        if transient_path is not None:
            try:
                transient_path.unlink()
            except FileNotFoundError:
                pass
        if transient_repo_root is not None:
            shutil.rmtree(transient_repo_root, ignore_errors=True)


def run_story_trials(
    *,
    story_path: Path,
    repo: str,
    runner: str,
    model: str | None = None,
    runs: int = 1,
    max_concurrent: int = 1,
    run_indices: Sequence[int] | None = None,
    run_specs: Sequence[TrialRunSpec] | None = None,
    skip_pipeline: bool = False,
    calibration_fast_mode: bool = False,
    stream_output: bool = False,
    suite: SuiteManifest | None = None,
) -> list[StoryRun]:
    return _run_story_trials_for_paths(
        story_paths=[story_path],
        repo=repo,
        runner=runner,
        model=model,
        runs=runs,
        max_concurrent=max_concurrent,
        run_indices=run_indices,
        run_specs=run_specs,
        skip_pipeline=skip_pipeline,
        calibration_fast_mode=calibration_fast_mode,
        stream_output=stream_output,
        suite=suite,
    )


def _run_story_trials_for_paths(
    *,
    story_paths: Sequence[Path],
    repo: str,
    runner: str,
    model: str | None = None,
    runs: int = 1,
    max_concurrent: int = 1,
    run_indices: Sequence[int] | None = None,
    run_specs: Sequence[TrialRunSpec] | None = None,
    skip_pipeline: bool = False,
    calibration_fast_mode: bool = False,
    stream_output: bool = False,
    suite: SuiteManifest | None = None,
) -> list[StoryRun]:
    if run_specs is not None:
        selected_run_specs = list(run_specs)
        seen_run_indices: set[int] = set()
        for run_spec in selected_run_specs:
            if run_spec.run_index in seen_run_indices:
                raise ValueError(f"Duplicate run_index in run_specs: {run_spec.run_index}")
            seen_run_indices.add(run_spec.run_index)
    else:
        selected_run_indices = list(run_indices) if run_indices is not None else list(range(1, runs + 1))
        selected_run_specs = [
            TrialRunSpec(run_index=run_index, runner=runner, model=model)
            for run_index in selected_run_indices
        ]
    planned_run_count = max(
        max((run_spec.run_index for run_spec in selected_run_specs), default=0),
        runs,
        1,
    )
    jobs = [(run_spec, story_path) for run_spec in selected_run_specs for story_path in story_paths]
    planned_jobs_count = len(story_paths) * planned_run_count
    args = argparse.Namespace(
        repo=repo,
        runner=runner,
        model=model,
        runs=planned_run_count,
        max_concurrent=max_concurrent,
        skip_pipeline=skip_pipeline,
        calibration_fast_mode=calibration_fast_mode,
        stream_output=stream_output,
        isolate_repo=not skip_pipeline and planned_jobs_count > 1,
        enforce_repo_actionability=not skip_pipeline and planned_run_count > 1,
    )
    if max_concurrent > 1 and len(jobs) > 1:
        with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            return list(
                executor.map(
                    lambda job: _run_story_once(
                        story_path=job[1],
                        suite=suite,
                        run_index=job[0].run_index,
                        args=args,
                        run_spec=job[0],
                    ),
                    jobs,
                )
            )
    return [
        _run_story_once(
            story_path=path,
            suite=suite,
            run_index=run_spec.run_index,
            args=args,
            run_spec=run_spec,
        )
        for run_spec, path in jobs
    ]


def _opik_evaluate(*args: Any, **kwargs: Any) -> Any:
    from opik.evaluation import evaluate

    return evaluate(*args, **kwargs)


def _create_opik_dataset(suite_tier: str, summary: ScoreSummary) -> tuple[Any, Any]:
    import opik

    try:
        from server.config import load_config
        from core.opik_tracing import configure_opik_client

        client = configure_opik_client((load_config().get("opik") or {}))
    except ImportError:
        client = opik.Opik()
    dataset = client.get_or_create_dataset(
        f"agent-workbench-{suite_tier}-suite",
        description=f"Agent Workbench {suite_tier} suite evaluation results",
    )
    dataset.insert(
        [
            {
                "input": {"suite_tier": suite_tier},
                "expected_output": {
                    "score_weighted_composite": summary.weighted_composite,
                    "score_attempted_rate": summary.attempted_rate,
                },
            }
        ]
    )
    return client, dataset


def _log_opik(
    *,
    skip_opik: bool,
    suite_tier: str,
    checks: Sequence[CheckResult],
    summary: ScoreSummary,
    regression: bool,
) -> bool:
    if skip_opik:
        return False
    score_results = eval_score_results(checks, summary, suite_tier=suite_tier, regression=regression)
    experiment_name = f"{suite_tier}_suite_{datetime.now(timezone.utc).isoformat()}"
    metric = EvalScoreMetric(score_results)
    client, dataset = _create_opik_dataset(suite_tier, summary)

    def task(item: dict[str, Any]) -> dict[str, Any]:
        return {"suite_tier": item.get("suite_tier", suite_tier)}

    _opik_evaluate(
        dataset=dataset,
        task=task,
        experiment_name=experiment_name,
        scoring_metrics=[metric],
    )
    flush = getattr(client, "flush", None)
    if callable(flush):
        flush()
    return True


def run_eval(args: argparse.Namespace) -> EvalRunResult:
    story_paths, suite, suite_tier = discover_story_paths(
        suite=args.suite,
        story=args.story,
        change_id=args.change_id,
    )
    if not story_paths:
        raise ValueError("No stories discovered for evaluation")

    story_runs: list[StoryRun] = []
    all_checks: list[CheckResult] = []
    results = _run_story_trials_for_paths(
        story_paths=story_paths,
        repo=args.repo,
        runner=args.runner,
        model=args.model,
        runs=args.runs,
        max_concurrent=args.max_concurrent,
        skip_pipeline=args.skip_pipeline,
        stream_output=False,
        suite=suite,
    )
    for story_run in results:
        story_runs.append(story_run)
        all_checks.extend(story_run.checks)

    if suite is None and story_runs:
        suite_tier = _story_suite_tier(story_runs[0].story) or suite_tier
    aggregate = summarize_scores(all_checks)
    baseline_path = BASELINE_DIR / f"{suite_tier}.json"
    workflow_failed = any(story_run.subprocess_returncode != 0 for story_run in story_runs)
    if workflow_failed:
        baseline_written, baseline_updated, regression, breached = False, False, 0.0, False
    else:
        baseline_written, baseline_updated, regression, breached = _compare_baseline(
            path=baseline_path,
            summary=aggregate,
            suite_tier=suite_tier,
            threshold=args.regression_threshold,
            update=args.update_baseline,
        )
    opik_logged = _log_opik(
        skip_opik=args.skip_opik,
        suite_tier=suite_tier,
        checks=all_checks,
        summary=aggregate,
        regression=breached,
    )
    return EvalRunResult(
        suite_tier=suite_tier,
        stories=story_runs,
        summary=aggregate,
        baseline_path=baseline_path,
        baseline_written=baseline_written,
        baseline_updated=baseline_updated,
        regression=regression,
        regression_breached=breached,
        opik_logged=opik_logged,
        workflow_failed=workflow_failed,
    )


def _print_result(result: EvalRunResult) -> None:
    print(
        json.dumps(
            {
                "suite_tier": result.suite_tier,
                "stories": len(result.stories),
                "summary": result.summary.to_dict(),
                "baseline_path": str(result.baseline_path),
                "baseline_written": result.baseline_written,
                "baseline_updated": result.baseline_updated,
                "regression": result.regression,
                "regression_breached": result.regression_breached,
                "opik_logged": result.opik_logged,
                "workflow_failed": result.workflow_failed,
            },
            indent=2,
            sort_keys=True,
        )
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_eval(args)
    except Exception as exc:
        print(f"eval failed: {exc}", file=sys.stderr)
        return 1
    _print_result(result)
    if result.workflow_failed or result.regression_breached:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
