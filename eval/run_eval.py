"""Steady-state evaluation suite runner."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from workflow_inputs import resolve_workflow_input

if __package__ in {None, ""}:  # pragma: no cover - exercised by direct CLI use.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from eval.metrics import EvalScoreMetric, eval_score_results
    from eval.models import CheckResult, EvalStory, ScoreSummary, SuiteManifest
    from eval.scoring import summarize_scores
    from eval.story_checks import run_story_checks
    from eval.suite_io import load_eval_story, load_suite_manifest, workflow_fixture_from_story
else:
    from .metrics import EvalScoreMetric, eval_score_results
    from .models import CheckResult, EvalStory, ScoreSummary, SuiteManifest
    from .scoring import summarize_scores
    from .story_checks import run_story_checks
    from .suite_io import load_eval_story, load_suite_manifest, workflow_fixture_from_story

DEFAULT_TESTING_BRANCH = "codex/eval-run"
REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_DIR = Path(__file__).resolve().parent / ".baselines"
TRANSIENT_FIXTURE_DIR = Path(__file__).resolve().parent / ".run_fixtures"


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
    parser.add_argument("--runner", choices=["claude", "copilot", "gemini"], default="claude")
    parser.add_argument("--model")
    parser.add_argument("--runs", type=_positive_int, default=1)
    parser.add_argument("--max-concurrent", type=_positive_int, default=1)
    parser.add_argument("--skip-pipeline", action="store_true")
    parser.add_argument("--skip-materialize", action="store_true")
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
    skip_materialize: bool,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
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
    if skip_materialize:
        command.append("--skip-materialize")
    return subprocess.run(command, cwd=str(REPO_ROOT), text=True, capture_output=True, check=False)


def _candidate_artifact_files(change_id: str) -> list[Path]:
    root = REPO_ROOT / "agent-context" / change_id
    if not root.exists():
        return []
    preferred_names = ("impl_report.yaml", "qa_report.yaml", "summary.md", "story.yaml", "constraints.md")
    preferred = [path for name in preferred_names for path in root.rglob(name) if path.is_file()]
    suffixes = {".md", ".yaml", ".yml", ".json", ".txt", ".log"}
    fallback = [path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in suffixes]
    seen: set[Path] = set()
    ordered = []
    for path in [*preferred, *sorted(fallback)]:
        if path not in seen:
            seen.add(path)
            ordered.append(path)
    return ordered


def load_best_artifact_text(change_id: str, process_text: str = "") -> str:
    parts: list[str] = []
    for path in _candidate_artifact_files(change_id):
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if text:
            parts.append(f"--- {path.relative_to(REPO_ROOT)} ---\n{text}")
    if process_text.strip():
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
) -> StoryRun:
    story = load_story(story_path)
    isolate_workflow = not args.skip_pipeline and args.runs > 1
    change_id = _workflow_change_id(story, run_index, isolate=isolate_workflow)
    if story_path.suffix.lower() == ".json" and not isolate_workflow:
        fixture_path = story_path
    elif story_path.suffix.lower() == ".json":
        fixture_path = _write_json_fixture_copy(story_path, run_index, change_id)
    else:
        fixture_path = _write_workflow_fixture(story, run_index, change_id)
    transient_path = fixture_path if fixture_path.parent == TRANSIENT_FIXTURE_DIR else None
    try:
        _validate_fixture(fixture_path, args.repo, change_id)
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        if not args.skip_pipeline:
            completed = _run_workflow(
                fixture_path=fixture_path,
                repo=args.repo,
                runner=args.runner,
                model=args.model,
                skip_materialize=args.skip_materialize,
            )
        process_text = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        artifact_text = load_best_artifact_text(change_id, process_text)
        plugin = _metadata_plugin(story, suite, story_path)
        checks = run_story_checks(
            story.story_id,
            artifact_text,
            plugin=plugin,
            difficulty_overrides=_difficulty_overrides(story, suite),
            suite_story=story,
            repo_path=args.repo,
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


def _opik_evaluate(*args: Any, **kwargs: Any) -> Any:
    from opik.evaluation import evaluate

    return evaluate(*args, **kwargs)


def _create_opik_dataset(suite_tier: str, summary: ScoreSummary) -> tuple[Any, Any]:
    import opik

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
    jobs = [(run_index, story_path) for run_index in range(1, args.runs + 1) for story_path in story_paths]
    if args.max_concurrent > 1 and len(jobs) > 1:
        with ThreadPoolExecutor(max_workers=args.max_concurrent) as executor:
            results = list(
                executor.map(
                    lambda job: _run_story_once(story_path=job[1], suite=suite, run_index=job[0], args=args),
                    jobs,
                )
            )
    else:
        results = [
            _run_story_once(story_path=story_path, suite=suite, run_index=run_index, args=args)
            for run_index, story_path in jobs
        ]
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
