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
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.runner_models import RUNNER_MODEL_CHOICES, is_copilot_runner
from eval.result_schema import BenchmarkResult, EvalMetrics, TestSummary
from eval.seed_benchmarks import (
    acceptance_criterion_ids,
    find_ac_test_map,
    validate_ac_test_map,
    validate_hidden_tests,
)

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


def emit_event(event: dict[str, Any]) -> None:
    path = os.environ.get("AGENT_RUNNER_EVENT_LOG")
    if not path:
        return
    payload = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **event}
    event_path = Path(path)
    event_path.parent.mkdir(parents=True, exist_ok=True)
    with event_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


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

    if runner == "openai-compat":
        return inherited_model
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


def benchmark_story(path: Path) -> dict[str, Any]:
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
    return data


def benchmark_ac_test_map(path: Path) -> dict[str, list[str]]:
    tree = ast.parse((path / "hidden_tests.py").read_text(encoding="utf-8"), filename=str(path / "hidden_tests.py"))
    return find_ac_test_map(tree)


def validate_benchmark(path: Path) -> None:
    story = benchmark_story(path)
    hidden_tests = validate_hidden_tests((path / "hidden_tests.py").read_text(encoding="utf-8"))
    validate_ac_test_map(story, hidden_tests)


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


def parse_junit(path: Path) -> TestSummary:
    if not path.exists():
        return TestSummary()
    root = ET.parse(path).getroot()
    suites = list(root.iter("testsuite"))
    total = sum(int(suite.get("tests", 0) or 0) for suite in suites)
    failed = sum(int(suite.get("failures", 0) or 0) for suite in suites)
    errors = sum(int(suite.get("errors", 0) or 0) for suite in suites)
    skipped = sum(int(suite.get("skipped", 0) or 0) for suite in suites)
    cases: list[dict[str, Any]] = []
    for case in root.iter("testcase"):
        status = "passed"
        message = ""
        failure = case.find("failure")
        error = case.find("error")
        skipped_node = case.find("skipped")
        if skipped_node is not None:
            status = "skipped"
            message = skipped_node.get("message") or (skipped_node.text or "")
        elif failure is not None:
            status = "failed"
            message = failure.get("message") or (failure.text or "")
        elif error is not None:
            status = "skipped" if (error.get("type") or "").lower().endswith("skipped") else "error"
            message = error.get("message") or (error.text or "")
        cases.append(
            {
                "classname": case.get("classname") or "",
                "name": case.get("name") or "",
                "time": float(case.get("time", 0) or 0),
                "status": status,
                "message": tail(message, 1000),
            }
        )
    if not total:
        total = len(cases)
        failed = sum(1 for case in cases if case["status"] == "failed")
        errors = sum(1 for case in cases if case["status"] == "error")
        skipped = sum(1 for case in cases if case["status"] == "skipped")
    passed = max(total - failed - errors - skipped, 0)
    return TestSummary(total=total, passed=passed, failed=failed, skipped=skipped, errors=errors, cases=cases)


def _case_matches_test(case: dict[str, Any], test_name: str) -> bool:
    name = str(case.get("name") or "")
    classname = str(case.get("classname") or "")
    return name == test_name or name.startswith(f"{test_name}[") or test_name in f"{classname}.{name}"


def map_ac_results(summary: TestSummary, ac_map: dict[str, list[str]], *, required_ids: list[str]) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for ac_id in required_ids:
        mapped_tests = ac_map.get(ac_id, [])
        cases = [case for test_name in mapped_tests for case in summary.cases if _case_matches_test(case, test_name)]
        statuses = [case["status"] for case in cases]
        passed = bool(cases) and all(status == "passed" for status in statuses)
        results[ac_id] = {
            "tests": mapped_tests,
            "cases": cases,
            "passed": passed,
            "missing_cases": not cases,
            "failed": any(status in {"failed", "error"} for status in statuses),
            "skipped": any(status == "skipped" for status in statuses),
        }
    summary.ac_results = results
    return results


def run_hidden_tests(path: Path, workspace: Path, timeout: int) -> tuple[subprocess.CompletedProcess[str], TestSummary]:
    shutil.copy2(path / "hidden_tests.py", workspace / "hidden_tests.py")
    junit_path = workspace / ".awb-hidden-tests.xml"
    env = workflow_env()
    env["PYTHONPATH"] = str(workspace) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    completed = run_cmd(
        [sys.executable, "-m", "pytest", "hidden_tests.py", "--disable-warnings", "-q", "--junitxml", str(junit_path)],
        cwd=workspace,
        timeout=timeout,
        env=env,
    )
    return completed, parse_junit(junit_path)


def collect_session_metrics(run_id: str) -> dict[str, Any]:
    roots = [ROOT / "logs" / run_id, ROOT / "eval" / "reports" / "analyze" / run_id]
    sessions: list[Path] = []
    for root in roots:
        if root.is_dir():
            sessions.extend(root.rglob("*_session.json"))
    by_agent: dict[str, dict[str, Any]] = {}
    total_duration_ms = 0
    tokens_in = 0
    tokens_out = 0
    for path in sessions:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        agent = str(payload.get("agent") or "unknown")
        duration_ms = int(payload.get("duration_ms") or 0)
        prompt_tokens = int(payload.get("prompt_est_tokens") or payload.get("tokens_in") or 0)
        response_tokens = int(payload.get("response_est_tokens") or payload.get("tokens_out") or 0)
        total_duration_ms += duration_ms
        tokens_in += prompt_tokens
        tokens_out += response_tokens
        bucket = by_agent.setdefault(agent, {"calls": 0, "duration_ms": 0, "tokens_in": 0, "tokens_out": 0})
        bucket["calls"] += 1
        bucket["duration_ms"] += duration_ms
        bucket["tokens_in"] += prompt_tokens
        bucket["tokens_out"] += response_tokens
    return {
        "agent_sessions": len(sessions),
        "llm_session_seconds": round(total_duration_ms / 1000.0, 2),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "tokens_total": tokens_in + tokens_out,
        "by_agent": by_agent,
    }


def _empty_quality(story: dict[str, Any]) -> dict[str, Any]:
    ac_ids = acceptance_criterion_ids(story["acceptance_criteria"])
    return {
        "weighted_score": 0.0,
        "ac_passed": 0,
        "ac_total": len(ac_ids),
        "ac_failed_ids": ac_ids,
        "critical_ac_failed": len(ac_ids),
        "project_tests_passed": False,
        "hidden_tests_passed": False,
        "hidden_tests_skipped": 0,
    }


def build_quality(
    *,
    story: dict[str, Any],
    ac_results: dict[str, dict[str, Any]],
    project_passed: bool,
    hidden_passed: bool,
    hidden_summary: TestSummary | None,
) -> dict[str, Any]:
    ac_ids = acceptance_criterion_ids(story["acceptance_criteria"])
    passed_ids = [ac_id for ac_id in ac_ids if ac_results.get(ac_id, {}).get("passed")]
    failed_ids = [ac_id for ac_id in ac_ids if ac_id not in passed_ids]
    critical_ids = story.get("metadata", {}).get("critical_acceptance_criteria") or story.get("metadata", {}).get("critical_acs") or ac_ids
    critical_failed = [ac_id for ac_id in failed_ids if ac_id in set(critical_ids)]
    return {
        "weighted_score": round(len(passed_ids) / len(ac_ids), 4) if ac_ids else 0.0,
        "ac_passed": len(passed_ids),
        "ac_total": len(ac_ids),
        "ac_failed_ids": failed_ids,
        "critical_ac_failed": len(critical_failed),
        "project_tests_passed": project_passed,
        "hidden_tests_passed": hidden_passed,
        "hidden_tests_skipped": hidden_summary.skipped if hidden_summary else 0,
    }


def finalize_result(result: BenchmarkResult, start: float) -> dict[str, Any]:
    elapsed = round(time.monotonic() - start, 2)
    session_metrics = collect_session_metrics(result.run_id)
    result.metrics = EvalMetrics(wall_seconds=elapsed, cost_usd=0.0, **session_metrics)
    payload = result.to_dict()
    payload["seconds"] = elapsed
    emit_event({"type": "metrics", "tokens_in": result.metrics.tokens_in, "tokens_out": result.metrics.tokens_out, "cost_usd": result.metrics.cost_usd})
    emit_event({"type": "job.end", "job_id": result.run_id, "status": "ok" if result.status == "PASS" else "error", "msg": result.error})
    return payload


def run_one(path: Path, args: argparse.Namespace, *, trial_index: int = 1) -> dict[str, Any]:
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
    run_id = _make_run_id(f"{path.name}-t{trial_index}")
    run_story = _prepare_run_story(path / "story.json", sandbox / "story.json", run_id)
    # --------------------------------------------------------------------------

    story = benchmark_story(path)
    ac_ids = acceptance_criterion_ids(story["acceptance_criteria"])
    ac_map = benchmark_ac_test_map(path)
    print(f"\n== {path.name} (trial {trial_index}) ==")
    emit_event({"type": "job.start", "job_id": run_id, "msg": f"Starting benchmark {path.name} trial {trial_index}"})
    result = BenchmarkResult(
        name=path.name,
        run_id=run_id,
        status="FAIL",
        error="",
        score_weighted=0.0,
        trial_index=trial_index,
        project_tests=None,
        hidden_tests=None,
        quality=_empty_quality(story),
        metrics=EvalMetrics(wall_seconds=0.0),
        story={
            "change_id": story.get("change_id"),
            "title": story.get("title"),
            "description": story.get("description"),
            "acceptance_criteria": story.get("acceptance_criteria", []),
            "metadata": story.get("metadata", {}),
        },
        artifacts={"story": str(run_story)},
    )
    try:
        print("Preparing sandbox")
        emit_event({"type": "log", "level": "info", "stage": "benchmark.clone", "msg": f"Preparing sandbox for {path.name}"})
        prepare_workspace(args.repo, args.sha, workspace)

        if not args.no_verify_gold_fails:
            print("Verifying hidden tests fail on gold master")
            gold_hidden, gold_summary = run_hidden_tests(path, workspace, args.test_timeout)
            if gold_hidden.returncode == 0:
                result.error = "hidden tests unexpectedly passed on gold master"
                return finalize_result(result, start)
            if gold_hidden.returncode in {2, 3, 4, 5} or gold_summary.errors:
                result.error = f"hidden tests errored on gold master: pytest exit {gold_hidden.returncode}"
                return finalize_result(result, start)
            if gold_summary.skipped and not args.allow_hidden_skips:
                result.error = f"hidden tests skipped on gold master: {gold_summary.skipped}"
                return finalize_result(result, start)

        print("Running workflow")
        emit_event({"type": "log", "level": "info", "stage": "benchmark.workflow", "msg": f"Running workflow for {path.name}"})
        workflow = invoke_workflow(path, workspace, args, story_file=run_story)
        if workflow.returncode != 0:
            print(tail(workflow.stdout))
            print(tail(workflow.stderr), file=sys.stderr)
            result.error = "workflow failed"
            return finalize_result(result, start)

        if args.project_test_command:
            print("Running project tests")
        project = run_project_tests(args.project_test_command, workspace, args.test_timeout)
        project_passed = project is None or project.returncode == 0
        if project is not None:
            project_summary = TestSummary(total=1, passed=1 if project.returncode == 0 else 0, failed=0 if project.returncode == 0 else 1)
            result.project_tests = project_summary
            if project.returncode != 0:
                print(tail(project.stdout))
                print(tail(project.stderr), file=sys.stderr)
                result.error = "project tests failed"
                result.quality = build_quality(
                    story=story,
                    ac_results={ac_id: {"passed": False, "tests": ac_map.get(ac_id, [])} for ac_id in ac_ids},
                    project_passed=False,
                    hidden_passed=False,
                    hidden_summary=None,
                )
                return finalize_result(result, start)

        print("Running hidden tests")
        emit_event({"type": "log", "level": "info", "stage": "benchmark.hidden_tests", "msg": f"Running hidden tests for {path.name}"})
        hidden, hidden_summary = run_hidden_tests(path, workspace, args.test_timeout)
        ac_results = map_ac_results(hidden_summary, ac_map, required_ids=ac_ids)
        hidden_passed = hidden.returncode == 0 and (args.allow_hidden_skips or hidden_summary.skipped == 0)
        result.hidden_tests = hidden_summary
        result.quality = build_quality(
            story=story,
            ac_results=ac_results,
            project_passed=project_passed,
            hidden_passed=hidden_passed,
            hidden_summary=hidden_summary,
        )
        result.score_weighted = result.quality["weighted_score"]
        if hidden_summary.skipped and not args.allow_hidden_skips:
            result.error = f"hidden tests skipped: {hidden_summary.skipped}"
            print(result.error, file=sys.stderr)
            return finalize_result(result, start)
        missing_cases = [ac_id for ac_id, ac_result in ac_results.items() if ac_result.get("missing_cases")]
        if missing_cases:
            result.error = f"AC_TEST_MAP entries did not execute: {', '.join(missing_cases)}"
            print(result.error, file=sys.stderr)
            return finalize_result(result, start)
        if hidden.returncode != 0:
            print(tail(hidden.stdout))
            print(tail(hidden.stderr), file=sys.stderr)
            result.error = "hidden tests failed"
            return finalize_result(result, start)

        result.status = "PASS"
        print("PASS")
        return finalize_result(result, start)
    except subprocess.TimeoutExpired as exc:
        result.error = f"timeout after {exc.timeout}s"
        print(result.error, file=sys.stderr)
        return finalize_result(result, start)
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        print(result.error, file=sys.stderr)
        return finalize_result(result, start)
    finally:
        elapsed = round(time.monotonic() - start, 2)
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


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def aggregate_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    quality_scores = [float(result.get("quality", {}).get("weighted_score") or 0.0) for result in results]
    passed = sum(1 for result in results if result.get("status") == "PASS")
    wall_times = [float(result.get("metrics", {}).get("wall_seconds") or result.get("seconds") or 0.0) for result in results]
    tokens = [float(result.get("metrics", {}).get("tokens_total") or 0.0) for result in results]
    cost = [float(result.get("metrics", {}).get("cost_usd") or 0.0) for result in results]
    failure_categories: dict[str, int] = {}
    for result in results:
        if result.get("status") == "PASS":
            continue
        key = str(result.get("error") or "failed").split(":", 1)[0]
        failure_categories[key] = failure_categories.get(key, 0) + 1
    hidden_skipped = sum(int(result.get("quality", {}).get("hidden_tests_skipped") or 0) for result in results)
    incomplete_ac_mapping = any(
        ac_result.get("missing_cases")
        for result in results
        for ac_result in ((result.get("hidden_tests") or {}).get("ac_results") or {}).values()
    )
    warnings = []
    if hidden_skipped:
        warnings.append("Hidden tests skipped.")
    if incomplete_ac_mapping:
        warnings.append("Benchmark report/AC mapping is incomplete.")
    return {
        "quality": {
            "weighted_score": _mean(quality_scores),
            "ac_passed": sum(int(result.get("quality", {}).get("ac_passed") or 0) for result in results),
            "ac_total": sum(int(result.get("quality", {}).get("ac_total") or 0) for result in results),
            "critical_ac_failed": sum(int(result.get("quality", {}).get("critical_ac_failed") or 0) for result in results),
            "hidden_tests_skipped": hidden_skipped,
        },
        "reliability": {
            "runs": len(results),
            "passed": passed,
            "pass_rate": round(passed / len(results), 4) if results else 0.0,
            "weighted_score_mean": _mean(quality_scores),
            "failure_categories": failure_categories,
        },
        "efficiency": {
            "wall_seconds_mean": _mean(wall_times),
            "tokens_total_mean": _mean(tokens),
            "cost_usd_mean": _mean(cost),
        },
        "warnings": warnings,
    }


def classify_trend(
    current: dict[str, Any],
    baseline: dict[str, Any],
    *,
    quality_pp: float = 5.0,
    efficiency_pct: float = 15.0,
) -> str:
    q_delta = float(current.get("quality", {}).get("weighted_score") or 0.0) - float(baseline.get("quality", {}).get("weighted_score") or 0.0)
    baseline_time = max(float(baseline.get("efficiency", {}).get("wall_seconds_mean") or 0.0), 1.0)
    baseline_tokens = max(float(baseline.get("efficiency", {}).get("tokens_total_mean") or 0.0), 1.0)
    time_delta_pct = 100.0 * (float(current.get("efficiency", {}).get("wall_seconds_mean") or 0.0) - baseline_time) / baseline_time
    token_delta_pct = 100.0 * (float(current.get("efficiency", {}).get("tokens_total_mean") or 0.0) - baseline_tokens) / baseline_tokens
    threshold = quality_pp / 100.0
    if q_delta <= -threshold:
        return "decreased"
    if abs(q_delta) < threshold and (time_delta_pct > efficiency_pct or token_delta_pct > efficiency_pct):
        return "decreased"
    if q_delta >= threshold:
        return "increased"
    if abs(q_delta) < threshold and time_delta_pct < -efficiency_pct and token_delta_pct < -efficiency_pct:
        return "increased"
    return "same"


def load_baseline(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_report(results: list[dict[str, Any]], args: argparse.Namespace) -> None:
    if not args.write_report:
        return
    DEFAULT_REPORTS.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    summary = aggregate_results(results)
    baseline_payload = load_baseline(args.compare_to)
    baseline_summary = baseline_payload.get("summary") if baseline_payload else None
    if baseline_summary:
        summary["trend"] = classify_trend(
            summary,
            baseline_summary,
            quality_pp=args.regression_quality_pp,
            efficiency_pct=args.regression_efficiency_pct,
        )
        summary["baseline"] = baseline_summary
        if baseline_payload.get("runner") != args.runner or baseline_payload.get("model") != args.model:
            summary.setdefault("warnings", []).append("Current runner/model differs from baseline.")
        if baseline_payload.get("sha") != args.sha:
            summary.setdefault("warnings", []).append("Current target SHA differs from baseline.")
        baseline_names = sorted({result.get("name") for result in baseline_payload.get("results", [])})
        current_names = sorted({result.get("name") for result in results})
        if baseline_names != current_names:
            summary.setdefault("warnings", []).append("Current benchmark set differs from baseline.")
    else:
        summary["trend"] = "insufficient data"
        summary.setdefault("warnings", []).append("No baseline selected.")
    if args.runs <= 1:
        summary.setdefault("warnings", []).append("Only one run; reliability unknown.")
    payload = {
        "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repo": args.repo,
        "sha": args.sha,
        "runner": args.runner,
        "model": args.model,
        "runs": args.runs,
        "summary": summary,
        "results": results,
    }
    # Filename: YYYY-MM-DD-HHMMss-<difficulty>.json  e.g. 2026-05-20-143022-easy.json
    stamp = now.strftime("%Y-%m-%d-%H%M%S")
    difficulty = _difficulty_label(results)
    report_path = DEFAULT_REPORTS / f"{stamp}-{difficulty}.json"
    latest_path = DEFAULT_REPORTS / "latest.json"
    report_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    latest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if args.update_baseline:
        baseline_path = args.compare_to or (DEFAULT_REPORTS / "baseline.json")
        baseline_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
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
    parser.add_argument("--runs", type=int, default=1, help="Number of trials to run per benchmark.")
    parser.add_argument("--allow-hidden-skips", action="store_true", help="Allow skipped hidden tests without failing the benchmark.")
    parser.add_argument("--no-verify-gold-fails", action="store_true", help="Do not require hidden tests to fail cleanly on gold master before running the workflow.")
    parser.add_argument("--compare-to", type=Path, default=None, help="Baseline report JSON to compare against.")
    parser.add_argument("--update-baseline", action="store_true", help="Write the current report as the baseline report.")
    parser.add_argument("--regression-quality-pp", type=float, default=5.0)
    parser.add_argument("--regression-efficiency-pct", type=float, default=15.0)
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

    if args.runs < 1:
        print("--runs must be >= 1", file=sys.stderr)
        return 2

    results = []
    for path in benchmarks:
        for trial_index in range(1, args.runs + 1):
            results.append(run_one(path, args, trial_index=trial_index))

    print("\nEvaluation report")
    print("-----------------")
    for result in results:
        suffix = f" ({result['error']})" if result.get("error") else ""
        print(f"{result['name']:24} trial {result.get('trial_index', 1):02d} {result['status']}{suffix}")
    write_report(results, args)
    return 0 if all(result["status"] == "PASS" for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
