import json
import logging
import os
import re
import time
from pathlib import Path

from .opik_compat import opik_context

from . import steps
from .artifact_utils import normalize_impl_report_file, snapshot_impl_report_attempt, validate_impl_report_alignment
from .runner_models import DEFAULT_GEMINI_MODEL
from .ui_trace_bridge import start_span_with_ui, track_with_ui

logger = logging.getLogger(__name__)


def _extract_json_object(text: str) -> dict | None:
    stripped = (text or "").strip()
    if not stripped:
        return None
    candidates = [stripped]
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fence_match:
        candidates.insert(0, fence_match.group(1).strip())
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _persist_impl_evaluator_feedback(*, change_id: str, uow_id: str, iteration: int, evaluator_out: str) -> Path:
    uow_dir = steps.AGENT_CONTEXT_ROOT / change_id / "execution" / uow_id
    path = uow_dir / f"eval_impl_{iteration}.json"
    payload = _extract_json_object(evaluator_out)
    if payload is None:
        payload = {
            "artifact_evaluated": "impl_report.yaml",
            "status": "PASS" if "PASS" in (evaluator_out or "") else "FAIL",
            "raw_response": evaluator_out or "",
        }
    else:
        payload.setdefault("artifact_evaluated", "impl_report.yaml")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _extract_change_id(text: str) -> str:
    if not text:
        return ""
    match = re.search(r"agent-context/([\w\-]+)/", text)
    return match.group(1) if match else ""


def _annotate_loop_trace(*, runner: str, change_id: str, stage: str, extra_metadata: dict | None = None) -> None:
    metadata = {"change_id": change_id, "runner": runner, "stage": stage}
    if extra_metadata:
        metadata.update({k: v for k, v in extra_metadata.items() if v is not None})
    tags = [runner, f"loop:{stage}"]
    try:
        kwargs: dict = {"tags": tags, "metadata": metadata}
        if change_id:
            kwargs["thread_id"] = change_id
        opik_context.update_current_trace(**kwargs)
    except Exception:
        pass


def _emit_loop_event(type: str, **fields: object) -> None:
    if not os.environ.get("AGENT_RUNNER_EVENT_LOG"):
        return
    try:
        from server.events import emit
        stage = fields.get("stage") or os.environ.get("AGENT_RUNNER_CURRENT_STAGE")
        if stage:
            fields["stage"] = stage
        emit(type, **fields)
    except Exception:
        pass


def _loop_trace_metadata(*, runner: str, change_id: str, stage: str, **extra: object) -> dict[str, object]:
    metadata: dict[str, object] = {"runner": runner, "change_id": change_id, "stage": stage}
    metadata.update({key: value for key, value in extra.items() if value is not None})
    return metadata


@track_with_ui(
    name="loop:uow-eval",
    type="general",
    metadata_getter=lambda uow_id, change_id, repo, iter_count=3, runner="claude", **_unused: _loop_trace_metadata(
        runner=runner,
        change_id=change_id,
        stage="uow-eval",
        uow_id=uow_id,
        iter_count=iter_count,
    ),
)
def run_uow_eval_loop(
    uow_id: str,
    change_id: str,
    repo: str,
    iter_count: int = 3,
    runner: str = "claude",
    runner_model: str | None = DEFAULT_GEMINI_MODEL,
    evaluator_runner: str | None = None,
    evaluator_runner_model: str | None = None,
) -> tuple[str, str]:
    """
    Run the software-engineer + implementation-evaluator eval-optimizer loop
    for a single Unit of Work. Returns (final_impl_out, final_eval_out).
    """
    logger.info("run_uow_eval_loop: START uow_id=%s change_id=%s runner=%s iter_count=%d", uow_id, change_id, runner, iter_count)
    _annotate_loop_trace(
        runner=runner,
        change_id=change_id,
        stage="uow-eval",
        extra_metadata={"uow_id": uow_id},
    )
    producer_out, evaluator_out = "", ""
    evaluator_feedback_path: Path | None = None
    effective_evaluator_runner = evaluator_runner or runner
    effective_evaluator_model = evaluator_runner_model if evaluator_runner_model is not None else runner_model
    actual_iterations = 0
    passed = False
    loop_started = time.perf_counter()
    _emit_loop_event(
        "loop.start",
        loop_name="uow-eval",
        stage="uow-eval",
        uow_id=uow_id,
        change_id=change_id,
        max_iterations=iter_count,
        runner=runner,
        model=runner_model,
    )
    try:
        for i in range(iter_count):
            iteration = i + 1
            actual_iterations = iteration
            iteration_started = time.perf_counter()
            logger.info("run_uow_eval_loop: iteration %d/%d uow_id=%s", iteration, iter_count, uow_id)
            _emit_loop_event(
                "loop.iteration.start",
                loop_name="uow-eval",
                stage="uow-eval",
                uow_id=uow_id,
                change_id=change_id,
                iteration=iteration,
                max_iterations=iter_count,
                runner=runner,
                model=runner_model,
            )
            with start_span_with_ui(
                f"uow-iteration-{iteration}",
                type="general",
                metadata={
                    "runner": runner,
                    "change_id": change_id,
                    "stage": "uow-eval",
                    "uow_id": uow_id,
                    "iteration": iteration,
                },
            ) as span:
                span.input = {"uow_id": uow_id, "iteration": iteration}
                producer_out = steps.step_software_engineer(
                    uow_id=uow_id,
                    change_id=change_id,
                    repo=repo,
                    evaluator_feedback=evaluator_out if i > 0 else "",
                    evaluator_feedback_path=str(evaluator_feedback_path) if i > 0 and evaluator_feedback_path else "",
                    runner=runner,
                    runner_model=runner_model,
                )
                normalize_impl_report_file(
                    steps.AGENT_CONTEXT_ROOT / change_id / "execution" / uow_id / "impl_report.yaml"
                )
                snapshot_impl_report_attempt(
                    agent_context_root=steps.AGENT_CONTEXT_ROOT,
                    change_id=change_id,
                    uow_id=uow_id,
                    attempt=iteration,
                )
                validate_impl_report_alignment(
                    agent_context_root=steps.AGENT_CONTEXT_ROOT,
                    change_id=change_id,
                    uow_id=uow_id,
                )
                evaluator_out = steps.step_software_engineer_evaluator(
                    uow_id=uow_id,
                    change_id=change_id,
                    repo=repo,
                    runner=effective_evaluator_runner,
                    runner_model=effective_evaluator_model,
                )
                evaluator_feedback_path = _persist_impl_evaluator_feedback(
                    change_id=change_id,
                    uow_id=uow_id,
                    iteration=iteration,
                    evaluator_out=evaluator_out,
                )
                passed = "PASS" in evaluator_out
                logger.info("run_uow_eval_loop: iteration %d/%d uow_id=%s passed=%s", iteration, iter_count, uow_id, passed)
                span.output = {"passed": passed}
                try:
                    opik_context.update_current_span(
                        metadata={
                            "iteration": iteration,
                            "attempt": iteration,
                            "uow_id": uow_id,
                            "change_id": change_id,
                            "stage": "uow-eval",
                            "passed": passed,
                        },
                    )
                except Exception:
                    pass
            _emit_loop_event(
                "loop.iteration.end",
                loop_name="uow-eval",
                stage="uow-eval",
                uow_id=uow_id,
                change_id=change_id,
                iteration=iteration,
                max_iterations=iter_count,
                passed=passed,
                duration_ms=int((time.perf_counter() - iteration_started) * 1000),
                runner=runner,
                model=runner_model,
            )
            if passed:
                logger.info("run_uow_eval_loop: uow_id=%s PASSED on iteration %d — stopping early", uow_id, iteration)
                print(f"[{uow_id}] Evaluator passed on iteration {iteration} — stopping loop early.")
                break
        else:
            logger.warning("run_uow_eval_loop: uow_id=%s did NOT pass after %d iteration(s)", uow_id, iter_count)
    except Exception:
        _emit_loop_event(
            "loop.end",
            loop_name="uow-eval",
            stage="uow-eval",
            uow_id=uow_id,
            change_id=change_id,
            actual_iterations=actual_iterations,
            max_iterations=iter_count,
            passed=False,
            stopped_early=False,
            exhausted=False,
            status="error",
            duration_ms=int((time.perf_counter() - loop_started) * 1000),
            runner=runner,
            model=runner_model,
        )
        raise
    _emit_loop_event(
        "loop.end",
        loop_name="uow-eval",
        stage="uow-eval",
        uow_id=uow_id,
        change_id=change_id,
        actual_iterations=actual_iterations,
        max_iterations=iter_count,
        passed=passed,
        stopped_early=passed,
        exhausted=actual_iterations >= iter_count and not passed,
        status="ok",
        duration_ms=int((time.perf_counter() - loop_started) * 1000),
        runner=runner,
        model=runner_model,
    )
    logger.info("run_uow_eval_loop: DONE uow_id=%s", uow_id)
    return producer_out, evaluator_out


# ====================== EVAL-OPTIMIZER LOOP ====================== #

@track_with_ui(
    name="loop:eval-optimizer",
    type="general",
    metadata_getter=lambda producer_func, producer_input, evaluator_func, evaluator_prompt, iter_count=3, runner="claude", **_unused: _loop_trace_metadata(
        runner=runner,
        change_id=_extract_change_id(producer_input) or _extract_change_id(evaluator_prompt),
        stage="eval-optimizer",
        iter_count=iter_count,
    ),
)
def run_eval_optimizer_loop(
    producer_func,
    producer_input,
    evaluator_func,
    evaluator_prompt,
    iter_count: int = 3,
    runner: str = "claude",
    runner_model: str | None = DEFAULT_GEMINI_MODEL,
    evaluator_runner: str | None = None,
    evaluator_runner_model: str | None = None,
):
    change_id = _extract_change_id(producer_input) or _extract_change_id(evaluator_prompt)
    effective_evaluator_runner = evaluator_runner or runner
    effective_evaluator_model = evaluator_runner_model if evaluator_runner_model is not None else runner_model
    logger.info(
        "run_eval_optimizer_loop: START producer=%s evaluator=%s change_id=%s runner=%s iter_count=%d",
        getattr(producer_func, "__name__", str(producer_func)),
        getattr(evaluator_func, "__name__", str(evaluator_func)),
        change_id, runner, iter_count,
    )
    _annotate_loop_trace(runner=runner, change_id=change_id, stage="eval-optimizer")
    producer_out, evaluator_out = "", ""
    actual_iterations = 0
    passed = False
    loop_started = time.perf_counter()
    _emit_loop_event(
        "loop.start",
        loop_name="eval-optimizer",
        stage="eval-optimizer",
        change_id=change_id,
        max_iterations=iter_count,
        runner=runner,
        model=runner_model,
    )
    try:
        for i in range(iter_count):
            iteration = i + 1
            actual_iterations = iteration
            iteration_started = time.perf_counter()
            logger.info("run_eval_optimizer_loop: iteration %d/%d change_id=%s", iteration, iter_count, change_id)
            _emit_loop_event(
                "loop.iteration.start",
                loop_name="eval-optimizer",
                stage="eval-optimizer",
                change_id=change_id,
                iteration=iteration,
                max_iterations=iter_count,
                runner=runner,
                model=runner_model,
            )
            with start_span_with_ui(
                f"eval-optimizer-iteration-{iteration}",
                type="general",
                metadata={
                    "runner": runner,
                    "change_id": change_id,
                    "stage": "eval-optimizer",
                    "iteration": iteration,
                },
            ) as span:
                span.input = {"iteration": iteration}
                if i == 0 or not evaluator_out:
                    combined_input = producer_input
                else:
                    combined_input = (
                        f"{producer_input}\n\n"
                        f"## Evaluator Issues to Fix (iteration {i}):\n{evaluator_out}\n\n"
                        f"Revise your output artifact to address the issues above. "
                        f"If resolving an issue requires a blocking product decision or user-only clarification, "
                        f"use the user escalation protocol and continue after receiving the response."
                    )
                    logger.debug("run_eval_optimizer_loop: iteration %d injecting evaluator feedback (len=%d)", iteration, len(evaluator_out))
                producer_out = producer_func(combined_input, runner=runner, runner_model=runner_model)
                evaluator_out = evaluator_func(
                    evaluator_prompt,
                    runner=effective_evaluator_runner,
                    runner_model=effective_evaluator_model,
                )
                passed = "PASS" in evaluator_out
                logger.info("run_eval_optimizer_loop: iteration %d/%d change_id=%s passed=%s", iteration, iter_count, change_id, passed)
                span.output = {"passed": passed}
                try:
                    opik_context.update_current_span(
                        metadata={
                            "iteration": iteration,
                            "attempt": iteration,
                            "change_id": change_id,
                            "stage": "eval-optimizer",
                            "passed": passed,
                        },
                    )
                except Exception:
                    pass
            _emit_loop_event(
                "loop.iteration.end",
                loop_name="eval-optimizer",
                stage="eval-optimizer",
                change_id=change_id,
                iteration=iteration,
                max_iterations=iter_count,
                passed=passed,
                duration_ms=int((time.perf_counter() - iteration_started) * 1000),
                runner=runner,
                model=runner_model,
            )
            if passed:
                logger.info("run_eval_optimizer_loop: PASSED on iteration %d — stopping early", iteration)
                print(f"Evaluator passed on iteration {iteration} — stopping loop early.")
                break
        else:
            logger.warning("run_eval_optimizer_loop: did NOT pass after %d iteration(s) for change_id=%s", iter_count, change_id)
    except Exception:
        _emit_loop_event(
            "loop.end",
            loop_name="eval-optimizer",
            stage="eval-optimizer",
            change_id=change_id,
            actual_iterations=actual_iterations,
            max_iterations=iter_count,
            passed=False,
            stopped_early=False,
            exhausted=False,
            status="error",
            duration_ms=int((time.perf_counter() - loop_started) * 1000),
            runner=runner,
            model=runner_model,
        )
        raise
    _emit_loop_event(
        "loop.end",
        loop_name="eval-optimizer",
        stage="eval-optimizer",
        change_id=change_id,
        actual_iterations=actual_iterations,
        max_iterations=iter_count,
        passed=passed,
        stopped_early=passed,
        exhausted=actual_iterations >= iter_count and not passed,
        status="ok",
        duration_ms=int((time.perf_counter() - loop_started) * 1000),
        runner=runner,
        model=runner_model,
    )

    logger.info("run_eval_optimizer_loop: DONE change_id=%s", change_id)
    return producer_out, evaluator_out
