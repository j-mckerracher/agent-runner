from __future__ import annotations

import os
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import opik
from prefect import task

from evaluator_optimizer_loops import run_eval_optimizer_loop, run_uow_eval_loop
from opik_integration import call_evaluator_sdk
from run_cmds import run_agent_cmd
from runner_models import DEFAULT_GEMINI_MODEL

RUNNER_ROOT = Path(__file__).resolve().parent
DEFAULT_ARTIFACTS_ROOT = RUNNER_ROOT / "agent-context"


def _agent_runner_kwargs(runner_model: str | None) -> dict[str, str]:
    return {"runner_model": runner_model} if runner_model is not None else {}


@dataclass(frozen=True)
class WorkflowContext:
    run_id: str
    repo: str
    runner: str = "claude"
    runner_model: str | None = DEFAULT_GEMINI_MODEL
    artifact_root: Path | None = None
    opik_project_name: str = os.environ.get("OPIK_PROJECT_NAME", "agent-runner")

    def __post_init__(self) -> None:
        artifact_root = self.artifact_root or (DEFAULT_ARTIFACTS_ROOT / self.run_id)
        object.__setattr__(self, "artifact_root", Path(artifact_root).expanduser().resolve())

    @property
    def change_id(self) -> str:
        return self.run_id

    @property
    def artifact_root_relative(self) -> str:
        try:
            return self.artifact_root.relative_to(RUNNER_ROOT).as_posix()
        except ValueError:
            return self.artifact_root.as_posix()

    def prompt_values(self, **extra_values: Any) -> dict[str, Any]:
        values = {
            "run_id": self.run_id,
            "change_id": self.change_id,
            "repo": self.repo,
            "runner": self.runner,
            "runner_model": self.runner_model or "",
            "artifact_root": self.artifact_root_relative,
            "artifact_root_abs": str(self.artifact_root),
        }
        values.update(extra_values)
        return values


def render_prompt_template(prompt_template: str, workflow_context: WorkflowContext, **prompt_vars: Any) -> str:
    return prompt_template.format(**workflow_context.prompt_values(**prompt_vars))


def _resolve_trace_metadata(
    workflow_context: WorkflowContext | None,
    prompt: str,
    prompt_vars: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    run_id = prompt_vars.get("run_id") or prompt_vars.get("change_id")
    if workflow_context is not None:
        run_id = workflow_context.run_id
    metadata = {
        "prompt_preview": prompt[:500],
        "prompt_vars": prompt_vars,
    }
    return run_id or "", metadata


def make_agent_step(
    *,
    agent_name: str,
    trace_name: str,
    task_name: str | None = None,
    prompt_template: str | None = None,
) -> Callable[..., str]:
    @task(log_prints=True, name=task_name or trace_name)
    def agent_step(
        prompt: str | None = None,
        workflow_context: WorkflowContext | None = None,
        runner: str | None = None,
        runner_model: str | None = None,
        **prompt_vars: Any,
    ) -> str:
        if prompt is None:
            if workflow_context is None:
                raise ValueError("workflow_context is required when prompt is omitted.")
            if prompt_template is None:
                raise ValueError("prompt_template is required when prompt is omitted.")
            prompt = render_prompt_template(prompt_template, workflow_context, **prompt_vars)

        resolved_runner = runner or (workflow_context.runner if workflow_context else "claude")
        resolved_runner_model = runner_model if runner_model is not None else (
            workflow_context.runner_model if workflow_context else DEFAULT_GEMINI_MODEL
        )
        thread_id, metadata = _resolve_trace_metadata(workflow_context, prompt, prompt_vars)

        with opik.start_as_current_trace(trace_name, project_name=(
            workflow_context.opik_project_name if workflow_context else os.environ.get("OPIK_PROJECT_NAME", "agent-runner")
        )) as trace:
            trace.input = {
                "agent": agent_name,
                "runner": resolved_runner,
                **metadata,
            }
            trace.thread_id = thread_id
            result = run_agent_cmd(
                runner=resolved_runner,
                prompt=prompt,
                agent=agent_name,
                **_agent_runner_kwargs(resolved_runner_model),
            )
            trace.output = {"stdout_preview": result[:2000]}
        return result

    return agent_step


def make_sdk_evaluator_step(
    *,
    agent_name: str,
    trace_name: str,
    task_name: str | None = None,
    prompt_template: str | None = None,
    model: str = "claude-haiku-4-5-20251001",
) -> Callable[..., str]:
    @task(log_prints=True, name=task_name or trace_name)
    def evaluator_step(
        prompt: str | None = None,
        workflow_context: WorkflowContext | None = None,
        runner: str | None = None,
        runner_model: str | None = None,
        **prompt_vars: Any,
    ) -> str:
        if prompt is None:
            if workflow_context is None:
                raise ValueError("workflow_context is required when prompt is omitted.")
            if prompt_template is None:
                raise ValueError("prompt_template is required when prompt is omitted.")
            prompt = render_prompt_template(prompt_template, workflow_context, **prompt_vars)

        thread_id, metadata = _resolve_trace_metadata(workflow_context, prompt, prompt_vars)

        with opik.start_as_current_trace(trace_name, project_name=(
            workflow_context.opik_project_name if workflow_context else os.environ.get("OPIK_PROJECT_NAME", "agent-runner")
        )) as trace:
            trace.input = {
                "agent": agent_name,
                **metadata,
            }
            trace.thread_id = thread_id
            result = call_evaluator_sdk(prompt, agent_name, model=model)
            passed = "PASS" in result
            opik.opik_context.update_current_trace(
                feedback_scores=[{"name": "evaluator_pass", "value": 1.0 if passed else 0.0}],
                metadata={"evaluator": agent_name, "passed": passed},
            )
            trace.output = {"result": result}
        return result

    return evaluator_step


def load_workflow_artifact_json(
    workflow_context: WorkflowContext,
    relative_path: str,
    normalizer: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    artifact_path = workflow_context.artifact_root / relative_path
    with artifact_path.open("r", encoding="utf-8") as artifact_file:
        data = json.load(artifact_file)
    return normalizer(data) if normalizer else data


def normalize_execution_schedule(data: dict[str, Any]) -> dict[str, Any]:
    sched = data.get("execution_schedule", [])
    if isinstance(sched, dict):
        batches = [sched]
        i = 2
        while f"batch_{i}" in data:
            batches.append(data[f"batch_{i}"])
            i += 1
        data = {**data, "execution_schedule": batches}
    return data


def load_execution_plan(
    workflow_context: WorkflowContext,
    relative_path: str = "planning/assignments.json",
) -> dict[str, Any]:
    return load_workflow_artifact_json(
        workflow_context=workflow_context,
        relative_path=relative_path,
        normalizer=normalize_execution_schedule,
    )


__all__ = [
    "WorkflowContext",
    "load_execution_plan",
    "load_workflow_artifact_json",
    "make_agent_step",
    "make_sdk_evaluator_step",
    "normalize_execution_schedule",
    "render_prompt_template",
    "run_eval_optimizer_loop",
    "run_uow_eval_loop",
]
