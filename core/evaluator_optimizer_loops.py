import logging
import re

from opik import opik_context

from . import steps
from .runner_models import DEFAULT_GEMINI_MODEL
from .ui_trace_bridge import start_span_with_ui, track_with_ui

logger = logging.getLogger(__name__)


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
    for i in range(iter_count):
        logger.info("run_uow_eval_loop: iteration %d/%d uow_id=%s", i + 1, iter_count, uow_id)
        with start_span_with_ui(
            f"uow-iteration-{i + 1}",
            type="general",
            metadata={
                "runner": runner,
                "change_id": change_id,
                "stage": "uow-eval",
                "uow_id": uow_id,
                "iteration": i + 1,
            },
        ) as span:
            span.input = {"uow_id": uow_id, "iteration": i + 1}
            producer_out = steps.step_software_engineer(
                uow_id=uow_id,
                change_id=change_id,
                repo=repo,
                evaluator_feedback=evaluator_out if i > 0 else "",
                runner=runner,
                runner_model=runner_model,
                            )
            evaluator_out = steps.step_software_engineer_evaluator(
                uow_id=uow_id,
                change_id=change_id,
                repo=repo,
                runner=runner,
                runner_model=runner_model,
                            )
            passed = "PASS" in evaluator_out
            logger.info("run_uow_eval_loop: iteration %d/%d uow_id=%s passed=%s", i + 1, iter_count, uow_id, passed)
            span.output = {"passed": passed}
            try:
                opik_context.update_current_span(
                    metadata={
                        "iteration": i + 1,
                        "attempt": i + 1,
                        "uow_id": uow_id,
                        "change_id": change_id,
                        "stage": "uow-eval",
                        "passed": passed,
                    },
                )
            except Exception:
                pass
        if passed:
            logger.info("run_uow_eval_loop: uow_id=%s PASSED on iteration %d — stopping early", uow_id, i + 1)
            print(f"[{uow_id}] Evaluator passed on iteration {i + 1} — stopping loop early.")
            break
    else:
        logger.warning("run_uow_eval_loop: uow_id=%s did NOT pass after %d iteration(s)", uow_id, iter_count)
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
):
    change_id = _extract_change_id(producer_input) or _extract_change_id(evaluator_prompt)
    logger.info(
        "run_eval_optimizer_loop: START producer=%s evaluator=%s change_id=%s runner=%s iter_count=%d",
        getattr(producer_func, "__name__", str(producer_func)),
        getattr(evaluator_func, "__name__", str(evaluator_func)),
        change_id, runner, iter_count,
    )
    _annotate_loop_trace(runner=runner, change_id=change_id, stage="eval-optimizer")
    producer_out, evaluator_out = "", ""

    for i in range(iter_count):
        logger.info("run_eval_optimizer_loop: iteration %d/%d change_id=%s", i + 1, iter_count, change_id)
        with start_span_with_ui(
            f"eval-optimizer-iteration-{i + 1}",
            type="general",
            metadata={
                "runner": runner,
                "change_id": change_id,
                "stage": "eval-optimizer",
                "iteration": i + 1,
            },
        ) as span:
            span.input = {"iteration": i + 1}
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
                logger.debug("run_eval_optimizer_loop: iteration %d injecting evaluator feedback (len=%d)", i + 1, len(evaluator_out))
            producer_out = producer_func(combined_input, runner=runner, runner_model=runner_model)
            evaluator_out = evaluator_func(evaluator_prompt, runner=runner, runner_model=runner_model)
            passed = "PASS" in evaluator_out
            logger.info("run_eval_optimizer_loop: iteration %d/%d change_id=%s passed=%s", i + 1, iter_count, change_id, passed)
            span.output = {"passed": passed}
            try:
                opik_context.update_current_span(
                    metadata={
                        "iteration": i + 1,
                        "attempt": i + 1,
                        "change_id": change_id,
                        "stage": "eval-optimizer",
                        "passed": passed,
                    },
                )
            except Exception:
                pass
        if passed:
            logger.info("run_eval_optimizer_loop: PASSED on iteration %d — stopping early", i + 1)
            print(f"Evaluator passed on iteration {i + 1} — stopping loop early.")
            break
    else:
        logger.warning("run_eval_optimizer_loop: did NOT pass after %d iteration(s) for change_id=%s", iter_count, change_id)

    logger.info("run_eval_optimizer_loop: DONE change_id=%s", change_id)
    return producer_out, evaluator_out

