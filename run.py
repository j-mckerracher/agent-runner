import json
import os
import subprocess
from prefect import flow, task, tags
from datetime import datetime

# ====================== HELPERS ====================== #

def get_time():
    now = datetime.now()
    return now.strftime("%H:%M %m/%d/%Y")

def load_assignments(change_id: str) -> dict:
    """Read assignments.json produced by the task-assigner."""
    path = os.path.join("agent-context", change_id, "planning", "assignments.json")
    with open(path, "r") as f:
        return json.load(f)

# ====================== STEPS ====================== #

# 1
@task(log_prints=True, name="intake")
def step_intake(ado_story_link: str, repo: str):
    with tags('intake-agent'):
        if not ado_story_link:
            raise ValueError("ADO story link cannot be empty.")
        print(f"Received ADO story link: {ado_story_link}")

        prompt = f"Intake the following story link: {ado_story_link}\n"
        prompt += f"Target repo: {repo}\n"
        prompt += "If intake artifacts already exist for this story, you must delete them and create new ones.\n"
        prompt += "You MUST use the azure-devops-cli skill to interact with ADO"
        return run_claude_cmd(prompt=prompt, agent="intake-agent")

# 2
@task(log_prints=True, name="task-gen-producer")
def step_task_gen_producer(context: str) -> str:
    return run_claude_cmd(prompt=context, agent="task-generator")


# 3
@task(log_prints=True, name="task-gen-evaluator")
def step_task_gen_evaluator(context: str) -> str:
    return run_claude_cmd(prompt=context, agent="task-plan-evaluator")

# 4
@task(log_prints=True, name="task-assigner")
def step_task_assigner(context: str) -> str:
    return run_claude_cmd(prompt=context, agent="task-assigner")

# 4b
@task(log_prints=True, name="assignment-evaluator")
def step_assignment_evaluator(context: str) -> str:
    return run_claude_cmd(prompt=context, agent="assignment-evaluator")

# 5
@task(log_prints=True, name="software-engineer")
def step_software_engineer(uow_id: str, change_id: str, repo: str, evaluator_feedback: str = "") -> str:
    prompt = (
        f"Implement UoW {uow_id} for change {change_id}.\n"
        f"Read the UoW spec from agent-context/{change_id}/execution/{uow_id}/uow_spec.yaml.\n"
        f"Target repo: {repo}\n"
    )
    if evaluator_feedback:
        prompt += (
            f"\n\n## Evaluator Issues to Fix:\n{evaluator_feedback}\n\n"
            f"Address every issue listed above. Do not ask questions — act immediately."
        )
    return run_claude_cmd(prompt=prompt, agent="software-engineer-hyperagent")

# 6
@task(log_prints=True, name="software-engineer-evaluator")
def step_software_engineer_evaluator(uow_id: str, change_id: str, repo: str) -> str:
    prompt = (
        f"Evaluate the implementation of UoW {uow_id} for change {change_id}.\n"
        f"Read the implementation report from agent-context/{change_id}/execution/{uow_id}/impl_report.yaml.\n"
        f"Read the UoW spec from agent-context/{change_id}/execution/{uow_id}/uow_spec.yaml.\n"
        f"Target repo: {repo}\n"
    )
    return run_claude_cmd(prompt=prompt, agent="implementation-evaluator")

# ====================== UOW EVAL-OPTIMIZER LOOP ====================== #

@task(log_prints=True, name="run-uow-eval-loop", timeout_seconds=3600)
def run_uow_eval_loop(uow_id: str, change_id: str, repo: str, iter_count: int = 3) -> tuple[str, str]:
    """
    Run the software-engineer + implementation-evaluator eval-optimizer loop
    for a single Unit of Work. Returns (final_impl_out, final_eval_out).
    """
    producer_out, evaluator_out = "", ""
    for i in range(iter_count):
        producer_out = step_software_engineer(
            uow_id=uow_id,
            change_id=change_id,
            repo=repo,
            evaluator_feedback=evaluator_out if i > 0 else "",
        )
        evaluator_out = step_software_engineer_evaluator(
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


# ====================== RUN COMMANDS ============================= #

def run_claude_cmd(
    prompt: str,
    agent: str,
    model: str = "claude-haiku-4-5-20251001",
    skip_permissions: bool = True,
    extra_flags: list[str] | None = None,
) -> str:
    """
    Trigger Claude Code via the CLI and return stdout.

    Args:
        prompt: The prompt or ADO URL to pass with -p.
        agent: The --agent value.
        model: The --model value.
        skip_permissions: Whether to include --dangerously-skip-permissions.
        extra_flags: Any additional CLI flags to append.

    Returns:
        stdout from the completed process.
    """
    if not prompt:
        raise ValueError(f"prompt must not be empty (agent={agent})")
    print(f"Starting Claude Code via {agent}...")
    print(f"Prompt: {prompt}")
    print(f"Model: {model}")
    cmd = ["claude", "-p", prompt, "--agent", agent, "--model", model]
    if skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    if extra_flags:
        cmd.extend(extra_flags)
    result = subprocess.run(cmd, check=True, text=True, capture_output=True)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)
    return result.stdout


@task(log_prints=True, name="run-claude")
def run_claude(
    prompt: str,
    agent: str,
    model: str = "claude-haiku-4-5-20251001",
    skip_permissions: bool = True,
    extra_flags: list[str] | None = None,
) -> str:
    """Prefect task wrapper around run_claude_cmd."""
    return run_claude_cmd(prompt=prompt, agent=agent, model=model,
                          skip_permissions=skip_permissions, extra_flags=extra_flags)


def run_copilot_cmd(
    prompt: str,
    agent: str,
    model: str = "gpt-5-mini",
    skip_permissions: bool = True,
    silent: bool = True,
    extra_flags: list[str] | None = None,
) -> str:
    """
    Trigger GitHub Copilot CLI non-interactively and return stdout.

    Args:
        prompt: The prompt to pass with -p.
        agent: The --agent=<name> value.
        model: The --model value.
        silent: When True, passes -s to suppress usage info from stdout.
        extra_flags: Any additional CLI flags to append.

    Returns:
        stdout from the completed process.
        :param skip_permissions:
    """
    if not prompt:
        raise ValueError(f"prompt must not be empty (agent={agent})")
    print(f"Starting Copilot CLI via {agent}...")
    print(f"Prompt: {prompt}")
    print(f"Model: {model}")
    cmd = ["copilot", "-p", prompt, f"--agent={agent}", "--model", model]
    if silent:
        cmd.append("-s")
    if skip_permissions:
        cmd.append("--yolo")
    if extra_flags:
        cmd.extend(extra_flags)
    result = subprocess.run(cmd, check=True, text=True, capture_output=True)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)
    return result.stdout


@task(log_prints=True, name="run-copilot")
def run_copilot(
    prompt: str,
    agent: str,
    model: str = "gpt-5-mini",
    silent: bool = True,
    extra_flags: list[str] | None = None,
) -> str:
    """Prefect task wrapper around run_copilot_cmd."""
    return run_copilot_cmd(prompt=prompt, agent=agent, model=model,
                           silent=silent, extra_flags=extra_flags)

# ====================== MAIN ====================== #

@flow(log_prints=True, timeout_seconds=7200)
def main():
    ado_url = "https://dev.azure.com/mclm/Mayo%20Collaborative%20Services/_workitems/edit/5035632"
    repo = "/Users/mckerracher.joshua/Code/mcs-products-mono-ui"
    change_id = "WI-5035632"

    # ── Stage 1: Intake ──────────────────────────────────────────────────────
    result_intake = step_intake(ado_story_link=ado_url, repo=repo)

    # ── Stage 2: Task Generation (eval-optimizer loop) ───────────────────────
    task_gen_evaluator_prompt = (
        f"Evaluate the task plan for {change_id} in {repo}. "
        f"Read agent-context/{change_id}/planning/tasks.yaml."
    )
    run_eval_optimizer_loop(
        producer_func=step_task_gen_producer,
        producer_input=result_intake,
        evaluator_func=step_task_gen_evaluator,
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
        producer_func=step_task_assigner,
        producer_input=assigner_input,
        evaluator_func=step_assignment_evaluator,
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

    return ado_url

if __name__ == "__main__":
    with tags(f"Running Claude Code {get_time()}"):
        main()

