"""
Evaluation runner for agent-runner.

Single entry point for a complete evaluation run:
  1. Ensures mcs-products-mono-ui is on the frozen testing branch
     (stashes uncommitted changes and switches branches if needed)
  2. Records the baseline SHA
  3. Runs the agent-runner pipeline against the test story
  4. Scores the result with eval/checks.sh
  5. Logs the experiment to Opik
  6. Restores the Angular repo to its original state
     (resets agent changes, switches back, pops stash)

Usage
-----
    python eval/run_eval.py \
        --change-id EVAL-001 \
        --mono-root /path/to/mcs-products-mono-ui

    # Skip Opik logging (offline / no credentials):
    python eval/run_eval.py --change-id EVAL-001 --mono-root /path/to/mono --skip-opik

    # Score an already-modified state without re-running the pipeline:
    python eval/run_eval.py --change-id EVAL-001 --mono-root /path/to/mono --skip-pipeline

    # Run 10 isolated evaluations with at most 3 executing at once:
    python eval/run_eval.py --change-id EVAL-001 --mono-root /path/to/mono --runs 10 --max-concurrent 3
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVAL_DIR.parent

# The frozen branch in mcs-products-mono-ui used as the test baseline.
# Agent changes are made against this branch and discarded after each run.
DEFAULT_TESTING_BRANCH = "agents/frozen-for-testing"

# Add repo root to path so opik_integration is importable
sys.path.insert(0, str(REPO_ROOT))

import opik  # noqa: E402
from opik.evaluation import evaluate  # noqa: E402

from eval.metrics import VerificationCheckMetric  # noqa: E402
from eval.story_checks import run_story_checks  # noqa: E402


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _git(*args: str, cwd: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=check,
    )


def _current_branch(mono_root: str) -> str:
    return _git("branch", "--show-current", cwd=mono_root).stdout.strip()


def _head_sha(mono_root: str) -> str:
    return _git("rev-parse", "HEAD", cwd=mono_root).stdout.strip()


def _has_uncommitted_changes(mono_root: str) -> bool:
    return bool(_git("status", "--porcelain", cwd=mono_root).stdout.strip())


def _branch_exists_locally(mono_root: str, branch: str) -> bool:
    result = _git("branch", "--list", branch, cwd=mono_root, check=False)
    return bool(result.stdout.strip())


def _branch_exists_remote(mono_root: str, branch: str) -> bool:
    result = _git("branch", "-r", "--list", f"origin/{branch}", cwd=mono_root, check=False)
    return bool(result.stdout.strip())


def _ensure_testing_branch_available(mono_root: str, testing_branch: str) -> None:
    if _branch_exists_locally(mono_root, testing_branch):
        return

    if _branch_exists_remote(mono_root, testing_branch):
        print(f"[git] Fetching '{testing_branch}' from origin…")
        _git("fetch", "origin", testing_branch, cwd=mono_root)
        _git("branch", "--track", testing_branch, f"origin/{testing_branch}", cwd=mono_root)
        return

    sys.exit(
        f"[ERROR] Testing branch '{testing_branch}' not found locally or at origin. "
        "Create it with: git checkout -b agents/frozen-for-testing"
    )


# ---------------------------------------------------------------------------
# Branch management
# ---------------------------------------------------------------------------

def ensure_testing_branch(mono_root: str, testing_branch: str) -> tuple[str, bool]:
    """
    Ensure the Angular repo is on testing_branch.

    If the repo is already on testing_branch, this is a no-op.
    Otherwise:
      - Stash uncommitted changes (including untracked files)
      - Switch to testing_branch

    Returns:
        (original_branch, stash_was_created)
    """
    original = _current_branch(mono_root)

    if original == testing_branch:
        print(f"[git] Already on '{testing_branch}' — no branch switch needed.")
        return original, False

    _ensure_testing_branch_available(mono_root, testing_branch)

    stash_created = False
    if _has_uncommitted_changes(mono_root):
        print(f"[git] Stashing uncommitted changes on '{original}'…")
        _git(
            "stash", "push",
            "--include-untracked",
            "-m", "eval-runner-pre-test-stash",
            cwd=mono_root,
        )
        stash_created = True

    print(f"[git] Switching from '{original}' → '{testing_branch}'…")
    _git("checkout", testing_branch, cwd=mono_root)
    return original, stash_created


def restore_repo(
    mono_root: str,
    original_branch: str,
    stash_created: bool,
    testing_branch: str,
) -> None:
    """
    Undo agent changes in the Angular repo and restore the original branch state.

    Steps:
      1. Discard tracked-file changes made by agents (git checkout -- .)
      2. Remove untracked files/dirs created by agents (git clean -fd)
      3. Switch back to original_branch (if we switched)
      4. Pop the stash (if we created one)
    """
    print("\n[git] Restoring mcs-products-mono-ui to pre-evaluation state…")

    _git("checkout", "--", ".", cwd=mono_root, check=False)
    _git("clean", "-fd", cwd=mono_root, check=False)

    if original_branch and original_branch != testing_branch:
        print(f"[git] Switching back to '{original_branch}'…")
        result = _git("checkout", original_branch, cwd=mono_root, check=False)
        if result.returncode != 0:
            print(
                f"[WARN] Could not switch back to '{original_branch}': {result.stderr.strip()}",
                file=sys.stderr,
            )

    if stash_created:
        print("[git] Restoring stashed changes…")
        result = _git("stash", "pop", cwd=mono_root, check=False)
        if result.returncode != 0:
            print(
                "[WARN] 'git stash pop' failed. Your stash is still saved — "
                "run 'git stash pop' manually in the Angular repo.",
                file=sys.stderr,
            )


# ---------------------------------------------------------------------------
# Pipeline and scoring helpers
# ---------------------------------------------------------------------------

def _run_pipeline(
    change_id: str,
    mono_root: str,
    runner: str,
    skip_materialize: bool,
    story_path: Path | None = None,
) -> int:
    """Invoke run.py as a subprocess and return its exit code."""
    resolved_story_path = Path(story_path) if story_path is not None else EVAL_DIR / "stories" / f"{change_id}.json"
    if not resolved_story_path.exists():
        sys.exit(f"[ERROR] Story fixture not found: {resolved_story_path}")

    cmd = [
        sys.executable, str(REPO_ROOT / "run.py"),
        "--story-file", str(resolved_story_path),
        "--repo", mono_root,
        "--runner", runner,
    ]
    if skip_materialize:
        cmd.append("--skip-materialize")

    print(f"\n[pipeline] Running run.py for {change_id} (runner={runner})…")
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT))
    if proc.returncode != 0:
        print(
            f"[WARN] Pipeline exited with code {proc.returncode} — proceeding to score.",
            file=sys.stderr,
        )
    return proc.returncode


def _capture_diff_stat(mono_root: str) -> str:
    """Return a summary of changes relative to HEAD (what the agents modified)."""
    proc = subprocess.run(
        ["git", "diff", "--stat"],
        capture_output=True,
        text=True,
        cwd=mono_root,
    )
    stat = proc.stdout.strip()
    return stat[:5000] if stat else "(no file changes detected)"


def _run_checks(mono_root: str, change_id: str) -> dict:
    """Execute the story-specific verification checks and return JSON-like results."""
    return run_story_checks(mono_root=mono_root, change_id=change_id, timeout=600)


def _now_tag() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be >= 1")
    return parsed


def _experiment_name_for_run(
    explicit_name: str | None,
    change_id: str,
    run_index: int,
    total_runs: int,
) -> str:
    base_name = explicit_name or f"eval-{change_id.lower()}-{_now_tag()}"
    if total_runs == 1:
        return base_name
    return f"{base_name}-run-{run_index:02d}"


def _create_temp_story_fixture(source_story_path: Path, run_index: int) -> tuple[Path, str]:
    temp_dir = Path(tempfile.mkdtemp(prefix=f"eval-story-{run_index:02d}-"))
    story = json.loads(source_story_path.read_text(encoding="utf-8"))
    run_change_id = f"{story['change_id']}-RUN-{run_index:02d}"
    story["change_id"] = run_change_id
    temp_story_path = temp_dir / source_story_path.name
    temp_story_path.write_text(json.dumps(story, indent=2) + "\n", encoding="utf-8")
    return temp_story_path, run_change_id


def _cleanup_temp_story_fixture(story_path: Path | None) -> None:
    if story_path is None:
        return
    shutil.rmtree(story_path.parent, ignore_errors=True)


def _cleanup_agent_context(change_id: str) -> None:
    shutil.rmtree(REPO_ROOT / "agent-context" / change_id, ignore_errors=True)


def _cleanup_stale_run_directories(change_id: str) -> None:
    """Remove any agent-context/{change_id}-RUN-* directories left by prior runs."""
    agent_context = REPO_ROOT / "agent-context"
    if not agent_context.is_dir():
        return
    base = re.sub(r"-RUN-\d+$", "", change_id)
    pattern = re.compile(rf"^{re.escape(base)}-RUN-\d+$")
    for entry in agent_context.iterdir():
        if entry.is_dir() and pattern.match(entry.name):
            print(f"[cleanup] Removing stale multi-run workspace: {entry.name}")
            shutil.rmtree(entry, ignore_errors=True)


def _create_temp_worktree(mono_root: str, testing_branch: str, change_id: str, run_index: int) -> Path:
    _ensure_testing_branch_available(mono_root, testing_branch)
    worktree_parent = Path(tempfile.mkdtemp(prefix=f"eval-{change_id.lower()}-{run_index:02d}-"))
    worktree_path = worktree_parent / "mono"
    print(f"[git] Creating isolated worktree for run {run_index}: {worktree_path}")
    _git("worktree", "add", "--detach", str(worktree_path), testing_branch, cwd=mono_root)
    return worktree_path


def _cleanup_temp_worktree(mono_root: str, worktree_path: Path | None) -> None:
    if worktree_path is None:
        return
    _git("worktree", "remove", "--force", str(worktree_path), cwd=mono_root, check=False)
    _git("worktree", "prune", cwd=mono_root, check=False)
    shutil.rmtree(worktree_path.parent, ignore_errors=True)


def _log_opik_result(
    *,
    change_id: str,
    story: dict,
    baseline_sha: str,
    testing_branch: str,
    diff_stat: str,
    total: int,
    mono_root: str,
    runner: str,
    pipeline_exit_code: int,
    check_results: dict,
    experiment_name: str,
    run_index: int,
    total_runs: int,
) -> None:
    client = opik.Opik()
    dataset = client.get_or_create_dataset(
        f"pipeline-eval-{change_id}",
        description=f"Evaluation dataset for {change_id}: {story['title']}",
    )
    dataset.insert([{
        "input": {
            "change_id": change_id,
            "story_title": story["title"],
            "acceptance_criteria": story["acceptance_criteria"],
            "baseline_sha": baseline_sha,
            "testing_branch": testing_branch,
            "diff_stat": diff_stat,
            "run_index": run_index,
            "total_runs": total_runs,
        },
        "expected_output": {
            "description": f"All {total} verification checks for {change_id} pass → score 100/100",
        },
    }])

    check_json = json.dumps(check_results)

    def task(item: dict) -> dict:
        return {"output": check_json, "context": diff_stat}

    print(f"[opik] Logging experiment '{experiment_name}'…")
    evaluate(
        dataset=dataset,
        task=task,
        scoring_metrics=[VerificationCheckMetric(mono_root=mono_root, change_id=change_id)],
        experiment_name=experiment_name,
        experiment_config={
            "change_id": change_id,
            "story": story["title"],
            "baseline_sha": baseline_sha,
            "testing_branch": testing_branch,
            "runner": runner,
            "pipeline_exit_code": pipeline_exit_code,
            "checks_total": total,
            "run_index": run_index,
            "total_runs": total_runs,
        },
    )
    client.flush()
    print(
        f"[opik] Experiment logged → "
        f"Opik dashboard › agent-runner › Experiments › {experiment_name}"
    )


def _execute_evaluation(
    *,
    change_id: str,
    pipeline_change_id: str,
    story: dict,
    mono_root: str,
    runner: str,
    testing_branch: str,
    experiment_name: str,
    skip_pipeline: bool,
    skip_materialize: bool,
    skip_opik: bool,
    story_path: Path | None,
    run_index: int,
    total_runs: int,
) -> dict:
    baseline_sha = _head_sha(mono_root)
    print(f"[git] Baseline SHA (run {run_index}/{total_runs}): {baseline_sha}")

    pipeline_exit_code = 0
    if not skip_pipeline:
        pipeline_exit_code = _run_pipeline(
            change_id=pipeline_change_id,
            mono_root=mono_root,
            runner=runner,
            skip_materialize=skip_materialize,
            story_path=story_path,
        )

    print(f"\n[eval] Running verification checks for {change_id} (run {run_index}/{total_runs})…")
    check_results = _run_checks(mono_root, change_id)
    diff_stat = _capture_diff_stat(mono_root)

    score = check_results["score"]
    passing = check_results["passing"]
    total = check_results["total"]

    print()
    print("=" * 44)
    print(f"  Pipeline Score (run {run_index}/{total_runs}): {score}/100")
    print("=" * 44)
    print(f"  Checks: {passing}/{total} passed")
    for check in check_results["checks"]:
        status = "PASS" if check["passed"] else "FAIL"
        print(f"    [{status}] {check['id']:02d}. {check['name']}")
    print()

    if not skip_opik:
        _log_opik_result(
            change_id=change_id,
            story=story,
            baseline_sha=baseline_sha,
            testing_branch=testing_branch,
            diff_stat=diff_stat,
            total=total,
            mono_root=mono_root,
            runner=runner,
            pipeline_exit_code=pipeline_exit_code,
            check_results=check_results,
            experiment_name=experiment_name,
            run_index=run_index,
            total_runs=total_runs,
        )

    return {
        "run_index": run_index,
        "baseline_sha": baseline_sha,
        "pipeline_exit_code": pipeline_exit_code,
        "score": score,
        "passing": passing,
        "total": total,
        "check_results": check_results,
    }


def _run_single_evaluation(args: argparse.Namespace, story: dict, story_path: Path) -> dict:
    mono_root = str(Path(args.mono_root).resolve())
    _cleanup_stale_run_directories(args.change_id)
    original_branch, stash_created = ensure_testing_branch(mono_root, args.testing_branch)
    try:
        return _execute_evaluation(
            change_id=args.change_id,
            pipeline_change_id=args.change_id,
            story=story,
            mono_root=mono_root,
            runner=args.runner,
            testing_branch=args.testing_branch,
            experiment_name=_experiment_name_for_run(args.experiment_name, args.change_id, 1, 1),
            skip_pipeline=args.skip_pipeline,
            skip_materialize=args.skip_materialize,
            skip_opik=args.skip_opik,
            story_path=story_path,
            run_index=1,
            total_runs=1,
        )
    finally:
        restore_repo(
            mono_root=mono_root,
            original_branch=original_branch,
            stash_created=stash_created,
            testing_branch=args.testing_branch,
        )


def _run_isolated_evaluation(args: argparse.Namespace, story: dict, story_path: Path, run_index: int) -> dict:
    mono_root = str(Path(args.mono_root).resolve())
    worktree_path = _create_temp_worktree(mono_root, args.testing_branch, args.change_id, run_index)
    temp_story_path: Path | None = None
    pipeline_change_id = args.change_id
    try:
        if not args.skip_pipeline:
            temp_story_path, pipeline_change_id = _create_temp_story_fixture(story_path, run_index)

        return _execute_evaluation(
            change_id=args.change_id,
            pipeline_change_id=pipeline_change_id,
            story=story,
            mono_root=str(worktree_path),
            runner=args.runner,
            testing_branch=args.testing_branch,
            experiment_name=_experiment_name_for_run(args.experiment_name, args.change_id, run_index, args.runs),
            skip_pipeline=args.skip_pipeline,
            skip_materialize=args.skip_materialize,
            skip_opik=args.skip_opik,
            story_path=temp_story_path or story_path,
            run_index=run_index,
            total_runs=args.runs,
        )
    finally:
        if pipeline_change_id != args.change_id:
            _cleanup_agent_context(pipeline_change_id)
        _cleanup_temp_story_fixture(temp_story_path)
        _cleanup_temp_worktree(mono_root, worktree_path)


def run_evaluations(args: argparse.Namespace, story: dict, story_path: Path) -> list[dict]:
    if args.runs == 1:
        return [_run_single_evaluation(args, story, story_path)]

    results: list[dict] = []
    runs = int(args.runs)
    _cleanup_stale_run_directories(args.change_id)
    max_concurrent = int(args.max_concurrent)
    max_workers = min(max_concurrent, runs)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(_run_isolated_evaluation, args, story, story_path, run_index): run_index
            for run_index in range(1, runs + 1)
        }
        for future in as_completed(future_map):
            results.append(future.result())

    results.sort(key=lambda item: item["run_index"])
    print("\n[eval] Multi-run summary")
    for result in results:
        print(
            f"  Run {result['run_index']:02d}: score={result['score']}/100, "
            f"checks={result['passing']}/{result['total']}, "
            f"pipeline_exit={result['pipeline_exit_code']}"
        )
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Full evaluation run: ensure test branch → run pipeline → score → restore repo."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--change-id",
        required=True,
        help="Story change_id to evaluate (e.g. EVAL-001). "
             "A matching eval/stories/{CHANGE-ID}.json must exist.",
    )
    parser.add_argument(
        "--mono-root",
        required=True,
        help="Absolute path to the mcs-products-mono-ui repository.",
    )
    parser.add_argument(
        "--runner",
        default="claude",
        choices=["claude", "copilot"],
        help="Agent runner passed to run.py (default: claude).",
    )
    parser.add_argument(
        "--runs",
        type=_positive_int,
        default=1,
        help="Number of evaluation runs to execute. Values greater than 1 use isolated temp worktrees.",
    )
    parser.add_argument(
        "--max-concurrent",
        type=_positive_int,
        default=1,
        help="Maximum number of isolated runs to execute simultaneously (default: 1).",
    )
    parser.add_argument(
        "--testing-branch",
        default=DEFAULT_TESTING_BRANCH,
        help=f"Frozen testing branch in mcs-products-mono-ui (default: {DEFAULT_TESTING_BRANCH}).",
    )
    parser.add_argument(
        "--experiment-name",
        default=None,
        help="Opik experiment name. Defaults to eval-{change_id}-{timestamp}.",
    )
    parser.add_argument(
        "--skip-pipeline",
        action="store_true",
        help=(
            "Skip the pipeline run and score the current state of the repo. "
            "Useful when re-scoring an already-modified working tree."
        ),
    )
    parser.add_argument(
        "--skip-materialize",
        action="store_true",
        help="Pass --skip-materialize to run.py (skip agent materialization).",
    )
    parser.add_argument(
        "--skip-opik",
        action="store_true",
        help="Print score without logging to Opik (no credentials needed).",
    )
    args = parser.parse_args()

    change_id = args.change_id
    story_path = EVAL_DIR / "stories" / f"{change_id}.json"
    if not story_path.exists():
        sys.exit(f"[ERROR] Story fixture not found: {story_path}")
    story = json.loads(story_path.read_text(encoding="utf-8"))

    run_evaluations(args, story, story_path)


if __name__ == "__main__":
    main()
