from __future__ import annotations

import textwrap
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .models import WorkflowConfig


def format_human_resolution(resolution: dict[str, Any]) -> str:
    """Format a resume.json resolution dict into a prompt-friendly text block."""

    lines = ["HUMAN RESOLUTION (authoritative — overrides prior assumptions)"]
    if resolution.get("responder"):
        lines.append(f"- Responder: {resolution['responder']}")
    if resolution.get("timestamp"):
        lines.append(f"- Timestamp: {resolution['timestamp']}")

    answers = resolution.get("answers", {})
    if answers:
        lines.append("- Answers:")
        for key, value in answers.items():
            lines.append(f"  - {key}: {value}")

    constraints = resolution.get("constraints", [])
    if constraints:
        lines.append("- Constraints:")
        for constraint in constraints:
            lines.append(f"  - {constraint}")

    extra = resolution.get("extra_context")
    if extra:
        lines.append(f"- Extra context: {extra}")

    return "\n".join(lines)


def _artifact_lines(artifact_paths: Iterable[Path]) -> str:
    return "\n".join(f"- {path}" for path in artifact_paths)


def build_intake_prompt(config: WorkflowConfig) -> str:
    """Create the non-interactive prompt for the intake agent."""

    return textwrap.dedent(
        f"""
        Automation run for stage `intake`. Do not ask the user questions.

        Workflow assets root: {config.workflow_assets_root}
        Code repo: {config.repo_root}
        Artifact root: {config.artifact_root}
        Change ID: {config.change_id}

        Treat the following context as already supplied by the workflow runner.
        Your job in this stage is only to normalize the intake and create or
        refresh these artifacts under `{config.artifact_root / config.change_id}`:

        - `intake/story.yaml`
        - `intake/config.yaml`
        - `intake/constraints.md`

        Do not orchestrate later stages, run evaluator loops, invoke other
        agents, or tell the user how to continue the workflow. Capture missing
        details as open questions inside `constraints.md`.

        Context:
        {config.context}
        """
    ).strip()


def build_producer_prompt(
    config: WorkflowConfig,
    stage_label: str,
    attempt: int,
    artifact_paths: Iterable[Path],
    feedback_path: Path | None = None,
    feedback_summary: str | None = None,
    uow_id: str | None = None,
    human_resolution: dict[str, Any] | None = None,
) -> str:
    """Build a prompt for a non-evaluator stage agent."""

    feedback_line = "This is the first attempt for this stage."
    if feedback_path is not None:
        feedback_line = (
            f"Previous evaluator feedback is available at `{feedback_path}`. "
            "Address it fully."
        )
        if feedback_summary:
            feedback_line += f"\n\nEvaluator fixes to apply:\n{feedback_summary}"
    elif feedback_summary:
        feedback_line = (
            "The previous attempt did not complete successfully. "
            "Address the runner-captured failure details below."
        )
        feedback_line += f"\n\nRunner-captured failure details:\n{feedback_summary}"
    uow_line = (
        f"Target only `{uow_id}`. Stay within that UoW's scope." if uow_id else ""
    )

    resolution_block = ""
    if human_resolution:
        resolution_block = "\n\n" + format_human_resolution(human_resolution)

    schema_section = ""
    if stage_label == "task_assigner":
        schema_section = textwrap.dedent(
            """

            ## ASSIGNMENTS.JSON SCHEMA (Required Format)

            The assignments.json artifact MUST conform to this exact structure:

            ```json
            {
              "batches": [
                {
                  "batch_id": 1,
                  "uows": [
                    {
                      "uow_id": "UOW-001",
                      "source_task_id": "T1",
                      "title": "Optional title",
                      "dependencies": [],
                      "definition_of_done": []
                    }
                  ]
                }
              ]
            }
            ```

            CRITICAL REQUIREMENTS:
            - Root key MUST be "batches" (NOT "execution_schedule")
            - batches MUST be an array of batch objects
            - Each batch MUST have "batch_id" (integer) and "uows" (array)
            - Each UoW MUST have "uow_id" (string) and "source_task_id" (string)
            - All string fields must be non-empty
            - All required fields must be present in every object
            """
        ).strip()

    return textwrap.dedent(
        f"""
        Automation run for stage `{stage_label}`.
        Workflow assets root: {config.workflow_assets_root}
        Code repo: {config.repo_root}
        Artifact root: {config.artifact_root}
        Change ID: {config.change_id}
        Attempt number: {attempt}
        {uow_line}

        Required artifacts for this stage:
        {_artifact_lines(artifact_paths)}

        {feedback_line}{resolution_block}{schema_section}

        Do the documented work for your agent and write or update the expected
        artifacts in the change folder. Return a concise status summary only.
        """
    ).strip()


def build_evaluator_prompt(
    config: WorkflowConfig,
    stage_label: str,
    attempt: int,
    artifact_paths: Iterable[Path],
    uow_id: str | None = None,
) -> str:
    """Build a prompt for an evaluator stage agent."""

    uow_line = f"Evaluate only `{uow_id}`." if uow_id else ""

    return textwrap.dedent(
        f"""
        Automation run for evaluator stage `{stage_label}`.
        Workflow assets root: {config.workflow_assets_root}
        Code repo: {config.repo_root}
        Artifact root: {config.artifact_root}
        Change ID: {config.change_id}
        Attempt number: {attempt}
        {uow_line}

        Evaluate the current stage artifacts:
        {_artifact_lines(artifact_paths)}

        Write the evaluation artifact for attempt {attempt} in the documented
        location and return a concise pass/fail summary only.
        """
    ).strip()


def build_lessons_prompt(config: WorkflowConfig) -> str:
    """Build a prompt for the lessons optimizer stage."""

    return textwrap.dedent(
        f"""
        Automation run for the lessons optimization stage.
        Workflow assets root: {config.workflow_assets_root}
        Code repo: {config.repo_root}
        Artifact root: {config.artifact_root}
        Change ID: {config.change_id}

        Read the workflow artifacts for this change and run the terminal lessons
        optimization stage. Write the summary artifact and return a concise status
        summary only.
        """
    ).strip()

