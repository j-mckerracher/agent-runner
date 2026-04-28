import re

import opik
from opik import opik_context

import steps
from runner_models import DEFAULT_GEMINI_MODEL


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


@opik.track(name="loop:uow-eval", type="general")
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
    _annotate_loop_trace(
        runner=runner,
        change_id=change_id,
        stage="uow-eval",
        extra_metadata={"uow_id": uow_id},
    )
    producer_out, evaluator_out = "", ""
    for i in range(iter_count):
        with opik.start_as_current_span(f"uow-iteration-{i + 1}", type="general") as span:
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
            print(f"[{uow_id}] Evaluator passed on iteration {i + 1} — stopping loop early.")
            break
    return producer_out, evaluator_out

# ====================== EVAL-OPTIMIZER LOOP ====================== #

@opik.track(name="loop:eval-optimizer", type="general")
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
    _annotate_loop_trace(runner=runner, change_id=change_id, stage="eval-optimizer")
    producer_out, evaluator_out = "", ""

    for i in range(iter_count):
        with opik.start_as_current_span(f"eval-optimizer-iteration-{i + 1}", type="general") as span:
            span.input = {"iteration": i + 1}
            if i == 0 or not evaluator_out:
                combined_input = producer_input
            else:
                combined_input = (
                    f"{producer_input}\n\n"
                    f"## Evaluator Issues to Fix (iteration {i}):\n{evaluator_out}\n\n"
                    f"Revise your output artifact to address the issues above. Do not ask questions — act immediately."
                )
            producer_out = producer_func(combined_input, runner=runner, runner_model=runner_model)
            evaluator_out = evaluator_func(evaluator_prompt, runner=runner, runner_model=runner_model)
            passed = "PASS" in evaluator_out
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
            print(f"Evaluator passed on iteration {i + 1} — stopping loop early.")
            break

    return producer_out, evaluator_out
