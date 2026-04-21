from prefect import task, flow
import steps

@task(log_prints=True, name="run-uow-eval-loop", timeout_seconds=3600)
def run_uow_eval_loop(uow_id: str, change_id: str, repo: str, iter_count: int = 3) -> tuple[str, str]:
    """
    Run the software-engineer + implementation-evaluator eval-optimizer loop
    for a single Unit of Work. Returns (final_impl_out, final_eval_out).
    """
    producer_out, evaluator_out = "", ""
    for i in range(iter_count):
        producer_out = steps.step_software_engineer(
            uow_id=uow_id,
            change_id=change_id,
            repo=repo,
            evaluator_feedback=evaluator_out if i > 0 else "",
        )
        evaluator_out = steps.step_software_engineer_evaluator(
            uow_id=uow_id,
            change_id=change_id,
            repo=repo,
        )
        if "PASS" in evaluator_out:
            print(f"[{uow_id}] Evaluator passed on iteration {i + 1} — stopping loop early.")
            break
    return producer_out, evaluator_out

# ====================== EVAL-OPTIMIZER LOOP ====================== #

@flow(log_prints=True, timeout_seconds=1800)
def run_eval_optimizer_loop(producer_func, producer_input, evaluator_func, evaluator_prompt, iter_count: int = 3):
    producer_out, evaluator_out = "", ""

    for i in range(iter_count):
        if i == 0 or not evaluator_out:
            combined_input = producer_input
        else:
            combined_input = (
                f"{producer_input}\n\n"
                f"## Evaluator Issues to Fix (iteration {i}):\n{evaluator_out}\n\n"
                f"Revise your output artifact to address the issues above. Do not ask questions — act immediately."
            )
        producer_out = producer_func(combined_input)
        # Evaluator always reads the artifact file directly, not the producer's stdout.
        evaluator_out = evaluator_func(evaluator_prompt)
        if "PASS" in evaluator_out:
            print(f"Evaluator passed on iteration {i + 1} — stopping loop early.")
            break

    return producer_out, evaluator_out