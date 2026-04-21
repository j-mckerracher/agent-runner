import json
import os
from prefect import flow, tags
from datetime import datetime
import steps
from evaluator_optimizer_loops import run_uow_eval_loop, run_eval_optimizer_loop

# ====================== HELPERS ====================== #

def get_time():
    now = datetime.now()
    return now.strftime("%H:%M %m/%d/%Y")

def load_assignments(change_id: str) -> dict:
    """Read assignments.json produced by the task-assigner."""
    path = os.path.join("agent-context", change_id, "planning", "assignments.json")
    with open(path, "r") as f:
        return json.load(f)

# ====================== MAIN ====================== #

@flow(log_prints=True, timeout_seconds=7200)
def main():
    ado_url = "https://dev.azure.com/mclm/Mayo%20Collaborative%20Services/_workitems/edit/5035632"
    repo = "/Users/mckerracher.joshua/Code/mcs-products-mono-ui"
    change_id = "WI-5035632"

    # ── Stage 1: Intake ──────────────────────────────────────────────────────
    result_intake = steps.step_intake(ado_story_link=ado_url, repo=repo)

    # ── Stage 2: Task Generation (eval-optimizer loop) ───────────────────────
    task_gen_evaluator_prompt = (
        f"Evaluate the task plan for {change_id} in {repo}. "
        f"Read agent-context/{change_id}/planning/tasks.yaml."
    )
    run_eval_optimizer_loop(
        producer_func=steps.step_task_gen_producer,
        producer_input=result_intake,
        evaluator_func=steps.step_task_gen_evaluator,
        evaluator_prompt=task_gen_evaluator_prompt,
    )

    # ── Stage 3: Task Assignment (eval-optimizer loop) ────────────────────────
    assigner_input = (
        f"Create an execution schedule for change {change_id}.\n"
        f"Read tasks from agent-context/{change_id}/planning/tasks.yaml.\n"
        f"Read story context from agent-context/{change_id}/intake/story.yaml.\n"
        f"Read constraints from agent-context/{change_id}/intake/constraints.md.\n"
        f"Target repo: {repo}\n"
        f"Act immediately. Do not ask questions."
    )
    assignment_evaluator_prompt = (
        f"Evaluate the execution schedule for {change_id}. "
        f"Read agent-context/{change_id}/planning/assignments.json and "
        f"agent-context/{change_id}/planning/tasks.yaml."
    )
    run_eval_optimizer_loop(
        producer_func=steps.step_task_assigner,
        producer_input=assigner_input,
        evaluator_func=steps.step_assignment_evaluator,
        evaluator_prompt=assignment_evaluator_prompt,
    )

    # ── Stage 4: Execution — per-batch, parallel where safe ──────────────────
    assignments = load_assignments(change_id)
    batches = sorted(assignments.get("execution_schedule", []), key=lambda b: b["batch"])

    for batch in batches:
        uow_ids = [uow["uow_id"] for uow in batch.get("uows", [])]
        is_parallel = batch.get("parallel_execution", False)
        print(f"Executing batch {batch['batch']} — UoWs: {uow_ids} (parallel={is_parallel})")

        if is_parallel and len(uow_ids) > 1:
            # Fan-out: submit all UoWs in the batch concurrently
            futures = [
                run_uow_eval_loop.submit(uow_id=uid, change_id=change_id, repo=repo)
                for uid in uow_ids
            ]
            # Wait for all UoWs in this batch to complete before advancing
            for future in futures:
                future.result()
        else:
            for uid in uow_ids:
                run_uow_eval_loop(uow_id=uid, change_id=change_id, repo=repo)

    # ── Stage 5: QA Validation (eval-optimizer loop) ─────────────────────────
    qa_producer_input = (
        f"Perform QA validation for change {change_id}.\n"
        f"Read story ACs from agent-context/{change_id}/intake/story.yaml.\n"
        f"Read task plan from agent-context/{change_id}/planning/tasks.yaml.\n"
        f"Read assignments from agent-context/{change_id}/planning/assignments.json.\n"
        f"Read all implementation reports from agent-context/{change_id}/execution/*/impl_report.yaml.\n"
        f"Target repo: {repo}\n"
        f"Write your report to agent-context/{change_id}/qa/qa_report.yaml.\n"
        f"Act immediately. Do not ask questions."
    )
    qa_evaluator_prompt = (
        f"Evaluate the QA report for {change_id}. "
        f"Read agent-context/{change_id}/qa/qa_report.yaml and "
        f"agent-context/{change_id}/intake/story.yaml."
    )
    run_eval_optimizer_loop(
        producer_func=steps.step_qa_engineer,
        producer_input=qa_producer_input,
        evaluator_func=steps.step_qa_evaluator,
        evaluator_prompt=qa_evaluator_prompt,
    )

    # ── Stage 6: Lessons Optimization (one-shot) ─────────────────────────────
    steps.step_lessons_optimizer(change_id=change_id, repo=repo)

    return ado_url

if __name__ == "__main__":
    with tags(f"Running Claude Code {get_time()}"):
        main()

