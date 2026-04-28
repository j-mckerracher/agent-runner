import re
import json
import subprocess
from pathlib import Path

import opik
from opik import opik_context
from run_cmds import run_claude_cmd, run_agent_cmd
from opik_integration import call_evaluator_sdk
from runner_models import DEFAULT_GEMINI_MODEL

AGENT_CONTEXT_ROOT = Path(__file__).resolve().parent / "agent-context"


def _agent_runner_kwargs(runner_model: str | None, copilot_effort: str | None = None) -> dict:
    result: dict = {}
    if runner_model is not None:
        result["runner_model"] = runner_model
    if copilot_effort is not None:
        result["copilot_effort"] = copilot_effort
    return result


def _extract_change_id(context: str) -> str:
    """Parse change_id from a context string that references agent-context paths."""
    match = re.search(r"agent-context/([\w\-]+)/", context)
    return match.group(1) if match else ""


def _annotate_trace(
    *,
    stage: str,
    runner: str,
    change_id: str,
    extra_metadata: dict | None = None,
    extra_tags: list[str] | None = None,
) -> None:
    """
    Attach standard Opik thread/tags/metadata to the current trace.
    Safe no-op if Opik is disabled or no trace is active.
    """
    metadata = {"change_id": change_id, "runner": runner, "stage": stage}
    if extra_metadata:
        metadata.update({k: v for k, v in extra_metadata.items() if v is not None})
    tags = [runner, f"stage:{stage}"]
    if extra_tags:
        tags.extend(extra_tags)
    try:
        kwargs: dict = {"tags": tags, "metadata": metadata}
        if change_id:
            kwargs["thread_id"] = change_id
        opik_context.update_current_trace(**kwargs)
    except Exception:
        pass


_INTAKE_FIELDS = {
    "System.Id",
    "System.Title",
    "System.Description",
    "System.WorkItemType",
    "System.State",
    "System.AreaPath",
    "System.IterationPath",
    "System.Tags",
    "Microsoft.VSTS.Common.AcceptanceCriteria",
    "Microsoft.VSTS.Scheduling.StoryPoints",
    "Microsoft.VSTS.Scheduling.Size",
    "System.AssignedTo",
    "System.Parent",
}


def _trim_ado_work_item(raw: dict) -> dict:
    """Keep only intake-relevant fields to reduce prompt size."""
    fields = raw.get("fields", {})
    trimmed_fields: dict = {}
    for key in _INTAKE_FIELDS:
        if key in fields:
            value = fields[key]
            if isinstance(value, dict) and "displayName" in value:
                value = value["displayName"]
            trimmed_fields[key] = value
    return {
        "id": raw.get("id"),
        "url": raw.get("url"),
        "fields": trimmed_fields,
    }


def _fetch_ado_work_item(ado_url: str) -> str | None:
    """
    Pre-fetch an Azure DevOps work item using the az CLI.
    """
    try:
        from urllib.parse import urlparse
        parsed = urlparse(ado_url)
        parts = parsed.path.strip("/").split("/")
        if len(parts) < 5:
            return None
        org_name = parts[0]
        item_id = parts[-1]
        org_url = f"https://dev.azure.com/{org_name}"
        result = subprocess.run(
            [
                "az", "boards", "work-item", "show",
                "--id", item_id,
                "--org", org_url,
                "--output", "json",
            ],
            text=True,
            capture_output=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            raw = json.loads(result.stdout.strip())
            trimmed = _trim_ado_work_item(raw)
            return json.dumps(trimmed, indent=2)
        return None
    except Exception:
        return None


def build_intake_prompt(
    intake_source: str,
    repo: str,
    change_id: str,
    intake_mode: str,
    runner: str = "claude",
    ado_work_item_json: str | None = None,
    extra_context: str | None = None,
) -> str:
    if not intake_source:
        raise ValueError("intake_source cannot be empty.")
    if intake_mode not in {"ado", "synthetic"}:
        raise ValueError(f"Unsupported intake_mode: {intake_mode}")

    if intake_mode == "ado":
        prompt = f"Intake the following Azure DevOps story link: {intake_source}\n"
        prompt += f"Change ID: {change_id}\n"
        prompt += f"Target repo: {repo}\n"
        prompt += "If intake artifacts already exist for this story, you must delete them and create new ones.\n"
        if runner == "gemini" and ado_work_item_json:
            prompt += (
                "The work item data has already been fetched for you. "
                "Use the JSON below as the raw input; do NOT run any shell commands to re-fetch it.\n\n"
                f"```json\n{ado_work_item_json}\n```\n\n"
            )
        elif runner != "gemini":
            prompt += "Use the azure-devops-cli skill (already loaded) to interact with ADO.\n"
        prompt += (
            f"Normalize the result into canonical intake artifacts under {AGENT_CONTEXT_ROOT}/{change_id}/intake/."
        )
        if extra_context:
            prompt += f"\n\nAdditional context from the user:\n{extra_context}\n"
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
        f"Normalize the result into canonical intake artifacts under {AGENT_CONTEXT_ROOT}/{change_id}/intake/."
    )
    if extra_context:
        prompt += f"\n\nAdditional context from the user:\n{extra_context}\n"
    return prompt


@opik.track(name="stage:intake", type="tool")
def step_intake(
    intake_source: str,
    repo: str,
    change_id: str,
    intake_mode: str = "ado",
    runner: str = "claude",
    runner_model: str | None = DEFAULT_GEMINI_MODEL,
    copilot_effort: str | None = None,
    extra_context: str | None = None,
):
    print(f"Received intake source ({intake_mode}): {intake_source}")
    _annotate_trace(
        stage="intake",
        runner=runner,
        change_id=change_id,
        extra_metadata={"intake_mode": intake_mode, "intake_source": intake_source},
    )
    prompt = build_intake_prompt(
        intake_source=intake_source,
        repo=repo,
        change_id=change_id,
        intake_mode=intake_mode,
        runner=runner,
        ado_work_item_json=(
            _fetch_ado_work_item(intake_source)
            if runner == "gemini" and intake_mode == "ado"
            else None
        ),
        extra_context=extra_context,
    )
    extra_skills = None
    return run_agent_cmd(
        runner=runner,
        prompt=prompt,
        agent="intake",
        extra_skills=extra_skills,
        **_agent_runner_kwargs(runner_model, copilot_effort),
    )


@opik.track(name="stage:task-gen-producer", type="tool")
def step_task_gen_producer(
    context: str,
    runner: str = "claude",
    runner_model: str | None = DEFAULT_GEMINI_MODEL,
    copilot_effort: str | None = None,
) -> str:
    change_id = _extract_change_id(context)
    _annotate_trace(stage="task-gen-producer", runner=runner, change_id=change_id)
    return run_agent_cmd(
        runner=runner,
        prompt=context,
        agent="task-generator",
        **_agent_runner_kwargs(runner_model, copilot_effort),
    )


@opik.track(name="stage:task-gen-evaluator", type="tool")
def step_task_gen_evaluator(
    context: str,
    runner: str = "claude",
    runner_model: str | None = DEFAULT_GEMINI_MODEL,
    copilot_effort: str | None = None,
) -> str:
    change_id = _extract_change_id(context)
    _annotate_trace(stage="task-gen-evaluator", runner=runner, change_id=change_id)
    result = call_evaluator_sdk(context, "task-plan-evaluator", runner=runner, runner_model=runner_model)
    passed = "PASS" in result
    try:
        opik_context.update_current_trace(
            feedback_scores=[{"name": "evaluator_pass", "value": 1.0 if passed else 0.0}],
            metadata={"evaluator": "task-plan-evaluator", "passed": passed},
        )
    except Exception:
        pass
    return result


@opik.track(name="stage:task-assigner", type="tool")
def step_task_assigner(
    context: str,
    runner: str = "claude",
    runner_model: str | None = DEFAULT_GEMINI_MODEL,
    copilot_effort: str | None = None,
) -> str:
    change_id = _extract_change_id(context)
    _annotate_trace(stage="task-assigner", runner=runner, change_id=change_id)
    return run_agent_cmd(
        runner=runner,
        prompt=context,
        agent="task-assigner",
        **_agent_runner_kwargs(runner_model, copilot_effort),
    )


@opik.track(name="stage:assignment-evaluator", type="tool")
def step_assignment_evaluator(
    context: str,
    runner: str = "claude",
    runner_model: str | None = DEFAULT_GEMINI_MODEL,
    copilot_effort: str | None = None,
) -> str:
    change_id = _extract_change_id(context)
    _annotate_trace(stage="assignment-evaluator", runner=runner, change_id=change_id)
    result = call_evaluator_sdk(context, "assignment-evaluator", runner=runner, runner_model=runner_model)
    passed = "PASS" in result
    try:
        opik_context.update_current_trace(
            feedback_scores=[{"name": "evaluator_pass", "value": 1.0 if passed else 0.0}],
            metadata={"evaluator": "assignment-evaluator", "passed": passed},
        )
    except Exception:
        pass
    return result


@opik.track(name="stage:implementation", type="tool")
def step_software_engineer(
    uow_id: str,
    change_id: str,
    repo: str,
    evaluator_feedback: str = "",
    runner: str = "claude",
    runner_model: str | None = DEFAULT_GEMINI_MODEL,
    copilot_effort: str | None = None,
) -> str:
    _annotate_trace(
        stage="implementation",
        runner=runner,
        change_id=change_id,
        extra_metadata={"uow_id": uow_id, "has_feedback": bool(evaluator_feedback)},
    )
    prompt = (
        f"Implement UoW {uow_id} for change {change_id}.\n"
        f"Read the UoW spec from {AGENT_CONTEXT_ROOT}/{change_id}/execution/{uow_id}/uow_spec.yaml.\n"
        f"Target repo: {repo}\n"
    )
    if evaluator_feedback:
        prompt += (
            f"\n\n## Evaluator Issues to Fix:\n{evaluator_feedback}\n\n"
            f"Address every issue listed above. Do not ask questions — act immediately."
        )
    return run_agent_cmd(
        runner=runner,
        prompt=prompt,
        agent="software-engineer-hyperagent",
        **_agent_runner_kwargs(runner_model, copilot_effort),
    )


@opik.track(name="stage:implementation-evaluator", type="tool")
def step_software_engineer_evaluator(
    uow_id: str,
    change_id: str,
    repo: str,
    runner: str = "claude",
    runner_model: str | None = DEFAULT_GEMINI_MODEL,
    copilot_effort: str | None = None,
) -> str:
    _annotate_trace(
        stage="implementation-evaluator",
        runner=runner,
        change_id=change_id,
        extra_metadata={"uow_id": uow_id},
    )
    context = (
        f"Evaluate the implementation of UoW {uow_id} for change {change_id}.\n"
        f"Read the implementation report from {AGENT_CONTEXT_ROOT}/{change_id}/execution/{uow_id}/impl_report.yaml.\n"
        f"Read the UoW spec from {AGENT_CONTEXT_ROOT}/{change_id}/execution/{uow_id}/uow_spec.yaml.\n"
        f"Target repo: {repo}"
    )
    result = call_evaluator_sdk(context, "implementation-evaluator", runner=runner, runner_model=runner_model)
    passed = "PASS" in result
    try:
        opik_context.update_current_trace(
            feedback_scores=[{"name": "evaluator_pass", "value": 1.0 if passed else 0.0}],
            metadata={"evaluator": "implementation-evaluator", "uow_id": uow_id, "passed": passed},
        )
    except Exception:
        pass
    return result


@opik.track(name="stage:qa", type="tool")
def step_qa_engineer(
    context: str,
    runner: str = "claude",
    runner_model: str | None = DEFAULT_GEMINI_MODEL,
    copilot_effort: str | None = None,
) -> str:
    change_id = _extract_change_id(context)
    _annotate_trace(stage="qa", runner=runner, change_id=change_id)
    return run_agent_cmd(
        runner=runner,
        prompt=context,
        agent="qa",
        **_agent_runner_kwargs(runner_model, copilot_effort),
    )


@opik.track(name="stage:qa-evaluator", type="tool")
def step_qa_evaluator(
    context: str,
    runner: str = "claude",
    runner_model: str | None = DEFAULT_GEMINI_MODEL,
    copilot_effort: str | None = None,
) -> str:
    change_id = _extract_change_id(context)
    _annotate_trace(stage="qa-evaluator", runner=runner, change_id=change_id)
    result = call_evaluator_sdk(context, "qa-evaluator", runner=runner, runner_model=runner_model)
    passed = "PASS" in result
    try:
        opik_context.update_current_trace(
            feedback_scores=[{"name": "evaluator_pass", "value": 1.0 if passed else 0.0}],
            metadata={"evaluator": "qa-evaluator", "passed": passed},
        )
    except Exception:
        pass
    return result


@opik.track(name="stage:lessons-optimizer", type="tool")
def step_lessons_optimizer(
    change_id: str,
    repo: str,
    runner: str = "claude",
    runner_model: str | None = DEFAULT_GEMINI_MODEL,
    copilot_effort: str | None = None,
) -> str:
    _annotate_trace(stage="lessons-optimizer", runner=runner, change_id=change_id)
    prompt = (
        f"Run the end-of-workflow lessons optimization for change {change_id}.\n"
        f"Read {AGENT_CONTEXT_ROOT}/lessons.md for recorded lessons.\n"
        f"Read all execution artifacts under {AGENT_CONTEXT_ROOT}/{change_id}/.\n"
        f"Target repo: {repo}\n"
        f"Write your report to {AGENT_CONTEXT_ROOT}/{change_id}/summary/lessons_optimizer_report.yaml.\n"
        f"Act immediately. Do not ask questions."
    )
    return run_agent_cmd(
        runner=runner,
        prompt=prompt,
        agent="lessons-optimizer-hyperagent",
        **_agent_runner_kwargs(runner_model, copilot_effort),
    )
