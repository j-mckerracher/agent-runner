from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..artifacts import (
    assignment_eval_path,
    assignments_path,
    constraints_path,
    qa_eval_path,
    qa_report_path,
    story_path,
    task_plan_eval_path,
    tasks_path,
)
from ..models import WorkflowConfig

ArtifactFactory = Callable[[WorkflowConfig], list[Path]]
EvaluationPathFactory = Callable[[WorkflowConfig, int], Path]

TOTAL_WORKFLOW_STAGES = 6
INTAKE_STAGE_NUMBER = 1
SOFTWARE_ENGINEER_STAGE_NUMBER = 4
LESSONS_STAGE_NUMBER = 6


@dataclass(frozen=True)
class LoopStageSpec:
    """Central definition of a producer/evaluator loop stage."""

    stage_name: str
    stage_number: int
    banner_title: str
    producer_stage_key: str
    evaluator_stage_key: str
    max_attempts_attr: str
    producer_artifacts_factory: ArtifactFactory
    evaluator_artifacts_factory: ArtifactFactory
    evaluation_path_factory: EvaluationPathFactory


def _task_generator_producer_artifacts(config: WorkflowConfig) -> list[Path]:
    return [story_path(config), constraints_path(config)]


def _task_generator_evaluator_artifacts(config: WorkflowConfig) -> list[Path]:
    return [tasks_path(config), story_path(config)]


def _task_assigner_producer_artifacts(config: WorkflowConfig) -> list[Path]:
    return [tasks_path(config), story_path(config), constraints_path(config)]


def _task_assigner_evaluator_artifacts(config: WorkflowConfig) -> list[Path]:
    return [assignments_path(config), tasks_path(config)]


def _qa_producer_artifacts(config: WorkflowConfig) -> list[Path]:
    return [
        story_path(config),
        tasks_path(config),
        assignments_path(config),
        config.artifact_root / config.change_id / "execution",
        qa_report_path(config),
    ]


def _qa_evaluator_artifacts(config: WorkflowConfig) -> list[Path]:
    return [qa_report_path(config), story_path(config)]


TASK_PLAN_STAGE = LoopStageSpec(
    stage_name="task_generator",
    stage_number=2,
    banner_title="STAGE 2+3/6 — task-generator ↔ task-plan-evaluator",
    producer_stage_key="task_generator",
    evaluator_stage_key="task_plan_evaluator",
    max_attempts_attr="max_task_plan_attempts",
    producer_artifacts_factory=_task_generator_producer_artifacts,
    evaluator_artifacts_factory=_task_generator_evaluator_artifacts,
    evaluation_path_factory=task_plan_eval_path,
)

ASSIGNMENT_STAGE = LoopStageSpec(
    stage_name="task_assigner",
    stage_number=3,
    banner_title="STAGE 4+5/6 — task-assigner ↔ assignment-evaluator",
    producer_stage_key="task_assigner",
    evaluator_stage_key="assignment_evaluator",
    max_attempts_attr="max_assignment_attempts",
    producer_artifacts_factory=_task_assigner_producer_artifacts,
    evaluator_artifacts_factory=_task_assigner_evaluator_artifacts,
    evaluation_path_factory=assignment_eval_path,
)

QA_STAGE = LoopStageSpec(
    stage_name="qa",
    stage_number=5,
    banner_title="QA — qa-engineer ↔ qa-evaluator",
    producer_stage_key="qa",
    evaluator_stage_key="qa_evaluator",
    max_attempts_attr="max_qa_attempts",
    producer_artifacts_factory=_qa_producer_artifacts,
    evaluator_artifacts_factory=_qa_evaluator_artifacts,
    evaluation_path_factory=qa_eval_path,
)

LOOP_STAGE_SPECS = (TASK_PLAN_STAGE, ASSIGNMENT_STAGE, QA_STAGE)

