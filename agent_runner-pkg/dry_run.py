from __future__ import annotations

import textwrap

from .artifacts import (
    assignment_eval_path,
    assignments_path,
    config_path,
    constraints_path,
    impl_eval_path,
    impl_report_path,
    iso_now,
    lessons_report_path,
    qa_eval_path,
    qa_report_path,
    story_path,
    task_plan_eval_path,
    tasks_path,
    uow_spec_path,
    write_json,
    write_text,
)
from .models import WorkflowConfig, WorkflowError


def dry_run_story_yaml(config: WorkflowConfig) -> str:
    """Generate a minimal story artifact for dry-run mode."""

    escaped_context = config.context.strip().replace('"', "'")
    return (
        textwrap.dedent(
            f"""
            change_id: "{config.change_id}"
            title: "Dry-run workflow"
            description: "Synthetic story created by agent_workflow_runner dry-run mode"
            acceptance_criteria:
              - id: "AC1"
                description: "Workflow runner completes all documented stages"
                testable: true
                notes: null
            examples: []
            constraints: []
            non_functional_requirements: []
            raw_input: "{escaped_context}"
            ado_provenance:
              work_item_id: null
              organization: null
              project: null
              fields_auto_filled: []
            planning_docs: []
            """
        ).strip()
        + "\n"
    )


def dry_run_config_yaml(config: WorkflowConfig) -> str:
    """Generate a minimal config artifact for dry-run mode."""

    return (
        textwrap.dedent(
            f"""
            change_id: "{config.change_id}"
            code_repo: "{config.repo_root}"
            project_type: "brownfield"
            planning_docs_root: ""
            planning_docs_paths: []
            created_at: "{iso_now()}"
            model_assignments: {{}}
            iteration_limits:
              task_plan: {config.max_task_plan_attempts}
              assignment: {config.max_assignment_attempts}
              implementation: {config.max_implementation_attempts}
              qa: {config.max_qa_attempts}
            run_metadata:
              status: "intake_complete"
              current_stage: "intake"
              started_at: "{iso_now()}"
            """
        ).strip()
        + "\n"
    )


def materialize_dry_run_artifacts(
    config: WorkflowConfig,
    stage_key: str,
    attempt: int,
    uow_id: str | None = None,
) -> list:
    """Create synthetic artifacts so dry-run mode exercises control flow end-to-end."""

    created: list = []

    if stage_key == "intake":
        write_text(story_path(config), dry_run_story_yaml(config))
        write_text(config_path(config), dry_run_config_yaml(config))
        write_text(
            constraints_path(config),
            "# Dry-run constraints\n\n- No additional constraints.\n",
        )
        created.extend(
            [story_path(config), config_path(config), constraints_path(config)]
        )
    elif stage_key == "task_generator":
        write_text(
            tasks_path(config),
            (
                textwrap.dedent(
                    f"""
                    story_id: "{config.change_id}"
                    tasks:
                      - id: "T1"
                        task_id: "T1"
                        title: "Dry-run task"
                        description: "Validate the workflow runner path"
                        ac_mapping: ["AC1"]
                        acceptance_criteria_mapped: ["AC1"]
                        dependencies: []
                        priority: "high"
                        complexity: "simple"
                        estimated_complexity: "simple"
                        definition_of_done:
                          - "Workflow runner completes all documented stages"
                    ac_coverage_matrix:
                      AC1: ["T1"]
                    notes: "Synthetic dry-run tasks"
                    """
                ).strip()
                + "\n"
            ),
        )
        created.append(tasks_path(config))
    elif stage_key == "task_plan_evaluator":
        write_json(
            task_plan_eval_path(config, attempt),
            {
                "evaluation_id": f"DRY-TASK-{attempt}",
                "artifact_evaluated": "planning/tasks.yaml",
                "attempt_number": attempt,
                "overall_result": "pass",
                "score": 100,
                "programmatic_gates": {"all_gates_passed": True},
                "rubric_results": {},
                "issues": [],
                "actionable_fixes_summary": [],
                "escalation_recommendation": {"required": False, "reason": None},
                "notes": "Dry-run evaluation passed.",
            },
        )
        created.append(task_plan_eval_path(config, attempt))
    elif stage_key == "task_assigner":
        write_json(
            assignments_path(config),
            {
                "story_id": config.change_id,
                "batches": [
                    {
                        "batch_id": 1,
                        "batch": 1,
                        "parallel_execution": False,
                        "batch_rationale": "Dry-run batch",
                        "uows": [
                            {
                                "uow_id": "UOW-001",
                                "source_task_id": "T1",
                                "assigned_role": "software-engineer",
                                "priority_in_batch": 1,
                                "rationale": "Dry-run execution",
                                "dependencies": [],
                                "definition_of_done": [
                                    "Workflow runner reaches implementation stage"
                                ],
                            }
                        ],
                    }
                ],
                "critical_path": ["UOW-001"],
                "estimated_total_batches": 1,
            },
        )
        created.append(assignments_path(config))
    elif stage_key == "assignment_evaluator":
        write_json(
            assignment_eval_path(config, attempt),
            {
                "evaluation_id": f"DRY-ASG-{attempt}",
                "artifact_evaluated": "planning/assignments.json",
                "attempt_number": attempt,
                "overall_result": "pass",
                "score": 100,
                "programmatic_gates": {"all_gates_passed": True},
                "rubric_results": {},
                "issues": [],
                "actionable_fixes_summary": [],
                "escalation_recommendation": {"required": False, "reason": None},
                "notes": "Dry-run evaluation passed.",
            },
        )
        created.append(assignment_eval_path(config, attempt))
    elif stage_key == "software_engineer":
        if not uow_id:
            raise WorkflowError("dry-run software_engineer stage requires a uow_id")
        write_text(
            uow_spec_path(config, uow_id),
            (
                textwrap.dedent(
                    f"""
                    uow_id: "{uow_id}"
                    source_task_id: "T1"
                    description: "Dry-run UoW"
                    definition_of_done:
                      - "Runner reaches implementation stage"
                    """
                ).strip()
                + "\n"
            ),
        )
        write_text(
            impl_report_path(config, uow_id),
            (
                textwrap.dedent(
                    f"""
                    uow_id: "{uow_id}"
                    status: "complete"
                    implementation_summary: "Dry-run implementation completed"
                    summary: "Dry-run implementation completed"
                    files_modified: []
                    definition_of_done_status:
                      - item: "Runner reaches implementation stage"
                        met: true
                        evidence: "Synthetic dry-run evidence"
                    """
                ).strip()
                + "\n"
            ),
        )
        created.extend([uow_spec_path(config, uow_id), impl_report_path(config, uow_id)])
    elif stage_key == "implementation_evaluator":
        if not uow_id:
            raise WorkflowError(
                "dry-run implementation_evaluator stage requires a uow_id"
            )
        write_json(
            impl_eval_path(config, uow_id, attempt),
            {
                "evaluation_id": f"DRY-IMPL-{uow_id}-{attempt}",
                "artifact_evaluated": f"execution/{uow_id}/impl_report.yaml",
                "attempt_number": attempt,
                "overall_result": "pass",
                "score": 100,
                "programmatic_gates": {"all_gates_passed": True},
                "rubric_results": {},
                "issues": [],
                "actionable_fixes_summary": [],
                "escalation_recommendation": {"required": False, "reason": None},
                "notes": "Dry-run implementation evaluation passed.",
            },
        )
        created.append(impl_eval_path(config, uow_id, attempt))
    elif stage_key == "qa":
        write_text(
            qa_report_path(config),
            (
                textwrap.dedent(
                    f"""
                    change_id: "{config.change_id}"
                    overall_status: "pass"
                    ac_validations:
                      - ac_id: "AC1"
                        status: "pass"
                        evidence:
                          - "Dry-run workflow completed."
                    acceptance_criteria_validation:
                      - id: "AC1"
                        result: "pass"
                        evidence: "Dry-run workflow completed."
                    regression_risk:
                      level: "low"
                      notes: "Synthetic dry-run risk profile."
                    """
                ).strip()
                + "\n"
            ),
        )
        created.append(qa_report_path(config))
    elif stage_key == "qa_evaluator":
        write_json(
            qa_eval_path(config, attempt),
            {
                "evaluation_id": f"DRY-QA-{attempt}",
                "artifact_evaluated": "qa/qa_report.yaml",
                "attempt_number": attempt,
                "overall_result": "pass",
                "score": 100,
                "programmatic_gates": {"all_gates_passed": True},
                "rubric_results": {},
                "issues": [],
                "actionable_fixes_summary": [],
                "escalation_recommendation": {"required": False, "reason": None},
                "notes": "Dry-run QA evaluation passed.",
            },
        )
        created.append(qa_eval_path(config, attempt))
    elif stage_key == "lessons_optimizer":
        write_text(
            lessons_report_path(config),
            (
                textwrap.dedent(
                    f"""
                    change_id: "{config.change_id}"
                    status: "complete"
                    summary: "Dry-run lessons stage completed"
                    recommendations: []
                    """
                ).strip()
                + "\n"
            ),
        )
        created.append(lessons_report_path(config))

    return created
