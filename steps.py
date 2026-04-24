import re

import opik
from prefect import task, tags
from run_cmds import run_claude_cmd, run_agent_cmd
from opik_integration import call_evaluator_sdk
from runner_models import DEFAULT_GEMINI_MODEL

EVALUATOR_ISSUES_HEADER = "## Evaluator Issues to Fix:"
EVALUATOR_REMEDIATION_INSTRUCTION = "Address every issue listed above. Do not ask questions — act immediately."


def _agent_runner_kwargs(runner_model: str | None) -> dict[str, str]:
    return {"runner_model": runner_model} if runner_model is not None else {}


def _append_evaluator_feedback(prompt: str, evaluator_feedback: str) -> str:
    if not evaluator_feedback:
        return prompt

    feedback_block = f"{EVALUATOR_ISSUES_HEADER}\n{evaluator_feedback}"
    if feedback_block in prompt:
        return prompt

    return (
        f"{prompt}\n\n{feedback_block}\n\n"
        f"{EVALUATOR_REMEDIATION_INSTRUCTION}"
    )


def _extract_change_id(context: str) -> str:
    """Parse change_id from a context string that references agent-context paths."""
    match = re.search(r"agent-context/([\w\-]+)/", context)
    return match.group(1) if match else ""


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
def step_intake(
    intake_source: str,
    repo: str,
    change_id: str,
    intake_mode: str = "ado",
    runner: str = "claude",
    runner_model: str | None = DEFAULT_GEMINI_MODEL,
    prompt: str | None = None,
):
    with tags('intake-agent'):
        print(f"Received intake source ({intake_mode}): {intake_source}")
        prompt = prompt or build_intake_prompt(
            intake_source=intake_source,
            repo=repo,
            change_id=change_id,
            intake_mode=intake_mode,
        )
        with opik.start_as_current_trace("intake", project_name="agent-runner") as trace:
            trace.input = {"intake_source": intake_source, "change_id": change_id, "intake_mode": intake_mode}
            trace.thread_id = change_id
            result = run_agent_cmd(
                runner=runner,
                prompt=prompt,
                agent="intake",
                **_agent_runner_kwargs(runner_model),
            )
            trace.output = {"stdout_preview": result[:2000]}
        return result


# 2
@task(log_prints=True, name="task-gen-producer")
def step_task_gen_producer(
    context: str,
    runner: str = "claude",
    runner_model: str | None = DEFAULT_GEMINI_MODEL,
) -> str:
    with opik.start_as_current_trace("task-gen-producer", project_name="agent-runner") as trace:
        trace.input = {"context": context}
        trace.thread_id = _extract_change_id(context)
        result = run_agent_cmd(
            runner=runner,
            prompt=context,
            agent="task-generator",
            **_agent_runner_kwargs(runner_model),
        )
        trace.output = {"stdout_preview": result[:2000]}
    return result


# 3
@task(log_prints=True, name="task-gen-evaluator")
def step_task_gen_evaluator(
    context: str,
    runner: str = "claude",
    runner_model: str | None = DEFAULT_GEMINI_MODEL,
) -> str:
    with opik.start_as_current_trace("task-gen-evaluator", project_name="agent-runner") as trace:
        trace.input = {"context": context}
        trace.thread_id = _extract_change_id(context)
        result = call_evaluator_sdk(context, "task-plan-evaluator")
        passed = "PASS" in result
        opik.opik_context.update_current_trace(
            feedback_scores=[{"name": "evaluator_pass", "value": 1.0 if passed else 0.0}],
            metadata={"evaluator": "task-plan-evaluator", "passed": passed},
        )
        trace.output = {"result": result}
    return result


# 4
@task(log_prints=True, name="task-assigner")
def step_task_assigner(
    context: str,
    runner: str = "claude",
    runner_model: str | None = DEFAULT_GEMINI_MODEL,
) -> str:
    with opik.start_as_current_trace("task-assigner", project_name="agent-runner") as trace:
        trace.input = {"context": context}
        trace.thread_id = _extract_change_id(context)
        result = run_agent_cmd(
            runner=runner,
            prompt=context,
            agent="task-assigner",
            **_agent_runner_kwargs(runner_model),
        )
        trace.output = {"stdout_preview": result[:2000]}
    return result


# 4b
@task(log_prints=True, name="assignment-evaluator")
def step_assignment_evaluator(
    context: str,
    runner: str = "claude",
    runner_model: str | None = DEFAULT_GEMINI_MODEL,
) -> str:
    with opik.start_as_current_trace("assignment-evaluator", project_name="agent-runner") as trace:
        trace.input = {"context": context}
        trace.thread_id = _extract_change_id(context)
        result = call_evaluator_sdk(context, "assignment-evaluator")
        passed = "PASS" in result
        opik.opik_context.update_current_trace(
            feedback_scores=[{"name": "evaluator_pass", "value": 1.0 if passed else 0.0}],
            metadata={"evaluator": "assignment-evaluator", "passed": passed},
        )
        trace.output = {"result": result}
    return result


# 5
@task(log_prints=True, name="software-engineer")
def step_software_engineer(
    uow_id: str,
    change_id: str,
    repo: str,
    evaluator_feedback: str = "",
    runner: str = "claude",
    runner_model: str | None = DEFAULT_GEMINI_MODEL,
    prompt: str | None = None,
) -> str:
    prompt = prompt or (
        f"Implement UoW {uow_id} for change {change_id}.\n"
        f"Read the UoW spec from agent-context/{change_id}/execution/{uow_id}/uow_spec.yaml.\n"
        f"Target repo: {repo}\n"
    )
    prompt = _append_evaluator_feedback(prompt, evaluator_feedback)
    with opik.start_as_current_trace("software-engineer", project_name="agent-runner") as trace:
        trace.input = {"uow_id": uow_id, "change_id": change_id, "has_feedback": bool(evaluator_feedback)}
        trace.thread_id = change_id
        result = run_agent_cmd(
            runner=runner,
            prompt=prompt,
            agent="software-engineer-hyperagent",
            **_agent_runner_kwargs(runner_model),
        )
        trace.output = {"stdout_preview": result[:2000]}
    return result


# 6
@task(log_prints=True, name="software-engineer-evaluator")
def step_software_engineer_evaluator(
    uow_id: str,
    change_id: str,
    repo: str,
    runner: str = "claude",
    runner_model: str | None = DEFAULT_GEMINI_MODEL,
    context: str | None = None,
) -> str:
    context = context or (
        f"Evaluate the implementation of UoW {uow_id} for change {change_id}.\n"
        f"Read the implementation report from agent-context/{change_id}/execution/{uow_id}/impl_report.yaml.\n"
        f"Read the UoW spec from agent-context/{change_id}/execution/{uow_id}/uow_spec.yaml.\n"
        f"Target repo: {repo}"
    )
    with opik.start_as_current_trace("implementation-evaluator", project_name="agent-runner") as trace:
        trace.input = {"uow_id": uow_id, "change_id": change_id}
        trace.thread_id = change_id
        result = call_evaluator_sdk(context, "implementation-evaluator")
        passed = "PASS" in result
        opik.opik_context.update_current_trace(
            feedback_scores=[{"name": "evaluator_pass", "value": 1.0 if passed else 0.0}],
            metadata={"evaluator": "implementation-evaluator", "uow_id": uow_id, "passed": passed},
        )
        trace.output = {"result": result}
    return result


# 7
@task(log_prints=True, name="qa-engineer")
def step_qa_engineer(
    context: str,
    runner: str = "claude",
    runner_model: str | None = DEFAULT_GEMINI_MODEL,
) -> str:
    with opik.start_as_current_trace("qa-engineer", project_name="agent-runner") as trace:
        trace.input = {"context": context}
        trace.thread_id = _extract_change_id(context)
        result = run_agent_cmd(
            runner=runner,
            prompt=context,
            agent="qa",
            **_agent_runner_kwargs(runner_model),
        )
        trace.output = {"stdout_preview": result[:2000]}
    return result


# 8
@task(log_prints=True, name="qa-evaluator")
def step_qa_evaluator(
    context: str,
    runner: str = "claude",
    runner_model: str | None = DEFAULT_GEMINI_MODEL,
) -> str:
    with opik.start_as_current_trace("qa-evaluator", project_name="agent-runner") as trace:
        trace.input = {"context": context}
        trace.thread_id = _extract_change_id(context)
        result = call_evaluator_sdk(context, "qa-evaluator")
        passed = "PASS" in result
        opik.opik_context.update_current_trace(
            feedback_scores=[{"name": "evaluator_pass", "value": 1.0 if passed else 0.0}],
            metadata={"evaluator": "qa-evaluator", "passed": passed},
        )
        trace.output = {"result": result}
    return result


# 9
@task(log_prints=True, name="lessons-optimizer")
def step_lessons_optimizer(
    change_id: str,
    repo: str,
    runner: str = "claude",
    runner_model: str | None = DEFAULT_GEMINI_MODEL,
    prompt: str | None = None,
) -> str:
    prompt = prompt or (
        f"Run the end-of-workflow lessons optimization for change {change_id}.\n"
        f"Read agent-context/lessons.md for recorded lessons.\n"
        f"Read all execution artifacts under agent-context/{change_id}/.\n"
        f"Target repo: {repo}\n"
        f"Write your report to agent-context/{change_id}/summary/lessons_optimizer_report.yaml.\n"
        f"Act immediately. Do not ask questions."
    )
    with opik.start_as_current_trace("lessons-optimizer", project_name="agent-runner") as trace:
        trace.input = {"change_id": change_id, "repo": repo}
        trace.thread_id = change_id
        result = run_agent_cmd(
            runner=runner,
            prompt=prompt,
            agent="lessons-optimizer-hyperagent",
            **_agent_runner_kwargs(runner_model),
        )
        trace.output = {"stdout_preview": result[:2000]}
    return result
