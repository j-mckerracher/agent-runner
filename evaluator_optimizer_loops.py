import opik
from prefect import task, flow
import steps
from runner_models import DEFAULT_GEMINI_MODEL

@task(log_prints=True, name="run-uow-eval-loop", timeout_seconds=3600)
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
            opik.opik_context.update_current_span(
                metadata={"iteration": i + 1, "uow_id": uow_id, "passed": passed},
            )
        if passed:
            print(f"[{uow_id}] Evaluator passed on iteration {i + 1} — stopping loop early.")
            break
    return producer_out, evaluator_out

# ====================== EVAL-OPTIMIZER LOOP ====================== #

@flow(log_prints=True, timeout_seconds=1800)
def run_eval_optimizer_loop(
    producer_func,
    producer_input,
    evaluator_func,
    evaluator_prompt,
    iter_count: int = 3,
    runner: str = "claude",
    runner_model: str | None = DEFAULT_GEMINI_MODEL,
):
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
            opik.opik_context.update_current_span(
                metadata={"iteration": i + 1, "passed": passed},
            )
        if passed:
            print(f"Evaluator passed on iteration {i + 1} — stopping loop early.")
            break

    return producer_out, evaluator_out
