import argparse
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from prefect import flow, tags

import opik_integration  # noqa: F401 — configures Opik project context before the flow runs
import steps
from evaluator_optimizer_loops import run_uow_eval_loop, run_eval_optimizer_loop
from materialize import run_materialization
from runner_models import DEFAULT_GEMINI_MODEL, GEMINI_MODEL_CHOICES
from workflow_api import (
    WorkflowContext,
    load_execution_plan,
    make_agent_step,
    make_sdk_evaluator_step,
    render_prompt_template,
)
from workflow_inputs import DEFAULT_TEST_STORY_FILE, resolve_workflow_input

# ====================== HELPERS ====================== #

RUNNER_ROOT = Path(__file__).resolve().parent
AGENT_CONTEXT_ROOT = RUNNER_ROOT / "agent-context"
RUNNER_LABELS = {
    "claude": "Claude Code",
    "copilot": "GitHub Copilot",
    "gemini": "Gemini CLI",
}
TASK_GEN_INPUT_TEMPLATE = (
    "Generate a task plan for change {change_id} in {repo}.\n"
    "Read the intake artifacts from {artifact_root}/intake/.\n"
    "Act immediately. Do not ask questions."
)
TASK_GEN_EVALUATOR_TEMPLATE = (
    "Evaluate the task plan for {change_id} in {repo}. "
    "Read {artifact_root}/planning/tasks.yaml."
)
ASSIGNER_INPUT_TEMPLATE = (
    "Create an execution schedule for change {change_id}.\n"
    "Read tasks from {artifact_root}/planning/tasks.yaml.\n"
    "Read story context from {artifact_root}/intake/story.yaml.\n"
    "Read constraints from {artifact_root}/intake/constraints.md.\n"
    "Target repo: {repo}\n"
    "Act immediately. Do not ask questions."
)
ASSIGNMENT_EVALUATOR_TEMPLATE = (
    "Evaluate the execution schedule for {change_id}. "
    "Read {artifact_root}/planning/assignments.json and "
    "{artifact_root}/planning/tasks.yaml."
)
QA_PRODUCER_TEMPLATE = (
    "Perform QA validation for change {change_id}.\n"
    "Read story ACs from {artifact_root}/intake/story.yaml.\n"
    "Read task plan from {artifact_root}/planning/tasks.yaml.\n"
    "Read assignments from {artifact_root}/planning/assignments.json.\n"
    "Read all implementation reports from {artifact_root}/execution/*/impl_report.yaml.\n"
    "Target repo: {repo}\n"
    "Write your report to {artifact_root}/qa/qa_report.yaml.\n"
    "Act immediately. Do not ask questions."
)
QA_EVALUATOR_TEMPLATE = (
    "Evaluate the QA report for {change_id}. "
    "Read {artifact_root}/qa/qa_report.yaml and "
    "{artifact_root}/intake/story.yaml."
)

TASK_GENERATOR_STEP = make_agent_step(
    agent_name="task-generator",
    trace_name="task-gen-producer",
    task_name="task-gen-producer",
)
TASK_PLAN_EVALUATOR_STEP = make_sdk_evaluator_step(
    agent_name="task-plan-evaluator",
    trace_name="task-gen-evaluator",
    task_name="task-gen-evaluator",
)
TASK_ASSIGNER_STEP = make_agent_step(
    agent_name="task-assigner",
    trace_name="task-assigner",
    task_name="task-assigner",
)
ASSIGNMENT_EVALUATOR_STEP = make_sdk_evaluator_step(
    agent_name="assignment-evaluator",
    trace_name="assignment-evaluator",
    task_name="assignment-evaluator",
)
QA_STEP = make_agent_step(
    agent_name="qa",
    trace_name="qa-engineer",
    task_name="qa-engineer",
)
QA_EVALUATOR_STEP = make_sdk_evaluator_step(
    agent_name="qa-evaluator",
    trace_name="qa-evaluator",
    task_name="qa-evaluator",
)

def get_time():
    now = datetime.now()
    return now.strftime("%H:%M %m/%d/%Y")


def use_runner_root() -> None:
    os.chdir(RUNNER_ROOT)


def runner_label(runner: str) -> str:
    return RUNNER_LABELS.get(runner, runner)


def clean_workspace(change_id: str) -> None:
    """Remove stale agent-context directories for this change_id before the pipeline starts.

    Removes:
      - agent-context/{change_id}/ (ensures a fresh start)
      - agent-context/{base}-RUN-*/ (stale multi-run artifacts from previous runs)
        Only when change_id is a bare ID (not already a -RUN-NN variant), to avoid
        interfering with concurrent isolated multi-run evaluations.
    """
    if not AGENT_CONTEXT_ROOT.is_dir():
        return

    base = re.sub(r"-RUN-\d+$", "", change_id)
    is_isolated_run = base != change_id

    target = AGENT_CONTEXT_ROOT / change_id
    if target.is_dir():
        print(f"[cleanup] Removing stale workspace: {target.name}")
        shutil.rmtree(target)

    if not is_isolated_run:
        pattern = re.compile(rf"^{re.escape(base)}-RUN-\d+$")
        for entry in AGENT_CONTEXT_ROOT.iterdir():
            if entry.is_dir() and pattern.match(entry.name):
                print(f"[cleanup] Removing stale multi-run workspace: {entry.name}")
                shutil.rmtree(entry)


def load_assignments(change_id: str) -> dict:
    """Read assignments.json produced by the task-assigner."""
    workflow_context = WorkflowContext(
        run_id=change_id,
        repo="",
        artifact_root=AGENT_CONTEXT_ROOT / change_id,
        runner_model=None,
    )
    return load_execution_plan(workflow_context)


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
        choices=["claude", "copilot", "gemini"],
        help=(
            "Agent runner to use: 'claude' (Claude Code CLI), "
            "'copilot' (GitHub Copilot CLI), or 'gemini' (Gemini CLI). Defaults to 'claude'."
        ),
    )
    parser.add_argument(
        "--skip-materialize",
        action="store_true",
        help="Skip the agent materialization step (useful when agents are known to be up-to-date).",
    )
    parser.add_argument(
        "--gemini-model",
        default=DEFAULT_GEMINI_MODEL,
        choices=GEMINI_MODEL_CHOICES,
        help=(
            "Gemini model to use when --runner gemini. "
            f"Defaults to '{DEFAULT_GEMINI_MODEL}'."
        ),
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
    gemini_model: str = DEFAULT_GEMINI_MODEL,
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

    clean_workspace(resolved_change_id)

    print(f"Running workflow for {resolved_change_id}")
    print(f"Target repo: {resolved_repo}")
    print(f"Intake mode: {intake_mode}")
    print(f"Intake source: {intake_source}")
    print(f"Runner: {runner}")
    selected_runner_model = gemini_model if runner == "gemini" else None
    runner_model_kwargs = {"runner_model": selected_runner_model} if selected_runner_model is not None else {}
    workflow_context = WorkflowContext(
        run_id=resolved_change_id,
        repo=resolved_repo,
        runner=runner,
        runner_model=selected_runner_model,
        artifact_root=AGENT_CONTEXT_ROOT / resolved_change_id,
    )
    if selected_runner_model is not None:
        print(f"Gemini model: {gemini_model}")

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
        **runner_model_kwargs,
    )

    # ── Stage 2: Task Generation (eval-optimizer loop) ───────────────────────
    task_gen_input = (
        render_prompt_template(
            TASK_GEN_INPUT_TEMPLATE,
            workflow_context,
        )
    )
    task_gen_evaluator_prompt = (
        render_prompt_template(
            TASK_GEN_EVALUATOR_TEMPLATE,
            workflow_context,
        )
    )
    run_eval_optimizer_loop(
        producer_func=TASK_GENERATOR_STEP,
        producer_input=task_gen_input,
        evaluator_func=TASK_PLAN_EVALUATOR_STEP,
        evaluator_prompt=task_gen_evaluator_prompt,
        runner=runner,
        **runner_model_kwargs,
    )

    # ── Stage 3: Task Assignment (eval-optimizer loop) ────────────────────────
    assigner_input = (
        render_prompt_template(
            ASSIGNER_INPUT_TEMPLATE,
            workflow_context,
        )
    )
    assignment_evaluator_prompt = (
        render_prompt_template(
            ASSIGNMENT_EVALUATOR_TEMPLATE,
            workflow_context,
        )
    )
    run_eval_optimizer_loop(
        producer_func=TASK_ASSIGNER_STEP,
        producer_input=assigner_input,
        evaluator_func=ASSIGNMENT_EVALUATOR_STEP,
        evaluator_prompt=assignment_evaluator_prompt,
        runner=runner,
        **runner_model_kwargs,
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
                run_uow_eval_loop.submit(
                    uow_id=uid,
                    change_id=resolved_change_id,
                    repo=resolved_repo,
                    runner=runner,
                    **runner_model_kwargs,
                )
                for uid in uow_ids
            ]
            # Wait for all UoWs in this batch to complete before advancing
            for future in futures:
                future.result()
        else:
            for uid in uow_ids:
                run_uow_eval_loop(
                    uow_id=uid,
                    change_id=resolved_change_id,
                    repo=resolved_repo,
                    runner=runner,
                    **runner_model_kwargs,
                )

    # ── Stage 5: QA Validation (eval-optimizer loop) ─────────────────────────
    qa_producer_input = (
        render_prompt_template(
            QA_PRODUCER_TEMPLATE,
            workflow_context,
        )
    )
    qa_evaluator_prompt = (
        render_prompt_template(
            QA_EVALUATOR_TEMPLATE,
            workflow_context,
        )
    )
    run_eval_optimizer_loop(
        producer_func=QA_STEP,
        producer_input=qa_producer_input,
        evaluator_func=QA_EVALUATOR_STEP,
        evaluator_prompt=qa_evaluator_prompt,
        runner=runner,
        **runner_model_kwargs,
    )

    # ── Stage 6: Lessons Optimization (one-shot) ─────────────────────────────
    steps.step_lessons_optimizer(
        change_id=resolved_change_id,
        repo=resolved_repo,
        runner=runner,
        **runner_model_kwargs,
    )

    return intake_source

if __name__ == "__main__":
    args = parse_args()
    with tags(f"Running {runner_label(args.runner)} {get_time()}"):
        main(
            repo=args.repo,
            change_id=args.change_id,
            ado_url=args.ado_url,
            story_file=args.story_file,
            runner=args.runner,
            skip_materialize=args.skip_materialize,
            gemini_model=args.gemini_model,
        )
