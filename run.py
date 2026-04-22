import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from prefect import flow, tags

import opik_integration  # noqa: F401 — configures Opik project context before the flow runs
import steps
from evaluator_optimizer_loops import run_uow_eval_loop, run_eval_optimizer_loop
from materialize import run_materialization
from workflow_inputs import DEFAULT_TEST_STORY_FILE, resolve_workflow_input

# ====================== HELPERS ====================== #

RUNNER_ROOT = Path(__file__).resolve().parent
AGENT_CONTEXT_ROOT = RUNNER_ROOT / "agent-context"

def get_time():
    now = datetime.now()
    return now.strftime("%H:%M %m/%d/%Y")


def use_runner_root() -> None:
    os.chdir(RUNNER_ROOT)

def load_assignments(change_id: str) -> dict:
    """Read assignments.json produced by the task-assigner."""
    path = AGENT_CONTEXT_ROOT / change_id / "planning" / "assignments.json"
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    # Normalize execution_schedule to always be a list of batch dicts.
    # The agent sometimes emits batch 1 as a bare dict under "execution_schedule"
    # and subsequent batches as top-level keys "batch_2", "batch_3", etc.
    sched = data.get("execution_schedule", [])
    if isinstance(sched, dict):
        batches = [sched]
        i = 2
        while f"batch_{i}" in data:
            batches.append(data[f"batch_{i}"])
            i += 1
        data["execution_schedule"] = batches

    return data


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
        choices=["claude", "copilot"],
        help="Agent runner to use: 'claude' (Claude Code CLI) or 'copilot' (GitHub Copilot CLI). Defaults to 'claude'.",
    )
    parser.add_argument(
        "--skip-materialize",
        action="store_true",
        help="Skip the agent materialization step (useful when agents are known to be up-to-date).",
    )
    return parser.parse_args()

# ====================== MAIN ====================== #

@flow(log_prints=True, timeout_seconds=7200)
def main(
    repo: str | None = None,
    change_id: str | None = None,
    ado_url: str | None = None,
    story_file: str | None = None,
    runner: str = "claude",
    skip_materialize: bool = False,
):
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

    print(f"Running workflow for {resolved_change_id}")
    print(f"Target repo: {resolved_repo}")
    print(f"Intake mode: {intake_mode}")
    print(f"Intake source: {intake_source}")
    print(f"Runner: {runner}")

    # ── Stage 0: Materialize agents ───────────────────────────────────────────
    if not skip_materialize:
        print("Materializing agents from agent-sources/...")
        run_materialization()
    else:
        print("Skipping materialization (--skip-materialize).")

    # ── Stage 1: Intake ───────────────────────────────────────────────────────
    result_intake = steps.step_intake(
        intake_source=intake_source,
        repo=resolved_repo,
        change_id=resolved_change_id,
        intake_mode=intake_mode,
        runner=runner,
    )

    # ── Stage 2: Task Generation (eval-optimizer loop) ───────────────────────
    task_gen_input = (
        f"Generate a task plan for change {resolved_change_id} in {resolved_repo}.\n"
        f"Read the intake artifacts from agent-context/{resolved_change_id}/intake/.\n"
        f"Act immediately. Do not ask questions."
    )
    task_gen_evaluator_prompt = (
        f"Evaluate the task plan for {resolved_change_id} in {resolved_repo}. "
        f"Read agent-context/{resolved_change_id}/planning/tasks.yaml."
    )
    run_eval_optimizer_loop(
        producer_func=steps.step_task_gen_producer,
        producer_input=task_gen_input,
        evaluator_func=steps.step_task_gen_evaluator,
        evaluator_prompt=task_gen_evaluator_prompt,
        runner=runner,
    )

    # ── Stage 3: Task Assignment (eval-optimizer loop) ────────────────────────
    assigner_input = (
        f"Create an execution schedule for change {resolved_change_id}.\n"
        f"Read tasks from agent-context/{resolved_change_id}/planning/tasks.yaml.\n"
        f"Read story context from agent-context/{resolved_change_id}/intake/story.yaml.\n"
        f"Read constraints from agent-context/{resolved_change_id}/intake/constraints.md.\n"
        f"Target repo: {resolved_repo}\n"
        f"Act immediately. Do not ask questions."
    )
    assignment_evaluator_prompt = (
        f"Evaluate the execution schedule for {resolved_change_id}. "
        f"Read agent-context/{resolved_change_id}/planning/assignments.json and "
        f"agent-context/{resolved_change_id}/planning/tasks.yaml."
    )
    run_eval_optimizer_loop(
        producer_func=steps.step_task_assigner,
        producer_input=assigner_input,
        evaluator_func=steps.step_assignment_evaluator,
        evaluator_prompt=assignment_evaluator_prompt,
        runner=runner,
    )

    # ── Stage 4: Execution — per-batch, parallel where safe ──────────────────
    assignments = load_assignments(resolved_change_id)
    batches = sorted(assignments.get("execution_schedule", []), key=lambda b: b["batch"])

    for batch in batches:
        uow_ids = [uow["uow_id"] for uow in batch.get("uows", [])]
        is_parallel = batch.get("parallel_execution", False)
        print(f"Executing batch {batch['batch']} — UoWs: {uow_ids} (parallel={is_parallel})")

        if is_parallel and len(uow_ids) > 1:
            # Fan-out: submit all UoWs in the batch concurrently
            futures = [
                run_uow_eval_loop.submit(uow_id=uid, change_id=resolved_change_id, repo=resolved_repo, runner=runner)
                for uid in uow_ids
            ]
            # Wait for all UoWs in this batch to complete before advancing
            for future in futures:
                future.result()
        else:
            for uid in uow_ids:
                run_uow_eval_loop(uow_id=uid, change_id=resolved_change_id, repo=resolved_repo, runner=runner)

    # ── Stage 5: QA Validation (eval-optimizer loop) ─────────────────────────
    qa_producer_input = (
        f"Perform QA validation for change {resolved_change_id}.\n"
        f"Read story ACs from agent-context/{resolved_change_id}/intake/story.yaml.\n"
        f"Read task plan from agent-context/{resolved_change_id}/planning/tasks.yaml.\n"
        f"Read assignments from agent-context/{resolved_change_id}/planning/assignments.json.\n"
        f"Read all implementation reports from agent-context/{resolved_change_id}/execution/*/impl_report.yaml.\n"
        f"Target repo: {resolved_repo}\n"
        f"Write your report to agent-context/{resolved_change_id}/qa/qa_report.yaml.\n"
        f"Act immediately. Do not ask questions."
    )
    qa_evaluator_prompt = (
        f"Evaluate the QA report for {resolved_change_id}. "
        f"Read agent-context/{resolved_change_id}/qa/qa_report.yaml and "
        f"agent-context/{resolved_change_id}/intake/story.yaml."
    )
    run_eval_optimizer_loop(
        producer_func=steps.step_qa_engineer,
        producer_input=qa_producer_input,
        evaluator_func=steps.step_qa_evaluator,
        evaluator_prompt=qa_evaluator_prompt,
        runner=runner,
    )

    # ── Stage 6: Lessons Optimization (one-shot) ─────────────────────────────
    steps.step_lessons_optimizer(change_id=resolved_change_id, repo=resolved_repo, runner=runner)

    return intake_source

if __name__ == "__main__":
    args = parse_args()
    with tags(f"Running Claude Code {get_time()}"):
        main(
            repo=args.repo,
            change_id=args.change_id,
            ado_url=args.ado_url,
            story_file=args.story_file,
            runner=args.runner,
            skip_materialize=args.skip_materialize,
        )
