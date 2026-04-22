import opik
from prefect import task, tags
from run_cmds import run_claude_cmd, run_agent_cmd
from opik_integration import call_evaluator_sdk


def build_intake_prompt(intake_source: str, repo: str, change_id: str, intake_mode: str) -> str:
    if not intake_source:
        raise ValueError("intake_source cannot be empty.")
    if intake_mode not in {"ado", "synthetic"}:
        raise ValueError(f"Unsupported intake_mode: {intake_mode}")

    if intake_mode == "ado":
        prompt = f"Intake the following Azure DevOps story link: {intake_source}\n"
        prompt += f"Change ID: {change_id}\n"
        prompt += f"Target repo: {repo}\n"
        prompt += "If intake artifacts already exist for this story, you must delete them and create new ones.\n"
        prompt += "You MUST use the azure-devops-cli skill to interact with ADO.\n"
        prompt += (
            f"Normalize the result into canonical intake artifacts under agent-context/{change_id}/intake/."
        )
        return prompt

    prompt = f"Create intake artifacts for a synthetic test story from the local fixture: {intake_source}\n"
    prompt += f"Change ID: {change_id}\n"
    prompt += f"Target repo: {repo}\n"
    prompt += "This is a local workflow test scenario, not a live Azure DevOps work item.\n"
    prompt += "Read the fixture file directly and normalize it into canonical intake artifacts.\n"
    prompt += "If intake artifacts already exist for this story, you must delete them and create new ones.\n"
    prompt += "Preserve the fixture contents under raw_input.\n"
    prompt += "Do NOT require or use the azure-devops-cli skill unless the fixture explicitly includes ADO metadata.\n"
    prompt += (
        f"Normalize the result into canonical intake artifacts under agent-context/{change_id}/intake/."
    )
    return prompt


# 1
@task(log_prints=True, name="intake")
def step_intake(intake_source: str, repo: str, change_id: str, intake_mode: str = "ado", runner: str = "claude"):
    with tags('intake-agent'):
        print(f"Received intake source ({intake_mode}): {intake_source}")
        prompt = build_intake_prompt(
            intake_source=intake_source,
            repo=repo,
            change_id=change_id,
            intake_mode=intake_mode,
        )
        with opik.start_as_current_trace("intake", project_name="agent-runner") as trace:
            trace.input = {"intake_source": intake_source, "change_id": change_id, "intake_mode": intake_mode}
            result = run_agent_cmd(runner=runner, prompt=prompt, agent="intake-agent")
            trace.output = {"stdout_preview": result[:2000]}
        return result


# 2
@task(log_prints=True, name="task-gen-producer")
def step_task_gen_producer(context: str, runner: str = "claude") -> str:
    with opik.start_as_current_trace("task-gen-producer", project_name="agent-runner") as trace:
        trace.input = {"context": context}
        result = run_agent_cmd(runner=runner, prompt=context, agent="task-generator")
        trace.output = {"stdout_preview": result[:2000]}
    return result


# 3
@task(log_prints=True, name="task-gen-evaluator")
def step_task_gen_evaluator(context: str, runner: str = "claude") -> str:
    with opik.start_as_current_trace("task-gen-evaluator", project_name="agent-runner") as trace:
        trace.input = {"context": context}
        result = call_evaluator_sdk(context, "task-plan-evaluator")
        trace.output = {"result": result}
    return result


# 4
@task(log_prints=True, name="task-assigner")
def step_task_assigner(context: str, runner: str = "claude") -> str:
    with opik.start_as_current_trace("task-assigner", project_name="agent-runner") as trace:
        trace.input = {"context": context}
        result = run_agent_cmd(runner=runner, prompt=context, agent="task-assigner")
        trace.output = {"stdout_preview": result[:2000]}
    return result


# 4b
@task(log_prints=True, name="assignment-evaluator")
def step_assignment_evaluator(context: str, runner: str = "claude") -> str:
    with opik.start_as_current_trace("assignment-evaluator", project_name="agent-runner") as trace:
        trace.input = {"context": context}
        result = call_evaluator_sdk(context, "assignment-evaluator")
        trace.output = {"result": result}
    return result


# 5
@task(log_prints=True, name="software-engineer")
def step_software_engineer(uow_id: str, change_id: str, repo: str, evaluator_feedback: str = "", runner: str = "claude") -> str:
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
    with opik.start_as_current_trace("software-engineer", project_name="agent-runner") as trace:
        trace.input = {"uow_id": uow_id, "change_id": change_id, "has_feedback": bool(evaluator_feedback)}
        result = run_agent_cmd(runner=runner, prompt=prompt, agent="software-engineer-hyperagent")
        trace.output = {"stdout_preview": result[:2000]}
    return result


# 6
@task(log_prints=True, name="software-engineer-evaluator")
def step_software_engineer_evaluator(uow_id: str, change_id: str, repo: str, runner: str = "claude") -> str:
    context = (
        f"Evaluate the implementation of UoW {uow_id} for change {change_id}.\n"
        f"Read the implementation report from agent-context/{change_id}/execution/{uow_id}/impl_report.yaml.\n"
        f"Read the UoW spec from agent-context/{change_id}/execution/{uow_id}/uow_spec.yaml.\n"
        f"Target repo: {repo}"
    )
    with opik.start_as_current_trace("implementation-evaluator", project_name="agent-runner") as trace:
        trace.input = {"uow_id": uow_id, "change_id": change_id}
        result = call_evaluator_sdk(context, "implementation-evaluator")
        trace.output = {"result": result}
    return result


# 7
@task(log_prints=True, name="qa-engineer")
def step_qa_engineer(context: str, runner: str = "claude") -> str:
    with opik.start_as_current_trace("qa-engineer", project_name="agent-runner") as trace:
        trace.input = {"context": context}
        result = run_agent_cmd(runner=runner, prompt=context, agent="qa-engineer")
        trace.output = {"stdout_preview": result[:2000]}
    return result


# 8
@task(log_prints=True, name="qa-evaluator")
def step_qa_evaluator(context: str, runner: str = "claude") -> str:
    with opik.start_as_current_trace("qa-evaluator", project_name="agent-runner") as trace:
        trace.input = {"context": context}
        result = call_evaluator_sdk(context, "qa-evaluator")
        trace.output = {"result": result}
    return result


# 9
@task(log_prints=True, name="lessons-optimizer")
def step_lessons_optimizer(change_id: str, repo: str, runner: str = "claude") -> str:
    prompt = (
        f"Run the end-of-workflow lessons optimization for change {change_id}.\n"
        f"Read agent-context/lessons.md for recorded lessons.\n"
        f"Read all execution artifacts under agent-context/{change_id}/.\n"
        f"Target repo: {repo}\n"
        f"Write your report to agent-context/{change_id}/summary/lessons_optimizer_report.yaml.\n"
        f"Act immediately. Do not ask questions."
    )
    with opik.start_as_current_trace("lessons-optimizer", project_name="agent-runner") as trace:
        trace.input = {"change_id": change_id, "repo": repo}
        result = run_agent_cmd(runner=runner, prompt=prompt, agent="lessons-optimizer-hyperagent")
        trace.output = {"stdout_preview": result[:2000]}
    return result
