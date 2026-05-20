#!/usr/bin/env python3
"""Run simple hidden-test workflow benchmarks.

Benchmark layout:

    eval/benchmarks/<name>/story.json
    eval/benchmarks/<name>/hidden_tests.py

For each benchmark, this runner clones the configured target repo, checks out a
fixed gold-master SHA, runs the normal Agent Workbench workflow in headless mode,
and finally runs the hidden pytest file against the modified sandbox.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.runner_models import RUNNER_MODEL_CHOICES, is_copilot_runner

DEFAULT_BENCHMARKS = ROOT / "eval" / "benchmarks"
DEFAULT_REPORTS = ROOT / "eval" / "reports"
RUN_PY = ROOT / "run.py"
WORKFLOW_POLL_SECONDS = 0.25
WORKFLOW_HEARTBEAT_SECONDS = 30.0
WORKFLOW_STAGE_ORDER = (
    "materialize",
    "intake",
    "task-generation",
    "task-assignment",
    "execution",
    "qa",
    "lessons-optimizer",
)


@dataclass
class WorkflowProgress:
    stages: list[str]
    completed_stages: set[str] = field(default_factory=set)
    completed_uows: set[str] = field(default_factory=set)
    current_stage: str | None = None
    total_uows: int | None = None
    last_seq: int = 0
    last_rendered: str | None = None
    last_render_time: float = 0.0

    def percent(self) -> int:
        total_stages = len(self.stages) or 1
        completed = float(len(self.completed_stages))
        if self.current_stage == "execution" and self.total_uows:
            completed += len(self.completed_uows) / self.total_uows
        return max(0, min(100, int((completed / total_stages) * 100)))

    def render(self, *, heartbeat: bool = False, done: bool = False) -> str:
        pct = 100 if done else self.percent()
        if done:
            return "Workflow progress: 100% — workflow complete"
        if self.current_stage == "execution" and self.total_uows:
            return (
                f"Workflow progress: {pct}% — execution "
                f"({len(self.completed_uows)}/{self.total_uows} UoWs complete)"
                + (" — still running" if heartbeat else "")
            )
        if self.current_stage:
            stage_index = self.stages.index(self.current_stage) + 1 if self.current_stage in self.stages else "?"
            return (
                f"Workflow progress: {pct}% — {self.current_stage} "
                f"(stage {stage_index}/{len(self.stages)})"
                + (" — still running" if heartbeat else "")
            )
        completed = len(self.completed_stages)
        return (
            f"Workflow progress: {pct}% — {completed}/{len(self.stages)} stages complete"
            + (" — still running" if heartbeat else "")
        )


def workflow_stage_names(include_lessons: bool) -> list[str]:
    stages = list(WORKFLOW_STAGE_ORDER)
    if not include_lessons:
        stages.remove("lessons-optimizer")
    return stages


def apply_workflow_event(progress: WorkflowProgress, event: dict[str, Any]) -> bool:
    event_type = event.get("type")
    changed = False
    if event_type == "stage.start":
        stage = event.get("stage")
        if stage in progress.stages:
            progress.current_stage = stage
            changed = True
    elif event_type == "stage.end":
        stage = event.get("stage")
        if stage in progress.stages and event.get("status") == "ok":
            progress.completed_stages.add(stage)
            changed = True
        if progress.current_stage == stage:
            progress.current_stage = None
            changed = True
    elif event_type == "workflow.plan":
        total_uows = event.get("total_uows")
        if isinstance(total_uows, int) and total_uows >= 0 and total_uows != progress.total_uows:
            progress.total_uows = total_uows
            changed = True
    elif event_type == "uow.end" and event.get("status") == "ok":
        uow_id = event.get("uow_id")
        if isinstance(uow_id, str) and uow_id not in progress.completed_uows:
            progress.completed_uows.add(uow_id)
            changed = True
    elif event_type == "job.end":
        changed = True
    seq = event.get("seq")
    if isinstance(seq, int):
        progress.last_seq = max(progress.last_seq, seq)
    return changed


def _emit_progress(progress: WorkflowProgress, *, heartbeat: bool = False, done: bool = False) -> None:
    line = progress.render(heartbeat=heartbeat, done=done)
    if done or line != progress.last_rendered:
        print(line, flush=True)
        progress.last_rendered = line
        progress.last_render_time = time.monotonic()


def _poll_workflow_events(event_log_path: Path, progress: WorkflowProgress) -> bool:
    from server.events import read_all

    changed = False
    for event in read_all(event_log_path):
        seq = event.get("seq")
        if isinstance(seq, int) and seq <= progress.last_seq:
            continue
        changed = apply_workflow_event(progress, event) or changed
    return changed


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def default(name: str, env_file: dict[str, str], fallback: str | None = None) -> str | None:
    return os.environ.get(name) or env_file.get(name) or fallback


def builtin_model_choices_for_runner(runner: str) -> tuple[str, ...] | None:
    if runner in RUNNER_MODEL_CHOICES:
        return RUNNER_MODEL_CHOICES[runner]
    if is_copilot_runner(runner):
        return RUNNER_MODEL_CHOICES["copilot"]
    return None


def resolve_model_override(
    runner: str,
    explicit_model: str | None,
    env_file: dict[str, str],
) -> str | None:
    if explicit_model is not None:
        return explicit_model

    inherited_model = default("EVAL_MODEL", env_file)
    if not inherited_model:
        return None

    allowed = builtin_model_choices_for_runner(runner)
    if allowed is None or inherited_model in allowed:
        return inherited_model
    return None


def run_cmd(
    cmd: list[str] | str,
    *,
    cwd: Path,
    timeout: int | None = None,
    env: dict[str, str] | None = None,
    shell: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        shell=shell,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def tail(text: str | None, limit: int = 3000) -> str:
    text = text or ""
    return text if len(text) <= limit else "..." + text[-limit:]


def workflow_env(event_log_path: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["AGENT_RUNNER_HEADLESS"] = "1"
    env.setdefault("AGENT_RUNNER_USER_ESCALATION", "auto")
    env.setdefault("PYTHONUNBUFFERED", "1")
    if event_log_path is not None:
        env["AGENT_RUNNER_EVENT_LOG"] = str(event_log_path)
    return env


def _make_run_id(benchmark_name: str) -> str:
    """Generate a unique run ID scoped to this eval run.

    Format: ``<benchmark>-YYYYMMDD-HHMMSSffffff`` — keeps the benchmark name
    at the front so ``agent-context/`` stays intuitive while the timestamp
    suffix prevents collisions when the same benchmark runs in parallel.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S%f")
    return f"{benchmark_name}-{stamp}"


def _prepare_run_story(source: Path, dest: Path, run_id: str) -> Path:
    """Copy *source* story.json to *dest* with ``change_id`` overridden to *run_id*.

    This ensures each eval run writes to its own ``agent-context/<run_id>/``
    directory so parallel runs of the same benchmark never collide.
    """
    data = json.loads(source.read_text(encoding="utf-8"))
    data["change_id"] = run_id
    dest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return dest


def validate_benchmark(path: Path) -> None:
    story = path / "story.json"
    tests = path / "hidden_tests.py"
    if not story.is_file():
        raise FileNotFoundError(f"missing {story}")
    if not tests.is_file():
        raise FileNotFoundError(f"missing {tests}")
    data = json.loads(story.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{story} must contain a JSON object")
    missing = [name for name in ("change_id", "title", "description", "acceptance_criteria") if not data.get(name)]
    if missing:
        raise ValueError(f"{story} is missing: {', '.join(missing)}")
    if not isinstance(data["acceptance_criteria"], list) or not data["acceptance_criteria"]:
        raise ValueError(f"{story} acceptance_criteria must be a non-empty list")


def discover_benchmarks(root: Path, names: list[str]) -> list[Path]:
    if names:
        paths = [root / name for name in names]
    else:
        paths = sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    for path in paths:
        validate_benchmark(path)
    return paths


def prepare_workspace(repo: str, sha: str, workspace: Path) -> None:
    result = run_cmd(["git", "clone", repo, str(workspace)], cwd=ROOT)
    if result.returncode != 0:
        raise RuntimeError(f"git clone failed:\n{tail(result.stderr)}")
    result = run_cmd(["git", "checkout", "--detach", sha], cwd=workspace)
    if result.returncode != 0:
        raise RuntimeError(f"git checkout failed:\n{tail(result.stderr)}")


def build_workflow_command(
    path: Path,
    workspace: Path,
    args: argparse.Namespace,
    *,
    story_file: Path | None = None,
) -> list[str]:
    cmd = [
        sys.executable,
        str(RUN_PY),
        "--repo",
        str(workspace),
        "--story-file",
        str(story_file if story_file is not None else path / "story.json"),
        "--runner",
        args.runner,
        "--headless",
        "--log-level",
        args.log_level,
    ]
    if args.model:
        cmd += ["--model", args.model]
    if not args.include_lessons:
        cmd.append("--skip-lessons-optimizer")
    return cmd


def run_workflow_with_progress(
    cmd: list[str],
    *,
    cwd: Path,
    timeout: int | None,
    env: dict[str, str],
    include_lessons: bool,
) -> subprocess.CompletedProcess[str]:
    progress = WorkflowProgress(stages=workflow_stage_names(include_lessons))
    start = time.monotonic()

    with tempfile.TemporaryDirectory(prefix="awb_eval_workflow_") as temp_dir:
        temp_root = Path(temp_dir)
        event_log_path = temp_root / "events.jsonl"
        stdout_path = temp_root / "workflow.stdout.log"
        stderr_path = temp_root / "workflow.stderr.log"
        proc_env = dict(env)
        proc_env["AGENT_RUNNER_EVENT_LOG"] = str(event_log_path)

        with stdout_path.open("w", encoding="utf-8") as stdout_fh, stderr_path.open("w", encoding="utf-8") as stderr_fh:
            process = subprocess.Popen(
                cmd,
                cwd=str(cwd),
                env=proc_env,
                stdout=stdout_fh,
                stderr=stderr_fh,
                text=True,
            )

            try:
                while True:
                    if _poll_workflow_events(event_log_path, progress):
                        _emit_progress(progress)

                    returncode = process.poll()
                    if returncode is not None:
                        break

                    now = time.monotonic()
                    if timeout is not None and now - start > timeout:
                        process.kill()
                        process.wait()
                        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

                    if progress.last_rendered and now - progress.last_render_time >= WORKFLOW_HEARTBEAT_SECONDS:
                        _emit_progress(progress, heartbeat=True)

                    time.sleep(WORKFLOW_POLL_SECONDS)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()

        _poll_workflow_events(event_log_path, progress)
        stdout = stdout_path.read_text(encoding="utf-8") if stdout_path.exists() else ""
        stderr = stderr_path.read_text(encoding="utf-8") if stderr_path.exists() else ""
        completed = subprocess.CompletedProcess(
            cmd,
            process.returncode if process.returncode is not None else 0,
            stdout,
            stderr,
        )
        if completed.returncode == 0:
            _emit_progress(progress, done=True)
        return completed


def invoke_workflow(
    path: Path,
    workspace: Path,
    args: argparse.Namespace,
    *,
    story_file: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = build_workflow_command(path, workspace, args, story_file=story_file)
    return run_workflow_with_progress(
        cmd,
        cwd=ROOT,
        timeout=args.workflow_timeout,
        env=workflow_env(),
        include_lessons=args.include_lessons,
    )


def run_project_tests(command: str | None, workspace: Path, timeout: int) -> subprocess.CompletedProcess[str] | None:
    if not command:
        return None
    return run_cmd(command, cwd=workspace, timeout=timeout, env=workflow_env(), shell=True)


def run_hidden_tests(path: Path, workspace: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    shutil.copy2(path / "hidden_tests.py", workspace / "hidden_tests.py")
    env = workflow_env()
    env["PYTHONPATH"] = str(workspace) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return run_cmd(
        [sys.executable, "-m", "pytest", "hidden_tests.py", "--disable-warnings", "-q"],
        cwd=workspace,
        timeout=timeout,
        env=env,
    )


def run_one(path: Path, args: argparse.Namespace) -> dict[str, Any]:
    start = time.monotonic()
    sandbox_obj: tempfile.TemporaryDirectory[str] | None = None
    if args.keep_sandbox:
        sandbox = Path(tempfile.mkdtemp(prefix=f"awb_eval_{path.name}_"))
    else:
        sandbox_obj = tempfile.TemporaryDirectory(prefix=f"awb_eval_{path.name}_")
        sandbox = Path(sandbox_obj.name)
    workspace = sandbox / "workspace"

    # --- collision prevention --------------------------------------------------
    # Each eval run gets its own unique change_id so parallel runs of the same
    # benchmark don't clobber each other's agent-context/ artifacts.
    run_id = _make_run_id(path.name)
    run_story = _prepare_run_story(path / "story.json", sandbox / "story.json", run_id)
    # --------------------------------------------------------------------------

    print(f"\n== {path.name} ==")
    result: dict[str, Any] = {"name": path.name, "status": "FAIL", "error": "", "seconds": None, "run_id": run_id}
    try:
        print("Preparing sandbox")
        prepare_workspace(args.repo, args.sha, workspace)

        print("Running workflow")
        workflow = invoke_workflow(path, workspace, args, story_file=run_story)
        if workflow.returncode != 0:
            print(tail(workflow.stdout))
            print(tail(workflow.stderr), file=sys.stderr)
            result["error"] = "workflow failed"
            return result

        if args.project_test_command:
            print("Running project tests")
        project = run_project_tests(args.project_test_command, workspace, args.test_timeout)
        if project is not None:
            if project.returncode != 0:
                print(tail(project.stdout))
                print(tail(project.stderr), file=sys.stderr)
                result["error"] = "project tests failed"
                return result

        print("Running hidden tests")
        hidden = run_hidden_tests(path, workspace, args.test_timeout)
        if hidden.returncode != 0:
            print(tail(hidden.stdout))
            print(tail(hidden.stderr), file=sys.stderr)
            result["error"] = "hidden tests failed"
            return result

        result["status"] = "PASS"
        print("PASS")
        return result
    except subprocess.TimeoutExpired as exc:
        result["error"] = f"timeout after {exc.timeout}s"
        print(result["error"], file=sys.stderr)
        return result
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        print(result["error"], file=sys.stderr)
        return result
    finally:
        elapsed = round(time.monotonic() - start, 2)
        result["seconds"] = elapsed
        if args.keep_sandbox:
            print(f"Sandbox kept at {workspace}")
        elif sandbox_obj is not None:
            try:
                sandbox_obj.cleanup()
            except OSError:
                # Best-effort cleanup; ignore leftover files (e.g. .git locks)
                shutil.rmtree(sandbox_obj.name, ignore_errors=True)
        print(f"Finished {path.name} in {elapsed}s")


def _difficulty_label(results: list[dict[str, Any]]) -> str:
    """Derive a difficulty label from the benchmark names in results.

    Benchmark names are expected to be one of 'easy', 'medium', or 'hard'.
    If all three are present the label is 'all'; multiple distinct values are
    joined with '+'; a single value is returned as-is; anything unrecognised
    falls back to 'mixed'.
    """
    known = {"easy", "medium", "hard"}
    levels = sorted({r["name"] for r in results if r.get("name") in known})
    if not levels:
        return "mixed"
    if set(levels) == known:
        return "all"
    return "+".join(levels)


def write_report(results: list[dict[str, Any]], args: argparse.Namespace) -> None:
    if not args.write_report:
        return
    DEFAULT_REPORTS.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    payload = {
        "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repo": args.repo,
        "sha": args.sha,
        "runner": args.runner,
        "model": args.model,
        "results": results,
    }
    # Filename: YYYY-MM-DD-HHMMss-<difficulty>.json  e.g. 2026-05-20-143022-easy.json
    stamp = now.strftime("%Y-%m-%d-%H%M%S")
    difficulty = _difficulty_label(results)
    report_path = DEFAULT_REPORTS / f"{stamp}-{difficulty}.json"
    latest_path = DEFAULT_REPORTS / "latest.json"
    report_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    latest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Report written to {report_path}")


def build_parser() -> argparse.ArgumentParser:
    env_file = read_env_file(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Run hidden-test workflow benchmarks.")
    parser.add_argument("--repo", default=default("EVAL_TARGET_REPO", env_file), help="Target Git repo path or URL. Defaults to EVAL_TARGET_REPO.")
    parser.add_argument("--sha", default=default("EVAL_TARGET_SHA", env_file), help="Gold-master commit SHA. Defaults to EVAL_TARGET_SHA.")
    parser.add_argument("--benchmarks-dir", type=Path, default=DEFAULT_BENCHMARKS)
    parser.add_argument("--benchmark", action="append", default=[], help="Benchmark name to run; repeat for multiple. Defaults to all.")
    parser.add_argument("--difficulty", nargs="+", choices=["easy", "medium", "hard"], default=None, help="Difficulty level(s) to run (e.g. --difficulty easy medium). Defaults to all.")
    parser.add_argument("--runner", default=default("EVAL_RUNNER", env_file, "claude"))
    parser.add_argument("--model", default=None)
    parser.add_argument("--workflow-timeout", type=int, default=10800) # 3 hours
    parser.add_argument("--test-timeout", type=int, default=300)
    parser.add_argument("--project-test-command", default=default("EVAL_PROJECT_TEST_COMMAND", env_file))
    parser.add_argument("--include-lessons", action="store_true", help="Include the lessons optimizer stage. Skipped by default for faster evals.")
    parser.add_argument("--keep-sandbox", action="store_true")
    parser.add_argument("--write-report", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--log-level", default="warning")
    return parser


def main(argv: list[str] | None = None) -> int:
    env_file = read_env_file(ROOT / ".env")
    args = build_parser().parse_args(argv)
    args.model = resolve_model_override(args.runner, args.model, env_file)
    if not args.repo:
        print("Missing target repo. Pass --repo or set EVAL_TARGET_REPO in .env.", file=sys.stderr)
        return 2
    if not args.sha:
        print("Missing gold-master SHA. Pass --sha or set EVAL_TARGET_SHA in .env.", file=sys.stderr)
        return 2
    try:
        names = args.benchmark
        if args.difficulty and not names:
            names = args.difficulty
        benchmarks = discover_benchmarks(args.benchmarks_dir, names)
    except Exception as exc:
        print(f"Benchmark discovery failed: {exc}", file=sys.stderr)
        return 2
    if not benchmarks:
        print(f"No benchmarks found in {args.benchmarks_dir}", file=sys.stderr)
        return 2

    results = [run_one(path, args) for path in benchmarks]

    print("\nEvaluation report")
    print("-----------------")
    for result in results:
        suffix = f" ({result['error']})" if result.get("error") else ""
        print(f"{result['name']:24} {result['status']}{suffix}")
    write_report(results, args)
    return 0 if all(result["status"] == "PASS" for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
