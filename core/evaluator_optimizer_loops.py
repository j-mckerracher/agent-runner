import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Callable

from .opik_compat import opik_context

from . import steps
from .artifact_utils import (
    ImplReportValidationError,
    normalize_impl_report_file,
    snapshot_impl_report_attempt,
    validate_impl_report_alignment,
)
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


def _format_evaluator_feedback_for_retry(evaluator_out: str, *, limit: int = 6000) -> str:
    """Keep retry prompts focused on actionable evaluator findings."""
    raw = (evaluator_out or "").strip()
    if not raw:
        return ""

    payload = _extract_json_object(raw)
    if payload is not None:
        selected: dict[str, object] = {}
        for key in (
            "overall_result",
            "status",
            "score",
            "summary",
            "issues",
            "findings",
            "required_changes",
            "recommendations",
            "programmatic_gates",
            "gate_failure_details",
        ):
            if key in payload:
                selected[key] = payload[key]
        if selected:
            return json.dumps(selected, indent=2, ensure_ascii=False)[:limit]

    cleaned = re.sub(r"<tool_call>.*?</tool_call>", "", raw, flags=re.DOTALL)
    cleaned = re.sub(r"<tool_response>.*?</tool_response>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"(?is)^.*?Full rubric analysis", "Full rubric analysis", cleaned)
    cleaned = re.sub(r"(?is)Now let me write the full evaluation:.*$", "", cleaned)
    cleaned = "\n".join(line.rstrip() for line in cleaned.splitlines())
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rstrip() + "\n\n[feedback truncated]"
    return cleaned


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


def _impl_report_artifact_failure_feedback(*, change_id: str, uow_id: str, error: Exception) -> str:
    report_path = steps.AGENT_CONTEXT_ROOT / change_id / "execution" / uow_id / "impl_report.yaml"
    payload = {
        "artifact_evaluated": "impl_report.yaml",
        "status": "FAIL",
        "summary": "The software-engineer invocation did not produce a valid implementation report.",
        "issues": [
            {
                "severity": "blocker",
                "description": str(error),
            }
        ],
        "required_changes": [
            f"Write the required implementation report to {report_path} before ending the turn.",
            "Do not leave test, lint, or build commands pending; include completed verification evidence or document a blocker.",
        ],
        "programmatic_gates": [
            {
                "name": "impl_report_artifact_validation",
                "status": "FAIL",
                "details": str(error),
            }
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _retryable_impl_report_error(exc: ImplReportValidationError) -> ImplReportValidationError | None:
    # A missing/invalid uow_spec.yaml is upstream corruption (the task-assigner's
    # output), not something the software-engineer can fix by retrying — fail fast.
    # Everything else (missing / invalid / malformed / wrong-domain impl_report.yaml)
    # is a recoverable artifact failure the next iteration can address.
    if "UoW spec" in str(exc):
        return None
    return exc


def _artifact_missing(artifact: "Path | str | None") -> bool:
    """True when a required producer artifact is absent or empty.

    Detects the failure mode where a producer agent exits cleanly (CLI status 0)
    without writing its required output — e.g. it ended its turn early to "wait
    for test results". Returns False when no artifact path is supplied, so the
    guard stays opt-in per call site.
    """
    if artifact is None:
        return False
    path = Path(artifact)
    try:
        return (not path.is_file()) or path.stat().st_size == 0
    except OSError:
        return True


def _missing_producer_artifact_feedback(artifact: "Path | str") -> str:
    """Corrective evaluator-style feedback when a producer wrote no artifact.

    Returned as a JSON object so it flows through
    `_format_evaluator_feedback_for_retry` (which selects status / summary /
    required_changes) on the next iteration's retry prompt.
    """
    payload = {
        "artifact_evaluated": Path(artifact).name,
        "status": "FAIL",
        "summary": (
            f"The required artifact was not produced: {artifact}. "
            "Your previous turn ended before the artifact was written to disk."
        ),
        "required_changes": [
            f"Write {artifact} before ending your turn.",
            "Finish all work in a single turn. If you start tests or other long-running "
            "commands, wait for them to complete and capture their results yourself. Do NOT "
            "end your turn saying you will 'wait for test results' — the session stops when "
            "you stop, so nothing runs after you end the turn.",
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


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
    runner_model: str | None = None,
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
                artifact_error: ImplReportValidationError | None = None
                producer_out = steps.step_software_engineer(
                    uow_id=uow_id,
                    change_id=change_id,
                    repo=repo,
                    evaluator_feedback=evaluator_out if i > 0 else "",
                    evaluator_feedback_path=str(evaluator_feedback_path) if i > 0 and evaluator_feedback_path else "",
                    runner=runner,
                    runner_model=runner_model,
                )
                try:
                    try:
                        normalize_impl_report_file(
                            steps.AGENT_CONTEXT_ROOT / change_id / "execution" / uow_id / "impl_report.yaml"
                        )
                    except ValueError as normalize_exc:
                        # normalize_impl_report_file raises a bare ValueError for
                        # malformed / non-mapping report YAML — a recoverable agent
                        # mistake. Promote it to a retryable artifact failure so it
                        # routes through the classifier like other report problems,
                        # while genuine ValueErrors from snapshot/validate below stay
                        # unmasked and propagate as real bugs.
                        raise ImplReportValidationError(str(normalize_exc)) from normalize_exc
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
                except ImplReportValidationError as exc:
                    artifact_error = _retryable_impl_report_error(exc)
                    if artifact_error is None:
                        raise
                    evaluator_out = _impl_report_artifact_failure_feedback(
                        change_id=change_id,
                        uow_id=uow_id,
                        error=artifact_error,
                    )
                    evaluator_feedback_path = _persist_impl_evaluator_feedback(
                        change_id=change_id,
                        uow_id=uow_id,
                        iteration=iteration,
                        evaluator_out=evaluator_out,
                    )
                    logger.warning(
                        "run_uow_eval_loop: iteration %d/%d uow_id=%s produced invalid impl_report artifact: %s",
                        iteration,
                        iter_count,
                        uow_id,
                        artifact_error,
                    )
                else:
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
                span.output = {"passed": passed, "artifact_error": str(artifact_error) if artifact_error else None}
                try:
                    opik_context.update_current_span(
                        metadata={
                            "iteration": iteration,
                            "attempt": iteration,
                            "uow_id": uow_id,
                            "change_id": change_id,
                            "stage": "uow-eval",
                            "passed": passed,
                            "artifact_error": bool(artifact_error),
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
                artifact_error=bool(artifact_error),
                error=str(artifact_error)[:500] if artifact_error else None,
            )
            if artifact_error is not None:
                if iteration >= iter_count:
                    raise artifact_error
                continue
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
    runner_model: str | None = None,
    evaluator_runner: str | None = None,
    evaluator_runner_model: str | None = None,
    on_exhausted: Callable[[str], None] | None = None,
    producer_artifact: "Path | str | None" = None,
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
                evaluator_feedback = _format_evaluator_feedback_for_retry(evaluator_out)
                combined_input = (
                    f"{producer_input}\n\n"
                    f"## Evaluator Issues to Fix (iteration {i}):\n{evaluator_feedback}\n\n"
                    f"Revise your output artifact to address the issues above. "
                    f"If resolving an issue requires a blocking product decision or user-only clarification, "
                    f"use the user escalation protocol and continue after receiving the response."
                )
                logger.debug(
                    "run_eval_optimizer_loop: iteration %d injecting evaluator feedback (raw_len=%d sanitized_len=%d)",
                    iteration,
                    len(evaluator_out),
                    len(evaluator_feedback),
                )
            producer_out = producer_func(combined_input, runner=runner, runner_model=runner_model)
            if _artifact_missing(producer_artifact):
                # The producer agent exited without writing its required artifact
                # (e.g. it ended its turn to "wait for test results"). Force a
                # deterministic retry with corrective feedback rather than trusting
                # the evaluator to notice — or crashing when it reads a missing file.
                logger.warning(
                    "run_eval_optimizer_loop: producer wrote no artifact %s (iteration %d/%d) — retrying",
                    producer_artifact, iteration, iter_count,
                )
                evaluator_out = _missing_producer_artifact_feedback(producer_artifact)
                passed = False
            else:
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
    if actual_iterations >= iter_count and not passed and on_exhausted is not None:
        on_exhausted(change_id)

    logger.info("run_eval_optimizer_loop: DONE change_id=%s", change_id)
    return producer_out, evaluator_out
