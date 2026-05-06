import argparse
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import yaml

from core.artifact_utils import load_assignments_file
from dotenv import load_dotenv
load_dotenv()

import core.opik_integration  # noqa: F401 — configures Opik project context before the flow runs
import core.steps as steps
from core.evaluator_optimizer_loops import run_uow_eval_loop, run_eval_optimizer_loop
from core.materialize import run_materialization
from core.runner_models import COPILOT_EFFORT_CHOICES, RUNNER_DEFAULT_MODELS, RUNNER_MODEL_CHOICES, canonical_runner, is_copilot_runner, copilot_alias_model
from core.workflow_inputs import DEFAULT_TEST_STORY_FILE, resolve_workflow_input

logger = logging.getLogger(__name__)

# ====================== HELPERS ====================== #

RUNNER_ROOT = Path(__file__).resolve().parent
AGENT_CONTEXT_ROOT = RUNNER_ROOT / "agent-context"
RUNNER_LABELS = {
    "claude": "Claude Code",
    "copilot": "GitHub Copilot",
    "gemini": "Gemini CLI",
}
WORKFLOW_STATUS_FILENAME = "workflow_status.yaml"


def _emit(type: str, **fields) -> None:
    """No-op unless AGENT_RUNNER_EVENT_LOG is set (server-driven runs)."""
    if not os.environ.get("AGENT_RUNNER_EVENT_LOG"):
        return
    try:
        from server.events import emit
        emit(type, **fields)
    except Exception:
        pass


class _Stage:
    """Context manager that emits stage.start/stage.end events around a block."""

    def __init__(self, name: str, state: dict[str, str | None] | None = None) -> None:
        self.name = name
        self.state = state

    def __enter__(self):
        if self.state is not None:
            self.state["current_stage"] = self.name
        logger.info("Stage START: %s", self.name)
        _emit("stage.start", stage=self.name)
        return self

    def __exit__(self, exc_type, exc, tb):
        status = "ok" if exc_type is None else "error"
        if exc_type is not None:
            if self.state is not None:
                self.state["failed_stage"] = self.name
            summary = _summarize_exception(exc)
            logger.error("Stage ERROR: %s — %s: %s", self.name, exc_type.__name__, summary)
            _emit(
                "log",
                level="error",
                kind="stage_failed",
                stage=self.name,
                msg=f"{exc_type.__name__}: {summary}"[:500],
            )
        else:
            if self.state is not None:
                self.state["last_completed_stage"] = self.name
                self.state["current_stage"] = None
            logger.info("Stage END: %s (ok)", self.name)
        _emit("stage.end", stage=self.name, status=status)
        return False


def get_time():
    now = datetime.now()
    return now.strftime("%H:%M %m/%d/%Y")


def use_runner_root() -> None:
    logger.debug("use_runner_root: chdir → %s", RUNNER_ROOT)
    os.chdir(RUNNER_ROOT)


def runner_label(runner: str) -> str:
    label = RUNNER_LABELS.get(runner, runner)
    logger.debug("runner_label: %s → %s", runner, label)
    return label


def _last_nonempty_line(text: str | None) -> str:
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _truncate_text(text: str | None, limit: int = 2000) -> str | None:
    if not text:
        return None
    if len(text) <= limit:
        return text
    return f"...{text[-limit:]}"


def _truncate_arg(arg: str, limit: int = 160) -> str:
    if len(arg) <= limit:
        return arg
    head = arg[:80]
    tail = arg[-40:]
    omitted = len(arg) - len(head) - len(tail)
    return f"{head}...<{omitted} chars omitted>...{tail}"


def _normalize_command(command: object) -> object:
    if isinstance(command, (list, tuple)):
        normalized: list[str] = []
        hide_prompt_value = False
        for raw_arg in command:
            arg = str(raw_arg)
            if hide_prompt_value:
                normalized.append(f"<prompt omitted len={len(arg)}>")
                hide_prompt_value = False
                continue
            normalized.append(_truncate_arg(arg))
            if arg in {"-p", "--prompt"}:
                hide_prompt_value = True
        return normalized
    if command is None:
        return None
    return _truncate_arg(str(command))


def _command_label(command: object) -> str:
    if isinstance(command, (list, tuple)):
        parts = [str(part) for part in command]
        agent = next((part.split("=", 1)[1] for part in parts if part.startswith("--agent=")), None)
        if agent:
            return agent
        if parts:
            return parts[0]
    elif command is not None:
        return str(command)
    return "command"


def _summarize_called_process_error(exc: subprocess.CalledProcessError) -> str:
    stdout = getattr(exc, "stdout", None) or getattr(exc, "output", None)
    stderr = getattr(exc, "stderr", None)
    detail = _last_nonempty_line(stderr) or _last_nonempty_line(stdout)
    label = _command_label(getattr(exc, "cmd", None) or getattr(exc, "args", None))
    if detail:
        return f"{label} command failed (exit {exc.returncode}): {detail}"
    return f"{label} command failed (exit {exc.returncode})"


def _summarize_exception(exc: BaseException | None) -> str:
    if exc is None:
        return ""
    if isinstance(exc, subprocess.CalledProcessError):
        return _summarize_called_process_error(exc)
    detail = str(exc).strip()
    return detail or type(exc).__name__


def _serialize_exception(exc: BaseException) -> dict:
    payload: dict[str, object] = {
        "exception_type": type(exc).__name__,
        "message": _summarize_exception(exc),
    }
    if isinstance(exc, subprocess.CalledProcessError):
        payload["returncode"] = exc.returncode
        command = getattr(exc, "cmd", None) or getattr(exc, "args", None)
        normalized_command = _normalize_command(command)
        if normalized_command is not None:
            payload["command"] = normalized_command
        stdout = getattr(exc, "stdout", None) or getattr(exc, "output", None)
        stderr = getattr(exc, "stderr", None)
        if stdout:
            payload["stdout_tail"] = _truncate_text(stdout)
        if stderr:
            payload["stderr_tail"] = _truncate_text(stderr)
    return payload


def _workflow_status_path(change_id: str) -> Path:
    return AGENT_CONTEXT_ROOT / change_id / "summary" / WORKFLOW_STATUS_FILENAME


def _write_workflow_status(
    *,
    change_id: str,
    status: str,
    stage_state: dict[str, str | None],
    runner: str,
    model: str | None,
    repo: str,
    exit_code: int,
    exc: BaseException | None = None,
) -> Path | None:
    run_dir = AGENT_CONTEXT_ROOT / change_id
    if not run_dir.is_dir():
        logger.warning("_write_workflow_status: run directory missing for change_id=%s", change_id)
        return None

    payload: dict[str, object] = {
        "change_id": change_id,
        "status": status,
        "runner": runner,
        "model": model,
        "repo": repo,
        "exit_code": exit_code,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    current_stage = stage_state.get("current_stage")
    failed_stage = current_stage or stage_state.get("failed_stage")
    last_completed_stage = stage_state.get("last_completed_stage")
    if failed_stage:
        payload["failed_stage"] = failed_stage
    if last_completed_stage:
        payload["last_completed_stage"] = last_completed_stage
    if exc is not None:
        payload["failure_summary"] = _summarize_exception(exc)
        payload["exception"] = _serialize_exception(exc)
        payload["traceback"] = _truncate_text(
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            limit=8000,
        )

    path = _workflow_status_path(change_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    logger.info("_write_workflow_status: wrote %s", path)
    return path


def clean_workspace(change_id: str) -> None:
    """Remove stale agent-context directories for this change_id before the pipeline starts.

    Removes:
      - agent-context/{change_id}/ (ensures a fresh start)
      - agent-context/{base}-RUN-*/ (stale multi-run artifacts from previous runs)
        Only when change_id is a bare ID (not already a -RUN-NN variant), to avoid
        interfering with concurrent isolated multi-run evaluations.
    """
    logger.info("clean_workspace: change_id=%s", change_id)
    if not AGENT_CONTEXT_ROOT.is_dir():
        logger.debug("clean_workspace: AGENT_CONTEXT_ROOT does not exist; skipping")
        return

    base = re.sub(r"-RUN-\d+$", "", change_id)
    is_isolated_run = base != change_id
    logger.debug("clean_workspace: base=%s is_isolated_run=%s", base, is_isolated_run)

    target = AGENT_CONTEXT_ROOT / change_id
    if target.is_dir():
        logger.info("clean_workspace: removing stale workspace %s", target)
        print(f"[cleanup] Removing stale workspace: {target.name}")
        shutil.rmtree(target)

    if not is_isolated_run:
        pattern = re.compile(rf"^{re.escape(base)}-RUN-\d+$")
        for entry in AGENT_CONTEXT_ROOT.iterdir():
            if entry.is_dir() and pattern.match(entry.name):
                logger.info("clean_workspace: removing stale multi-run workspace %s", entry)
                print(f"[cleanup] Removing stale multi-run workspace: {entry.name}")
                shutil.rmtree(entry)


def _extract_batches_from_duplicate_yaml(raw: str) -> list[dict]:
    """Extract batch blocks from YAML where execution_schedule has duplicate 'batch:' keys.

    Standard YAML parsers only keep the last value for duplicate keys, losing earlier
    batches. This function splits the raw text into per-batch segments and parses each.
    """
    import re as _re
    import textwrap as _textwrap

    # Locate execution_schedule block (indented content after the key)
    m = _re.search(r"^execution_schedule:\s*\n((?:[ \t]+.*\n?)*)", raw, _re.MULTILINE)
    if not m:
        logger.debug("_extract_batches_from_duplicate_yaml: no execution_schedule block found")
        return []
    es_block = m.group(1)

    # Split on lines that start a new batch (leading whitespace + "batch:")
    segments = _re.split(r"(?=^[ \t]+batch:\s*\d)", es_block, flags=_re.MULTILINE)
    batches: list[dict] = []
    for seg in segments:
        if not seg.strip():
            continue
        try:
            parsed = yaml.safe_load(_textwrap.dedent(seg))
            if isinstance(parsed, dict) and "batch" in parsed:
                batches.append(parsed)
        except Exception as exc:
            logger.warning("_extract_batches_from_duplicate_yaml: could not parse segment: %s", exc)
    logger.debug("_extract_batches_from_duplicate_yaml: extracted %d batch(es)", len(batches))
    return batches


def load_assignments(change_id: str) -> dict:
    """Read assignments.json produced by the task-assigner.

    The agent sometimes writes YAML instead of JSON despite the .json extension.
    Fall back to YAML parsing when JSON fails. The YAML may also use duplicate
    'batch:' keys under execution_schedule; handle that with a custom splitter.
    """
    path = AGENT_CONTEXT_ROOT / change_id / "planning" / "assignments.json"
    logger.debug("load_assignments: reading %s", path)
    data = load_assignments_file(path)
    batch_count = len(data.get("execution_schedule", []))
    logger.info("load_assignments: change_id=%s loaded %d batch(es)", change_id, batch_count)
    return data


def _validate_runner_model(parser: argparse.ArgumentParser, runner: str, model: str | None) -> str:
    """Return resolved model string; error via parser if runner or model is invalid."""
    base = canonical_runner(runner)
    if base not in RUNNER_DEFAULT_MODELS:
        parser.error(
            f"Runner '{runner}' is not valid. "
            f"Must be one of: claude, copilot, gemini, or a copilot alias (copilot-<name>)."
        )
    if model is None:
        # Copilot aliases encode the model in the runner string
        if is_copilot_runner(runner) and runner != "copilot":
            resolved = copilot_alias_model(runner)
            logger.debug("_validate_runner_model: copilot alias runner=%s using alias model=%s", runner, resolved)
            return resolved
        resolved = RUNNER_DEFAULT_MODELS[base]
        logger.debug("_validate_runner_model: runner=%s no model specified; using default=%s", runner, resolved)
        return resolved
    # Explicit model: validate against base runner's allowlist (alias runners bypass this)
    if runner == base and base in RUNNER_MODEL_CHOICES:
        valid = RUNNER_MODEL_CHOICES[base]
        if model not in valid:
            logger.error("_validate_runner_model: invalid model=%s for runner=%s valid=%s", model, runner, valid)
            parser.error(
                f"Model '{model}' is not valid for --runner {runner}. "
                f"Valid models: {', '.join(valid)}"
            )
    logger.debug("_validate_runner_model: runner=%s model=%s validated OK", runner, model)
    return model


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the agent workflow against either a live ADO story or a local synthetic story fixture."
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="Target repository path. Defaults to the current working directory.",
    )
    parser.add_argument(
        "--change-id",
        default=None,
        help="Workflow change id. Optional for ADO URLs that end in a work item id or for fixtures that include change_id.",
    )
    parser.add_argument(
        "--ado-url",
        default=None,
        help="Azure DevOps work item URL for a live intake run.",
    )
    parser.add_argument(
        "--story-file",
        default=None,
        help=(
            "Path to a synthetic story fixture JSON file for local testing. "
            f"Defaults to {DEFAULT_TEST_STORY_FILE} when neither --ado-url nor --story-file is provided."
        ),
    )
    parser.add_argument(
        "--runner",
        default="claude",
        metavar="RUNNER",
        help=(
            "Agent runner to use: 'claude' (Claude Code CLI), "
            "'copilot' (GitHub Copilot CLI), 'gemini' (Gemini CLI), "
            "or a copilot alias like 'copilot-gemma4' (uses 'gemma4' as the model). "
            "Defaults to 'claude'."
        ),
    )
    parser.add_argument(
        "--skip-materialize",
        action="store_true",
        help="Skip the agent materialization step (useful when agents are known to be up-to-date).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "Model to use for the selected runner. "
            "Defaults to the runner's default model if omitted. "
            "Must be compatible with --runner (e.g., Claude models for --runner claude)."
        ),
    )
    parser.add_argument(
        "--copilot-effort",
        default=None,
        choices=COPILOT_EFFORT_CHOICES,
        help=(
            "Reasoning effort level when --runner copilot. "
            f"Choices: {', '.join(COPILOT_EFFORT_CHOICES)}. Omitted by default."
        ),
    )
    parser.add_argument(
        "--extra-context",
        default=None,
        help=(
            "Optional free-form context to pass to the intake agent — e.g., a "
            "reference PR URL and notes. Appended verbatim to the intake prompt."
        ),
    )
    parser.add_argument(
        "--skip-lessons-optimizer",
        action="store_true",
        help="Skip the lessons optimizer stage at the end of the workflow.",
    )
    parser.add_argument(
        "--calibration-fast-mode",
        action="store_true",
        help="Use a cheaper one-iteration workflow profile intended for synthesis calibration runs.",
    )
    args = parser.parse_args()
    args.model = _validate_runner_model(parser, args.runner, args.model)
    return args

# ====================== MAIN ====================== #

def main(
    repo: str | None = None,
    change_id: str | None = None,
    ado_url: str | None = None,
    story_file: str | None = None,
    runner: str = "claude",
    skip_materialize: bool = False,
    model: str | None = None,
    copilot_effort: str | None = None,
    extra_context: str | None = None,
    skip_lessons_optimizer: bool = False,
    calibration_fast_mode: bool = False,
):
    logger.info(
        "main: starting workflow repo=%s change_id=%s runner=%s model=%s skip_materialize=%s skip_lessons_optimizer=%s calibration_fast_mode=%s",
        repo, change_id, runner, model, skip_materialize, skip_lessons_optimizer, calibration_fast_mode,
    )
    final_status = "succeeded"
    final_exit = 0
    resolved_repo = repo or ""
    resolved_change_id = change_id or ""
    resolved_model = model
    stage_state: dict[str, str | None] = {
        "current_stage": None,
        "failed_stage": None,
        "last_completed_stage": None,
    }
    try:
        workflow_input = resolve_workflow_input(
            repo=repo,
            change_id=change_id,
            ado_url=ado_url,
            story_file=story_file,
        )
        use_runner_root()
        resolved_repo = workflow_input.repo
        resolved_change_id = workflow_input.change_id
        intake_mode = workflow_input.intake_mode
        intake_source = workflow_input.intake_source

        logger.info(
            "main: resolved change_id=%s repo=%s intake_mode=%s",
            resolved_change_id, resolved_repo, intake_mode,
        )

        clean_workspace(resolved_change_id)

        print(f"Running workflow for {resolved_change_id}")
        print(f"Target repo: {resolved_repo}")
        print(f"Intake mode: {intake_mode}")
        print(f"Intake source: {intake_source}")
        print(f"Runner: {runner}")
        resolved_model = model or (
            copilot_alias_model(runner) if is_copilot_runner(runner) and runner != "copilot"
            else RUNNER_DEFAULT_MODELS[canonical_runner(runner)]
        )
        runner_model_kwargs: dict = {"runner_model": resolved_model}
        if copilot_effort is not None:
            runner_model_kwargs["copilot_effort"] = copilot_effort
        print(f"Model: {resolved_model}")
        if copilot_effort:
            print(f"Copilot effort: {copilot_effort}")

        logger.info("main: runner=%s model=%s copilot_effort=%s", runner, resolved_model, copilot_effort)
        loop_iter_count = 1 if calibration_fast_mode else 3
        if calibration_fast_mode:
            print("Calibration fast mode enabled (single-iteration workflow loops).")

        _emit(
            "job.start",
            change_id=resolved_change_id,
            repo=resolved_repo,
            runner=runner,
            model=resolved_model,
            intake_mode=intake_mode,
            copilot_effort=copilot_effort,
        )

        # Cooperative cancellation: when SIGTERM arrives (server cancel), emit
        # a job.end and exit cleanly so the tailer knows we're done.
        def _on_term(signum, frame):  # noqa: ARG001
            logger.warning("main: SIGTERM received — emitting job.end cancelled and exiting 143")
            if resolved_change_id:
                _write_workflow_status(
                    change_id=resolved_change_id,
                    status="cancelled",
                    stage_state=stage_state,
                    runner=runner,
                    model=resolved_model,
                    repo=resolved_repo,
                    exit_code=143,
                )
            _emit("job.end", status="cancelled", exit_code=143)
            sys.exit(143)

        try:
            signal.signal(signal.SIGTERM, _on_term)
            logger.debug("main: SIGTERM handler installed")
        except (ValueError, OSError) as exc:
            logger.debug("main: could not install SIGTERM handler: %s", exc)

        # ── Stage 0: Materialize agents + skills ──────────────────────────────
        with _Stage("materialize", state=stage_state):
            if not skip_materialize:
                logger.info("main: materializing agents and skills from source trees")
                print("Materializing agents and skills from source trees...")
                run_materialization()
            else:
                logger.info("main: skipping materialization (--skip-materialize)")
                print("Skipping materialization (--skip-materialize).")

        # ── Stage 1: Intake ───────────────────────────────────────────────────
        with _Stage("intake", state=stage_state):
            logger.info("main: intake source=%s mode=%s", intake_source, intake_mode)
            result_intake = steps.step_intake(
                intake_source=intake_source,
                repo=resolved_repo,
                change_id=resolved_change_id,
                intake_mode=intake_mode,
                runner=runner,
                extra_context=extra_context,
                **runner_model_kwargs,
            )
            logger.debug("main: intake complete, result length=%d", len(result_intake or ""))

    # ── Stage 2: Task Generation (eval-optimizer loop) ───────────────────────
        with _Stage("task-generation", state=stage_state):
            task_gen_input = (
                f"Generate a task plan for change {resolved_change_id} in {resolved_repo}.\n"
                f"Read the intake artifacts from {AGENT_CONTEXT_ROOT}/{resolved_change_id}/intake/.\n"
                f"Act immediately. Do not ask questions."
            )
            task_gen_evaluator_prompt = (
                f"Evaluate the task plan for {resolved_change_id} in {resolved_repo}. "
                f"Read {AGENT_CONTEXT_ROOT}/{resolved_change_id}/planning/tasks.yaml."
            )
            logger.debug("main: running task-generation eval-optimizer loop")
            run_eval_optimizer_loop(
                producer_func=steps.step_task_gen_producer,
                producer_input=task_gen_input,
                evaluator_func=steps.step_task_gen_evaluator,
                evaluator_prompt=task_gen_evaluator_prompt,
                iter_count=loop_iter_count,
                runner=runner,
                **runner_model_kwargs,
            )

        # ── Stage 3: Task Assignment (eval-optimizer loop) ────────────────────
        with _Stage("task-assignment", state=stage_state):
            assigner_input = (
                f"Create an execution schedule for change {resolved_change_id}.\n"
                f"Read tasks from {AGENT_CONTEXT_ROOT}/{resolved_change_id}/planning/tasks.yaml.\n"
                f"Read story context from {AGENT_CONTEXT_ROOT}/{resolved_change_id}/intake/story.yaml.\n"
                f"Read constraints from {AGENT_CONTEXT_ROOT}/{resolved_change_id}/intake/constraints.md.\n"
                f"Target repo: {resolved_repo}\n"
                f"Act immediately. Do not ask questions."
            )
            assignment_evaluator_prompt = (
                f"Evaluate the execution schedule for {resolved_change_id}. "
                f"Read {AGENT_CONTEXT_ROOT}/{resolved_change_id}/planning/assignments.json and "
                f"{AGENT_CONTEXT_ROOT}/{resolved_change_id}/planning/tasks.yaml."
            )
            logger.debug("main: running task-assignment eval-optimizer loop")
            run_eval_optimizer_loop(
                producer_func=steps.step_task_assigner,
                producer_input=assigner_input,
                evaluator_func=steps.step_assignment_evaluator,
                evaluator_prompt=assignment_evaluator_prompt,
                iter_count=loop_iter_count,
                runner=runner,
                **runner_model_kwargs,
            )

        # ── Stage 4: Execution — per-batch, parallel where safe ──────────────
        with _Stage("execution", state=stage_state):
            assignments = load_assignments(resolved_change_id)
            batches = sorted(assignments.get("execution_schedule", []), key=lambda b: b["batch"])
            logger.info("main: execution stage — %d batch(es) to run", len(batches))

            for batch in batches:
                uow_ids = [uow["uow_id"] for uow in batch.get("uows", [])]
                is_parallel = batch.get("parallel_execution", False)
                logger.info(
                    "main: batch %s — UoWs=%s parallel=%s",
                    batch["batch"], uow_ids, is_parallel,
                )
                print(f"Executing batch {batch['batch']} — UoWs: {uow_ids} (parallel={is_parallel})")

                if is_parallel and len(uow_ids) > 1:
                    logger.info("main: running %d UoW(s) in parallel for batch %s", len(uow_ids), batch["batch"])
                    with ThreadPoolExecutor() as executor:
                        futures = [
                            executor.submit(
                                run_uow_eval_loop,
                                uow_id=uid,
                                change_id=resolved_change_id,
                                repo=resolved_repo,
                                iter_count=loop_iter_count,
                                runner=runner,
                                **runner_model_kwargs,
                            )
                            for uid in uow_ids
                        ]
                        for future in futures:
                            future.result()
                    logger.info("main: parallel batch %s complete", batch["batch"])
                else:
                    for uid in uow_ids:
                        logger.info("main: running UoW %s (sequential)", uid)
                        run_uow_eval_loop(
                            uow_id=uid,
                            change_id=resolved_change_id,
                            repo=resolved_repo,
                            iter_count=loop_iter_count,
                            runner=runner,
                            **runner_model_kwargs,
                        )
                        logger.info("main: UoW %s complete", uid)

        # ── Stage 5: QA Validation (eval-optimizer loop) ─────────────────────
        with _Stage("qa", state=stage_state):
            qa_producer_input = (
                f"Perform QA validation for change {resolved_change_id}.\n"
                f"Read story ACs from {AGENT_CONTEXT_ROOT}/{resolved_change_id}/intake/story.yaml.\n"
                f"Read task plan from {AGENT_CONTEXT_ROOT}/{resolved_change_id}/planning/tasks.yaml.\n"
                f"Read assignments from {AGENT_CONTEXT_ROOT}/{resolved_change_id}/planning/assignments.json.\n"
                f"Read all implementation reports from {AGENT_CONTEXT_ROOT}/{resolved_change_id}/execution/*/impl_report.yaml.\n"
                f"Target repo: {resolved_repo}\n"
                f"Write your report to {AGENT_CONTEXT_ROOT}/{resolved_change_id}/qa/qa_report.yaml.\n"
                f"Act immediately. Do not ask questions."
            )
            qa_evaluator_prompt = (
                f"Evaluate the QA report for {resolved_change_id}. "
                f"Read {AGENT_CONTEXT_ROOT}/{resolved_change_id}/qa/qa_report.yaml and "
                f"{AGENT_CONTEXT_ROOT}/{resolved_change_id}/intake/story.yaml."
            )
            logger.debug("main: running qa eval-optimizer loop")
            run_eval_optimizer_loop(
                producer_func=steps.step_qa_engineer,
                producer_input=qa_producer_input,
                evaluator_func=steps.step_qa_evaluator,
                evaluator_prompt=qa_evaluator_prompt,
                iter_count=loop_iter_count,
                runner=runner,
                **runner_model_kwargs,
            )

        # ── Stage 6: Lessons Optimization (one-shot) ─────────────────────────
        if skip_lessons_optimizer:
            logger.info("main: skipping lessons optimizer (--skip-lessons-optimizer)")
            print("Skipping lessons optimizer (--skip-lessons-optimizer).")
        else:
            with _Stage("lessons-optimizer", state=stage_state):
                logger.debug("main: running lessons optimizer")
                steps.step_lessons_optimizer(
                    change_id=resolved_change_id,
                    repo=resolved_repo,
                    runner=runner,
                    **runner_model_kwargs,
                )

    except SystemExit:
        raise
    except BaseException as exc:
        final_status = "failed"
        final_exit = 1
        failure_summary = _summarize_exception(exc)
        logger.exception("main: workflow FAILED — %s: %s", type(exc).__name__, failure_summary)
        if resolved_change_id:
            _write_workflow_status(
                change_id=resolved_change_id,
                status=final_status,
                stage_state=stage_state,
                runner=runner,
                model=resolved_model,
                repo=resolved_repo,
                exit_code=final_exit,
                exc=exc,
            )
        _emit("log", level="error", kind="workflow_failed", msg=f"{type(exc).__name__}: {failure_summary}"[:1000])
        _emit("job.end", status=final_status, exit_code=final_exit)
        raise
    else:
        logger.info("main: workflow SUCCEEDED change_id=%s", resolved_change_id)
        if resolved_change_id:
            _write_workflow_status(
                change_id=resolved_change_id,
                status=final_status,
                stage_state=stage_state,
                runner=runner,
                model=resolved_model,
                repo=resolved_repo,
                exit_code=final_exit,
            )
        _emit("job.end", status=final_status, exit_code=final_exit)

    return intake_source


main.fn = main

if __name__ == "__main__":
    args = parse_args()
    main(
        repo=args.repo,
        change_id=args.change_id,
        ado_url=args.ado_url,
        story_file=args.story_file,
        runner=args.runner,
        skip_materialize=args.skip_materialize,
        model=args.model,
        copilot_effort=args.copilot_effort,
        extra_context=args.extra_context,
        skip_lessons_optimizer=args.skip_lessons_optimizer,
        calibration_fast_mode=args.calibration_fast_mode,
    )
