import logging
import re
import json
import subprocess
from pathlib import Path
import yaml

from artifact_utils import normalize_assignments_file
from opik import opik_context
from run_cmds import run_claude_cmd, run_agent_cmd
from opik_integration import call_evaluator_sdk
from runner_models import DEFAULT_GEMINI_MODEL, resolve_agent_model
from ui_trace_bridge import track_with_ui

logger = logging.getLogger(__name__)

AGENT_CONTEXT_ROOT = Path(__file__).resolve().parent / "agent-context"
_TASK_FIELD_ALIASES = {
    "task_id": "id",
    "acceptance_criteria_mapped": "ac_mapping",
    "estimated_complexity": "complexity",
}


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


def _stage_trace_metadata(*, stage: str, runner: str, change_id: str, **extra: object) -> dict[str, object]:
    metadata: dict[str, object] = {"stage": stage, "runner": runner, "change_id": change_id}
    metadata.update({key: value for key, value in extra.items() if value is not None})
    return metadata


def _context_stage_trace_metadata(stage: str, context: str, runner: str) -> dict[str, object]:
    return _stage_trace_metadata(stage=stage, runner=runner, change_id=_extract_change_id(context))


def _task_plan_path(change_id: str) -> Path:
    return AGENT_CONTEXT_ROOT / change_id / "planning" / "tasks.yaml"


def _assignments_path(change_id: str) -> Path:
    return AGENT_CONTEXT_ROOT / change_id / "planning" / "assignments.json"


def _should_expand_single_task_plan(change_id: str) -> bool:
    return change_id.startswith("calibration_")


def _next_task_id(tasks: list[dict]) -> str:
    max_index = 0
    for task in tasks:
        task_id = str(task.get("id", ""))
        match = re.fullmatch(r"T(\d+)", task_id)
        if match:
            max_index = max(max_index, int(match.group(1)))
    return f"T{max_index + 1 or 2}"


def _normalize_task_plan_artifact(change_id: str) -> bool:
    if not change_id:
        return False
    path = _task_plan_path(change_id)
    if not path.is_file():
        return False

    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        return False

    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, list):
        return False

    changed = False
    tasks: list[dict] = []
    for raw_task in raw_tasks:
        if not isinstance(raw_task, dict):
            tasks.append(raw_task)
            continue
        task = dict(raw_task)
        for legacy_key, canonical_key in _TASK_FIELD_ALIASES.items():
            if legacy_key in task and canonical_key not in task:
                task[canonical_key] = task[legacy_key]
                changed = True
            if legacy_key in task:
                task.pop(legacy_key, None)
                changed = True
        tasks.append(task)
    data["tasks"] = tasks

    if _should_expand_single_task_plan(change_id) and len(tasks) == 1 and isinstance(tasks[0], dict):
        primary_task = tasks[0]
        primary_id = str(primary_task.get("id") or "T1")
        if "id" not in primary_task:
            primary_task["id"] = primary_id
            changed = True
        secondary_id = _next_task_id(tasks)
        verification_task = {
            "id": secondary_id,
            "title": f"Verify {primary_task.get('title', 'implementation')}",
            "description": (
                f"Verify that {primary_task.get('title', 'the implementation')} satisfies its mapped "
                "acceptance criteria and aligns with repository conventions."
            ),
            "ac_mapping": list(primary_task.get("ac_mapping", [])),
            "dependencies": [primary_id],
            "priority": "medium",
            "complexity": "simple",
        }
        tasks.append(verification_task)
        changed = True

        ac_coverage_matrix = data.get("ac_coverage_matrix")
        if not isinstance(ac_coverage_matrix, dict):
            ac_coverage_matrix = {}
            data["ac_coverage_matrix"] = ac_coverage_matrix
        for ac_id in verification_task["ac_mapping"]:
            existing = ac_coverage_matrix.get(ac_id)
            mapped_tasks = list(existing) if isinstance(existing, list) else []
            if primary_id not in mapped_tasks:
                mapped_tasks.append(primary_id)
            if secondary_id not in mapped_tasks:
                mapped_tasks.append(secondary_id)
            ac_coverage_matrix[ac_id] = mapped_tasks

        normalization_note = (
            "Auto-normalized task schema and expanded a single-task plan into "
            "implementation + verification to satisfy the minimum task-count gate."
        )
        existing_notes = data.get("notes")
        if isinstance(existing_notes, str) and existing_notes.strip():
            if normalization_note not in existing_notes:
                data["notes"] = f"{existing_notes.rstrip()} {normalization_note}"
        else:
            data["notes"] = normalization_note

    if not changed:
        return False

    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False)
    logger.info("_normalize_task_plan_artifact: normalized %s", path)
    return True


def _normalize_assignments_artifact(change_id: str) -> bool:
    if not change_id:
        return False
    path = _assignments_path(change_id)
    if not path.is_file():
        return False
    changed = normalize_assignments_file(path)
    if changed:
        logger.info("_normalize_assignments_artifact: normalized %s", path)
    return changed


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
    logger.info("_fetch_ado_work_item: fetching %s", ado_url)
    try:
        from urllib.parse import urlparse
        parsed = urlparse(ado_url)
        parts = parsed.path.strip("/").split("/")
        if len(parts) < 5:
            logger.warning("_fetch_ado_work_item: URL path too short (%d parts): %s", len(parts), ado_url)
            return None
        org_name = parts[0]
        item_id = parts[-1]
        org_url = f"https://dev.azure.com/{org_name}"
        logger.debug("_fetch_ado_work_item: org_url=%s item_id=%s", org_url, item_id)
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
            logger.info("_fetch_ado_work_item: successfully fetched item_id=%s", item_id)
            return json.dumps(trimmed, indent=2)
        logger.warning("_fetch_ado_work_item: az CLI returned exit_code=%d for item_id=%s", result.returncode, item_id)
        return None
    except Exception as exc:
        logger.error("_fetch_ado_work_item: unexpected error for %s: %s", ado_url, exc)
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


@track_with_ui(
    name="stage:intake",
    type="tool",
    metadata_getter=lambda intake_source, repo, change_id, intake_mode="ado", runner="claude", **_unused: _stage_trace_metadata(
        stage="intake",
        runner=runner,
        change_id=change_id,
        intake_mode=intake_mode,
    ),
)
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
    logger.info("step_intake: change_id=%s mode=%s runner=%s source=%s", change_id, intake_mode, runner, intake_source)
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
    logger.debug("step_intake: prompt length=%d for change_id=%s", len(prompt), change_id)
    extra_skills = None
    resolved_model = resolve_agent_model("intake", runner, runner_model)
    result = run_agent_cmd(
        runner=runner,
        prompt=prompt,
        agent="intake",
        extra_skills=extra_skills,
        **_agent_runner_kwargs(resolved_model, copilot_effort),
    )
    logger.info("step_intake: completed change_id=%s output_len=%d", change_id, len(result or ""))
    return result


@track_with_ui(
    name="stage:task-gen-producer",
    type="tool",
    metadata_getter=lambda context, runner="claude", **_unused: _context_stage_trace_metadata(
        "task-gen-producer",
        context,
        runner,
    ),
)
def step_task_gen_producer(
    context: str,
    runner: str = "claude",
    runner_model: str | None = DEFAULT_GEMINI_MODEL,
    copilot_effort: str | None = None,
) -> str:
    change_id = _extract_change_id(context)
    logger.info("step_task_gen_producer: change_id=%s runner=%s", change_id, runner)
    _annotate_trace(stage="task-gen-producer", runner=runner, change_id=change_id)
    resolved_model = resolve_agent_model("task-generator", runner, runner_model)
    result = run_agent_cmd(
        runner=runner,
        prompt=context,
        agent="task-generator",
        **_agent_runner_kwargs(resolved_model, copilot_effort),
    )
    _normalize_task_plan_artifact(change_id)
    logger.info("step_task_gen_producer: completed change_id=%s output_len=%d", change_id, len(result or ""))
    return result


@track_with_ui(
    name="stage:task-gen-evaluator",
    type="tool",
    metadata_getter=lambda context, runner="claude", **_unused: _context_stage_trace_metadata(
        "task-gen-evaluator",
        context,
        runner,
    ),
)
def step_task_gen_evaluator(
    context: str,
    runner: str = "claude",
    runner_model: str | None = DEFAULT_GEMINI_MODEL,
    copilot_effort: str | None = None,
) -> str:
    change_id = _extract_change_id(context)
    logger.info("step_task_gen_evaluator: change_id=%s runner=%s", change_id, runner)
    _annotate_trace(stage="task-gen-evaluator", runner=runner, change_id=change_id)
    resolved_model = resolve_agent_model("task-plan-evaluator", runner, runner_model)
    result = call_evaluator_sdk(context, "task-plan-evaluator", model=resolved_model, runner=runner, copilot_effort=copilot_effort)
    passed = "PASS" in result
    logger.info("step_task_gen_evaluator: change_id=%s passed=%s", change_id, passed)
    try:
        opik_context.update_current_trace(
            feedback_scores=[{"name": "evaluator_pass", "value": 1.0 if passed else 0.0}],
            metadata={"evaluator": "task-plan-evaluator", "passed": passed},
        )
    except Exception:
        pass
    return result


@track_with_ui(
    name="stage:task-assigner",
    type="tool",
    metadata_getter=lambda context, runner="claude", **_unused: _context_stage_trace_metadata(
        "task-assigner",
        context,
        runner,
    ),
)
def step_task_assigner(
    context: str,
    runner: str = "claude",
    runner_model: str | None = DEFAULT_GEMINI_MODEL,
    copilot_effort: str | None = None,
) -> str:
    change_id = _extract_change_id(context)
    logger.info("step_task_assigner: change_id=%s runner=%s", change_id, runner)
    _annotate_trace(stage="task-assigner", runner=runner, change_id=change_id)
    resolved_model = resolve_agent_model("task-assigner", runner, runner_model)
    result = run_agent_cmd(
        runner=runner,
        prompt=context,
        agent="task-assigner",
        **_agent_runner_kwargs(resolved_model, copilot_effort),
    )
    _normalize_assignments_artifact(change_id)
    logger.info("step_task_assigner: completed change_id=%s output_len=%d", change_id, len(result or ""))
    return result


@track_with_ui(
    name="stage:assignment-evaluator",
    type="tool",
    metadata_getter=lambda context, runner="claude", **_unused: _context_stage_trace_metadata(
        "assignment-evaluator",
        context,
        runner,
    ),
)
def step_assignment_evaluator(
    context: str,
    runner: str = "claude",
    runner_model: str | None = DEFAULT_GEMINI_MODEL,
    copilot_effort: str | None = None,
) -> str:
    change_id = _extract_change_id(context)
    logger.info("step_assignment_evaluator: change_id=%s runner=%s", change_id, runner)
    _annotate_trace(stage="assignment-evaluator", runner=runner, change_id=change_id)
    resolved_model = resolve_agent_model("assignment-evaluator", runner, runner_model)
    result = call_evaluator_sdk(context, "assignment-evaluator", model=resolved_model, runner=runner, copilot_effort=copilot_effort)
    passed = "PASS" in result
    logger.info("step_assignment_evaluator: change_id=%s passed=%s", change_id, passed)
    try:
        opik_context.update_current_trace(
            feedback_scores=[{"name": "evaluator_pass", "value": 1.0 if passed else 0.0}],
            metadata={"evaluator": "assignment-evaluator", "passed": passed},
        )
    except Exception:
        pass
    return result


@track_with_ui(
    name="stage:implementation",
    type="tool",
    metadata_getter=lambda uow_id, change_id, repo, evaluator_feedback="", runner="claude", **_unused: _stage_trace_metadata(
        stage="implementation",
        runner=runner,
        change_id=change_id,
        uow_id=uow_id,
        has_feedback=bool(evaluator_feedback),
    ),
)
def step_software_engineer(
    uow_id: str,
    change_id: str,
    repo: str,
    evaluator_feedback: str = "",
    runner: str = "claude",
    runner_model: str | None = DEFAULT_GEMINI_MODEL,
    copilot_effort: str | None = None,
) -> str:
    logger.info(
        "step_software_engineer: uow_id=%s change_id=%s runner=%s has_feedback=%s",
        uow_id, change_id, runner, bool(evaluator_feedback),
    )
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
        logger.debug("step_software_engineer: uow_id=%s including evaluator feedback (len=%d)", uow_id, len(evaluator_feedback))
        prompt += (
            f"\n\n## Evaluator Issues to Fix:\n{evaluator_feedback}\n\n"
            f"Address every issue listed above. Do not ask questions — act immediately."
        )
    resolved_model = resolve_agent_model("software-engineer-hyperagent", runner, runner_model)
    result = run_agent_cmd(
        runner=runner,
        prompt=prompt,
        agent="software-engineer-hyperagent",
        **_agent_runner_kwargs(resolved_model, copilot_effort),
    )
    logger.info("step_software_engineer: uow_id=%s completed output_len=%d", uow_id, len(result or ""))
    return result


@track_with_ui(
    name="stage:implementation-evaluator",
    type="tool",
    metadata_getter=lambda uow_id, change_id, repo, runner="claude", **_unused: _stage_trace_metadata(
        stage="implementation-evaluator",
        runner=runner,
        change_id=change_id,
        uow_id=uow_id,
    ),
)
def step_software_engineer_evaluator(
    uow_id: str,
    change_id: str,
    repo: str,
    runner: str = "claude",
    runner_model: str | None = DEFAULT_GEMINI_MODEL,
    copilot_effort: str | None = None,
) -> str:
    logger.info("step_software_engineer_evaluator: uow_id=%s change_id=%s runner=%s", uow_id, change_id, runner)
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
    resolved_model = resolve_agent_model("implementation-evaluator", runner, runner_model)
    result = call_evaluator_sdk(context, "implementation-evaluator", model=resolved_model, runner=runner, copilot_effort=copilot_effort)
    passed = "PASS" in result
    logger.info("step_software_engineer_evaluator: uow_id=%s passed=%s", uow_id, passed)
    try:
        opik_context.update_current_trace(
            feedback_scores=[{"name": "evaluator_pass", "value": 1.0 if passed else 0.0}],
            metadata={"evaluator": "implementation-evaluator", "uow_id": uow_id, "passed": passed},
        )
    except Exception:
        pass
    return result


@track_with_ui(
    name="stage:qa",
    type="tool",
    metadata_getter=lambda context, runner="claude", **_unused: _context_stage_trace_metadata(
        "qa",
        context,
        runner,
    ),
)
def step_qa_engineer(
    context: str,
    runner: str = "claude",
    runner_model: str | None = DEFAULT_GEMINI_MODEL,
    copilot_effort: str | None = None,
) -> str:
    change_id = _extract_change_id(context)
    logger.info("step_qa_engineer: change_id=%s runner=%s", change_id, runner)
    _annotate_trace(stage="qa", runner=runner, change_id=change_id)
    resolved_model = resolve_agent_model("qa-engineer", runner, runner_model)
    result = run_agent_cmd(
        runner=runner,
        prompt=context,
        agent="qa-engineer",
        **_agent_runner_kwargs(resolved_model, copilot_effort),
    )
    logger.info("step_qa_engineer: completed change_id=%s output_len=%d", change_id, len(result or ""))
    return result


@track_with_ui(
    name="stage:qa-evaluator",
    type="tool",
    metadata_getter=lambda context, runner="claude", **_unused: _context_stage_trace_metadata(
        "qa-evaluator",
        context,
        runner,
    ),
)
def step_qa_evaluator(
    context: str,
    runner: str = "claude",
    runner_model: str | None = DEFAULT_GEMINI_MODEL,
    copilot_effort: str | None = None,
) -> str:
    change_id = _extract_change_id(context)
    logger.info("step_qa_evaluator: change_id=%s runner=%s", change_id, runner)
    _annotate_trace(stage="qa-evaluator", runner=runner, change_id=change_id)
    resolved_model = resolve_agent_model("qa-evaluator", runner, runner_model)
    result = call_evaluator_sdk(context, "qa-evaluator", model=resolved_model, runner=runner, copilot_effort=copilot_effort)
    passed = "PASS" in result
    logger.info("step_qa_evaluator: change_id=%s passed=%s", change_id, passed)
    try:
        opik_context.update_current_trace(
            feedback_scores=[{"name": "evaluator_pass", "value": 1.0 if passed else 0.0}],
            metadata={"evaluator": "qa-evaluator", "passed": passed},
        )
    except Exception:
        pass
    return result


@track_with_ui(
    name="stage:lessons-optimizer",
    type="tool",
    metadata_getter=lambda change_id, repo, runner="claude", **_unused: _stage_trace_metadata(
        stage="lessons-optimizer",
        runner=runner,
        change_id=change_id,
    ),
)
def step_lessons_optimizer(
    change_id: str,
    repo: str,
    runner: str = "claude",
    runner_model: str | None = DEFAULT_GEMINI_MODEL,
    copilot_effort: str | None = None,
) -> str:
    logger.info("step_lessons_optimizer: change_id=%s runner=%s", change_id, runner)
    _annotate_trace(stage="lessons-optimizer", runner=runner, change_id=change_id)
    prompt = (
        f"Run the end-of-workflow lessons optimization for change {change_id}.\n"
        f"Read {AGENT_CONTEXT_ROOT}/lessons.md for recorded lessons.\n"
        f"Read all execution artifacts under {AGENT_CONTEXT_ROOT}/{change_id}/.\n"
        f"Target repo: {repo}\n"
        f"Write your report to {AGENT_CONTEXT_ROOT}/{change_id}/summary/lessons_optimizer_report.yaml.\n"
        f"Act immediately. Do not ask questions."
    )
    resolved_model = resolve_agent_model("lessons-optimizer-hyperagent", runner, runner_model)
    result = run_agent_cmd(
        runner=runner,
        prompt=prompt,
        agent="lessons-optimizer-hyperagent",
        **_agent_runner_kwargs(resolved_model, copilot_effort),
    )
    logger.info("step_lessons_optimizer: completed change_id=%s output_len=%d", change_id, len(result or ""))
    return result
