from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from ..agents import discover_agents, resolve_agent
from ..artifacts import (
    assignments_path,
    change_root,
    constraints_path,
    impl_eval_path,
    impl_report_path,
    intake_artifact_paths,
    lessons_report_path,
    planning_dir,
    qa_report_path,
    story_path,
    task_plan_eval_path,
    tasks_path,
    uow_spec_path,
    write_runner_log,
)
from ..console import emit_event, log, print_stage_banner
from ..integrations.discord_resume import wait_for_resume, write_escalation_artifact
from ..integrations.observability import record_observability_event
from ..models import AgentSpec, StageResult, WorkflowConfig, WorkflowError
from ..prompts import (
    build_evaluator_prompt,
    build_intake_prompt,
    build_lessons_prompt,
    build_producer_prompt,
)
from ..runtime import (
    ensure_artifact_dirs,
    evaluation_signature,
    extract_command_failure_summary,
    extract_feedback_summary,
    invoke_agent,
    read_evaluation_result,
    validate_artifact_schema,
)
from .stages import (
    ASSIGNMENT_STAGE,
    INTAKE_STAGE_NUMBER,
    LESSONS_STAGE_NUMBER,
    LOOP_STAGE_SPECS,
    LoopStageSpec,
    QA_STAGE,
    SOFTWARE_ENGINEER_STAGE_NUMBER,
    TASK_PLAN_STAGE,
    TOTAL_WORKFLOW_STAGES,
)
from ..artifacts import load_execution_schedule, qa_eval_path


def _emit_structured_event(
    config: WorkflowConfig,
    event_type: str,
    **payload: Any,
) -> None:
    emit_event(event_type, **payload)
    record_observability_event(config, event_type, **payload)


def _validated_artifact_for_stage(
    config: WorkflowConfig,
    producer_stage_key: str,
) -> tuple[str, Path] | None:
    if producer_stage_key == "task_generator":
        return "tasks", tasks_path(config)
    if producer_stage_key == "task_assigner":
        return "assignments", assignments_path(config)
    return None


def run_stage_loop(
    config: WorkflowConfig,
    agents: dict[str, AgentSpec],
    producer_stage_key: str,
    evaluator_stage_key: str,
    producer_artifacts: list[Path],
    evaluator_artifacts: list[Path],
    evaluation_path_for_attempt: Callable[[int], Path],
    max_attempts: int,
) -> StageResult:
    """Run a producer/evaluator loop until pass or retries are exhausted."""

    producer = resolve_agent(agents, producer_stage_key)
    evaluator = resolve_agent(agents, evaluator_stage_key)
    previous_signature: str | None = None
    feedback_path: Path | None = None
    feedback_summary: str | None = None
    human_resolution: dict[str, Any] | None = None

    log(
        "INFO",
        f"Starting producer/evaluator loop: {producer_stage_key} ↔ "
        f"{evaluator_stage_key}  max_attempts={max_attempts}",
    )

    for attempt in range(1, max_attempts + 1):
        log(
            "INFO",
            f"[{producer_stage_key}] Attempt {attempt}/{max_attempts} — running "
            f"producer '{producer.key}'",
        )
        producer_prompt = build_producer_prompt(
            config=config,
            stage_label=producer_stage_key,
            attempt=attempt,
            artifact_paths=producer_artifacts,
            feedback_path=feedback_path,
            feedback_summary=feedback_summary,
            human_resolution=human_resolution,
        )
        producer_result = invoke_agent(
            config,
            producer,
            producer_prompt,
            producer_stage_key,
            attempt,
            raise_on_error=False,
        )
        if producer_result.exit_code != 0:
            feedback_path = None
            feedback_summary = extract_command_failure_summary(producer_result)
            log(
                "WARN",
                f"[{producer_stage_key}] Producer command failed on attempt {attempt} "
                f"(exit={producer_result.exit_code})",
            )
            log(
                "INFO",
                f"[{producer_stage_key}] Feedback for next attempt:\n{feedback_summary}",
            )
            continue

        validated = _validated_artifact_for_stage(config, producer_stage_key)
        if validated is not None:
            artifact_type, artifact_to_validate = validated
            if artifact_to_validate.exists():
                is_valid, validation_error = validate_artifact_schema(
                    config,
                    artifact_type,
                    artifact_to_validate,
                )
                if not is_valid:
                    feedback_path = None
                    feedback_summary = (
                        "CRITICAL: Schema validation failed before evaluation:\n"
                        f"{validation_error}"
                    )
                    log(
                        "WARN",
                        f"[{producer_stage_key}] Artifact schema validation FAILED "
                        f"on attempt {attempt}",
                    )
                    log(
                        "INFO",
                        f"[{producer_stage_key}] Feedback for next attempt:\n"
                        f"{feedback_summary}",
                    )
                    continue
                log("OK", f"[{producer_stage_key}] Artifact schema validation PASSED")

        evaluation_path = evaluation_path_for_attempt(attempt)
        log(
            "INFO",
            f"[{producer_stage_key}] Attempt {attempt}/{max_attempts} — running "
            f"evaluator '{evaluator.key}'",
        )
        evaluator_prompt = build_evaluator_prompt(
            config=config,
            stage_label=evaluator_stage_key,
            attempt=attempt,
            artifact_paths=evaluator_artifacts,
        )
        evaluator_result = invoke_agent(
            config,
            evaluator,
            evaluator_prompt,
            evaluator_stage_key,
            attempt,
            raise_on_error=False,
            early_exit_paths=[evaluation_path],
        )
        if evaluator_result.exit_code != 0:
            if evaluation_path.is_file():
                log(
                    "WARN",
                    f"[{producer_stage_key}] Evaluator exited {evaluator_result.exit_code} "
                    "but artifact already exists — using it",
                )
            else:
                feedback_path = None
                feedback_summary = extract_command_failure_summary(evaluator_result)
                log(
                    "WARN",
                    f"[{producer_stage_key}] Evaluator command failed on attempt "
                    f"{attempt} (exit={evaluator_result.exit_code})",
                )
                log(
                    "INFO",
                    f"[{producer_stage_key}] Feedback for next attempt:\n{feedback_summary}",
                )
                continue

        passed, payload = read_evaluation_result(evaluation_path)
        signature = evaluation_signature(payload)
        write_runner_log(
            config,
            "evaluation_result",
            {
                "stage_key": producer_stage_key,
                "evaluator_stage_key": evaluator_stage_key,
                "attempt": attempt,
                "passed": passed,
                "evaluation_path": str(evaluation_path),
            },
        )

        score = payload.get("score", "?")
        result_label = payload.get("overall_result", "?")
        if passed:
            log(
                "OK",
                f"[{producer_stage_key}] Evaluator PASSED on attempt {attempt}  "
                f"score={score}  result={result_label}",
            )
            _emit_structured_event(
                config,
                "eval_attempt",
                stage=producer_stage_key,
                attempt=attempt,
                max_attempts=max_attempts,
                passed=True,
                score=score,
            )
            return StageResult(
                stage_name=producer_stage_key,
                passed=True,
                attempts=attempt,
                artifact_paths=[*producer_artifacts, evaluation_path],
                details={"evaluation": payload},
            )

        log(
            "WARN",
            f"[{producer_stage_key}] Evaluator FAILED on attempt {attempt}  "
            f"score={score}  result={result_label}",
        )
        _emit_structured_event(
            config,
            "eval_attempt",
            stage=producer_stage_key,
            attempt=attempt,
            max_attempts=max_attempts,
            passed=False,
            score=score,
        )
        issues = payload.get("issues") or []
        for issue in issues:
            log("WARN", f"  Issue: {issue.get('description', issue)}")

        escalation_rec = payload.get("escalation_recommendation", {})
        escalation_required = (
            isinstance(escalation_rec, dict) and escalation_rec.get("required")
        )
        escalated_on_disk = (change_root(config) / "status" / "escalated.json").exists()

        if escalation_required or escalated_on_disk:
            _emit_structured_event(
                config,
                "escalation_start",
                stage=producer_stage_key,
                attempt=attempt,
            )
            if not escalated_on_disk:
                write_escalation_artifact(
                    config,
                    producer_stage_key,
                    evaluator_stage_key,
                    attempt,
                    payload,
                )
            resolution = wait_for_resume(config)
            if resolution:
                human_resolution = resolution
                feedback_path = evaluation_path
                feedback_summary = extract_feedback_summary(payload)
                log("INFO", f"[{producer_stage_key}] Retrying with human resolution")
                continue

        if signature == previous_signature:
            log(
                "ERROR",
                f"[{producer_stage_key}] Similarity plateau detected — aborting "
                f"after attempt {attempt}",
            )
            raise WorkflowError(
                f"Similarity plateau detected in stage '{producer_stage_key}' after "
                f"attempt {attempt}."
            )
        previous_signature = signature
        feedback_path = evaluation_path
        feedback_summary = extract_feedback_summary(payload)
        log(
            "INFO",
            f"[{producer_stage_key}] Feedback summary for next attempt:\n"
            f"{feedback_summary}",
        )

    raise WorkflowError(
        f"Stage '{producer_stage_key}' exceeded max attempts ({max_attempts}) "
        "without passing evaluation."
    )


def _run_loop_stage(
    config: WorkflowConfig,
    agents: dict[str, AgentSpec],
    spec: LoopStageSpec,
) -> StageResult:
    print_stage_banner(spec.banner_title)
    _emit_structured_event(
        config,
        "stage_start",
        stage=spec.stage_name,
        stage_number=spec.stage_number,
        total_stages=TOTAL_WORKFLOW_STAGES,
    )
    t0 = time.monotonic()
    result = run_stage_loop(
        config=config,
        agents=agents,
        producer_stage_key=spec.producer_stage_key,
        evaluator_stage_key=spec.evaluator_stage_key,
        producer_artifacts=spec.producer_artifacts_factory(config),
        evaluator_artifacts=spec.evaluator_artifacts_factory(config),
        evaluation_path_for_attempt=lambda attempt: spec.evaluation_path_factory(
            config,
            attempt,
        ),
        max_attempts=getattr(config, spec.max_attempts_attr),
    )
    log(
        "OK",
        f"Stage '{spec.stage_name}' complete  attempts={result.attempts}  "
        f"elapsed={time.monotonic() - t0:.1f}s",
    )
    _emit_structured_event(
        config,
        "stage_complete",
        stage=spec.stage_name,
        stage_number=spec.stage_number,
        passed=result.passed,
        attempts=result.attempts,
        elapsed_s=round(time.monotonic() - t0, 1),
    )
    return result


def run_execution_loop(config: WorkflowConfig, agents: dict[str, AgentSpec]) -> StageResult:
    """Execute each scheduled UoW with an implementation evaluator loop."""

    schedule = load_execution_schedule(assignments_path(config))
    software_engineer = resolve_agent(agents, "software_engineer")
    implementation_evaluator = resolve_agent(agents, "implementation_evaluator")

    total_uows = sum(len(batch.get("uows", [])) for batch in schedule)
    log("INFO", f"Execution loop: {len(schedule)} batch(es), {total_uows} UoW(s) total")

    completed_uows: list[str] = []
    produced_paths: list[Path] = [assignments_path(config)]

    for batch_index, batch in enumerate(schedule, start=1):
        uows = batch.get("uows", [])
        log(
            "INFO",
            f"Batch {batch_index}/{len(schedule)}: {len(uows)} UoW(s)  "
            f"parallel={batch.get('parallel_execution', False)}",
        )
        for uow in uows:
            uow_id = uow.get("uow_id")
            if not isinstance(uow_id, str) or not uow_id:
                raise WorkflowError(f"Invalid UoW entry in assignments.json: {uow}")

            log(
                "INFO",
                f"  Starting UoW '{uow_id}'  role={uow.get('assigned_role', '?')}",
            )
            _emit_structured_event(
                config,
                "uow_start",
                uow_id=uow_id,
                uow_index=len(completed_uows) + 1,
                total_uows=total_uows,
            )
            feedback_path: Path | None = None
            previous_signature: str | None = None
            feedback_summary: str | None = None
            human_resolution: dict[str, Any] | None = None

            for attempt in range(1, config.max_implementation_attempts + 1):
                log(
                    "INFO",
                    f"  [UoW {uow_id}] Attempt {attempt}/"
                    f"{config.max_implementation_attempts} — engineer",
                )
                producer_prompt = build_producer_prompt(
                    config=config,
                    stage_label="software_engineer",
                    attempt=attempt,
                    artifact_paths=[
                        tasks_path(config),
                        assignments_path(config),
                        story_path(config),
                    ],
                    feedback_path=feedback_path,
                    feedback_summary=feedback_summary,
                    uow_id=uow_id,
                    human_resolution=human_resolution,
                )
                producer_result = invoke_agent(
                    config,
                    software_engineer,
                    producer_prompt,
                    "software_engineer",
                    attempt,
                    uow_id=uow_id,
                    raise_on_error=False,
                )
                if producer_result.exit_code != 0:
                    feedback_path = None
                    feedback_summary = extract_command_failure_summary(producer_result)
                    log(
                        "WARN",
                        f"  [UoW {uow_id}] Engineer command failed on attempt {attempt} "
                        f"(exit={producer_result.exit_code})",
                    )
                    log(
                        "INFO",
                        f"  [UoW {uow_id}] Feedback for next attempt:\n"
                        f"{feedback_summary}",
                    )
                    continue

                evaluation_path = impl_eval_path(config, uow_id, attempt)
                log(
                    "INFO",
                    f"  [UoW {uow_id}] Attempt {attempt}/"
                    f"{config.max_implementation_attempts} — evaluator",
                )
                evaluator_prompt = build_evaluator_prompt(
                    config=config,
                    stage_label="implementation_evaluator",
                    attempt=attempt,
                    artifact_paths=[
                        uow_spec_path(config, uow_id),
                        impl_report_path(config, uow_id),
                    ],
                    uow_id=uow_id,
                )
                evaluator_result = invoke_agent(
                    config,
                    implementation_evaluator,
                    evaluator_prompt,
                    "implementation_evaluator",
                    attempt,
                    uow_id=uow_id,
                    raise_on_error=False,
                    early_exit_paths=[evaluation_path],
                )
                if evaluator_result.exit_code != 0:
                    if evaluation_path.is_file():
                        log(
                            "WARN",
                            f"  [UoW {uow_id}] Evaluator exited "
                            f"{evaluator_result.exit_code} but artifact already exists "
                            "— using it",
                        )
                    else:
                        feedback_path = None
                        feedback_summary = extract_command_failure_summary(
                            evaluator_result
                        )
                        log(
                            "WARN",
                            f"  [UoW {uow_id}] Evaluator command failed on attempt "
                            f"{attempt} (exit={evaluator_result.exit_code})",
                        )
                        log(
                            "INFO",
                            f"  [UoW {uow_id}] Feedback for next attempt:\n"
                            f"{feedback_summary}",
                        )
                        continue
                passed, payload = read_evaluation_result(evaluation_path)
                signature = evaluation_signature(payload)
                produced_paths.append(evaluation_path)

                score = payload.get("score", "?")
                if passed:
                    log(
                        "OK",
                        f"  [UoW {uow_id}] Implementation PASSED on attempt "
                        f"{attempt}  score={score}",
                    )
                    _emit_structured_event(
                        config,
                        "uow_complete",
                        uow_id=uow_id,
                        passed=True,
                        attempts=attempt,
                        score=score,
                    )
                    completed_uows.append(uow_id)
                    produced_paths.extend(
                        [
                            uow_spec_path(config, uow_id),
                            impl_report_path(config, uow_id),
                        ]
                    )
                    break

                log(
                    "WARN",
                    f"  [UoW {uow_id}] Implementation FAILED on attempt {attempt}  "
                    f"score={score}",
                )
                _emit_structured_event(
                    config,
                    "uow_complete",
                    uow_id=uow_id,
                    passed=False,
                    attempts=attempt,
                    score=score,
                )
                issues = payload.get("issues") or []
                for issue in issues:
                    log("WARN", f"    Issue: {issue.get('description', issue)}")

                escalation_rec = payload.get("escalation_recommendation", {})
                escalation_required = (
                    isinstance(escalation_rec, dict) and escalation_rec.get("required")
                )
                escalated_on_disk = (change_root(config) / "status" / "escalated.json").exists()

                if escalation_required or escalated_on_disk:
                    _emit_structured_event(
                        config,
                        "escalation_start",
                        stage="software_engineer",
                        uow_id=uow_id,
                        attempt=attempt,
                    )
                    if not escalated_on_disk:
                        write_escalation_artifact(
                            config,
                            "software_engineer",
                            "implementation_evaluator",
                            attempt,
                            payload,
                            uow_id=uow_id,
                        )
                    resolution = wait_for_resume(config)
                    if resolution:
                        human_resolution = resolution
                        feedback_path = evaluation_path
                        feedback_summary = extract_feedback_summary(payload)
                        log(
                            "INFO",
                            f"  [UoW {uow_id}] Retrying with human resolution",
                        )
                        continue

                if signature == previous_signature:
                    log("ERROR", f"  [UoW {uow_id}] Similarity plateau — aborting")
                    raise WorkflowError(
                        f"Similarity plateau detected for UoW '{uow_id}' after "
                        f"attempt {attempt}."
                    )
                previous_signature = signature
                feedback_path = evaluation_path
                feedback_summary = extract_feedback_summary(payload)
                log(
                    "INFO",
                    f"  [UoW {uow_id}] Feedback for next attempt:\n"
                    f"{feedback_summary}",
                )
            else:
                raise WorkflowError(
                    f"UoW '{uow_id}' exceeded max attempts "
                    f"({config.max_implementation_attempts})."
                )

    log(
        "OK",
        "Execution loop complete: "
        f"{len(completed_uows)} UoW(s) implemented: {', '.join(completed_uows)}",
    )
    return StageResult(
        stage_name="software_engineer",
        passed=True,
        attempts=len(completed_uows),
        artifact_paths=produced_paths,
        details={"completed_uows": completed_uows},
    )


def run_lessons_stage(config: WorkflowConfig, agents: dict[str, AgentSpec]) -> StageResult:
    """Run the terminal lessons optimization stage."""

    log("INFO", "Running lessons optimization stage")
    lessons_optimizer = resolve_agent(agents, "lessons_optimizer")
    prompt = build_lessons_prompt(config)
    invoke_agent(config, lessons_optimizer, prompt, "lessons_optimizer", 1)
    report_path = lessons_report_path(config)
    if not report_path.exists():
        raise WorkflowError(
            "Lessons optimizer did not produce the expected artifact: "
            f"{report_path}"
        )
    log("OK", f"Lessons optimization complete  artifact={report_path}")
    return StageResult(
        stage_name="lessons_optimizer",
        passed=True,
        attempts=1,
        artifact_paths=[report_path],
    )


def _inter_stage_escalation_check(config: WorkflowConfig, label: str) -> None:
    """Safety-net check for escalations written outside the evaluator flow."""

    resolution = wait_for_resume(config)
    if resolution:
        log("WARN", f"Inter-stage escalation detected and resolved ({label})")
        write_runner_log(
            config,
            "inter_stage_escalation_resolved",
            {"label": label, "resolution": resolution},
        )


def run_workflow(config: WorkflowConfig) -> list[StageResult]:
    """Execute the full custom-agent workflow and return stage results."""

    print_stage_banner(
        f"WORKFLOW START  change_id={config.change_id}  dry_run={config.dry_run}"
    )
    _emit_structured_event(
        config,
        "workflow_start",
        change_id=config.change_id,
        total_stages=TOTAL_WORKFLOW_STAGES,
        dry_run=config.dry_run,
    )
    log("INFO", f"repo_root          = {config.repo_root}")
    log("INFO", f"workflow_assets_root = {config.workflow_assets_root}")
    log("INFO", f"artifact_root      = {config.artifact_root}")
    log("INFO", f"cli_backend        = {config.cli_backend}")
    log("INFO", f"cli_bin            = {config.cli_bin}")
    log("INFO", f"model              = {config.model or '(default)'}")
    log("INFO", f"timeout_seconds    = {config.timeout_seconds}")
    log("INFO", f"reuse_existing_intake = {config.reuse_existing_intake}")
    log("INFO", f"max_task_plan_attempts      = {config.max_task_plan_attempts}")
    log("INFO", f"max_assignment_attempts     = {config.max_assignment_attempts}")
    log("INFO", f"max_implementation_attempts = {config.max_implementation_attempts}")
    log("INFO", f"max_qa_attempts             = {config.max_qa_attempts}")

    workflow_start = time.monotonic()
    ensure_artifact_dirs(config)
    agents = discover_agents(config.workflow_assets_root)
    results: list[StageResult] = []

    write_runner_log(
        config,
        "session_start",
        {
            "code_repo": str(config.repo_root),
            "workflow_assets_root": str(config.workflow_assets_root),
            "artifact_root": str(config.artifact_root),
            "cli_backend": config.cli_backend,
            "cli_bin": config.cli_bin,
            "reuse_existing_intake": config.reuse_existing_intake,
            "dry_run": config.dry_run,
        },
    )

    print_stage_banner("STAGE 1/6 — intake")
    _emit_structured_event(
        config,
        "stage_start",
        stage="intake",
        stage_number=INTAKE_STAGE_NUMBER,
        total_stages=TOTAL_WORKFLOW_STAGES,
    )
    t0 = time.monotonic()
    intake_paths = intake_artifact_paths(config.artifact_root, config.change_id)
    if config.reuse_existing_intake:
        missing_intake_artifacts = [path for path in intake_paths if not path.exists()]
        if missing_intake_artifacts:
            missing_display = ", ".join(str(path) for path in missing_intake_artifacts)
            raise WorkflowError(
                f"Requested intake reuse, but these artifacts are missing: {missing_display}"
            )
        write_runner_log(
            config,
            "intake_reused",
            {"artifacts": [str(path) for path in intake_paths]},
        )
        intake_result = StageResult(
            stage_name="intake",
            passed=True,
            attempts=0,
            artifact_paths=intake_paths,
            details={"reused": True},
        )
        results.append(intake_result)
        log(
            "OK",
            f"Stage 'intake' reused existing artifacts  "
            f"elapsed={time.monotonic() - t0:.1f}s",
        )
        _emit_structured_event(
            config,
            "stage_complete",
            stage="intake",
            stage_number=INTAKE_STAGE_NUMBER,
            passed=True,
            attempts=0,
            elapsed_s=round(time.monotonic() - t0, 1),
        )
    else:
        intake_agent = resolve_agent(agents, "intake")
        invoke_agent(config, intake_agent, build_intake_prompt(config), "intake", 1)
        missing_intake_artifacts = [path for path in intake_paths if not path.exists()]
        if missing_intake_artifacts:
            missing_display = ", ".join(str(path) for path in missing_intake_artifacts)
            raise WorkflowError(
                "Intake stage did not produce the expected artifacts: "
                f"{missing_display}"
            )
        intake_result = StageResult(
            stage_name="intake",
            passed=True,
            attempts=1,
            artifact_paths=intake_paths,
        )
        results.append(intake_result)
        log(
            "OK",
            f"Stage 'intake' complete  elapsed={time.monotonic() - t0:.1f}s",
        )
        _emit_structured_event(
            config,
            "stage_complete",
            stage="intake",
            stage_number=INTAKE_STAGE_NUMBER,
            passed=True,
            attempts=1,
            elapsed_s=round(time.monotonic() - t0, 1),
        )

    for spec in (TASK_PLAN_STAGE, ASSIGNMENT_STAGE):
        result = _run_loop_stage(config, agents, spec)
        results.append(result)
        _inter_stage_escalation_check(config, f"after {spec.stage_name}")

    print_stage_banner("STAGE 6/6 — software-engineer ↔ implementation-evaluator")
    _emit_structured_event(
        config,
        "stage_start",
        stage="software_engineer",
        stage_number=SOFTWARE_ENGINEER_STAGE_NUMBER,
        total_stages=TOTAL_WORKFLOW_STAGES,
    )
    t0 = time.monotonic()
    execution_result = run_execution_loop(config, agents)
    results.append(execution_result)
    log(
        "OK",
        f"Stage 'software_engineer' complete  uows={execution_result.attempts}  "
        f"elapsed={time.monotonic() - t0:.1f}s",
    )
    _emit_structured_event(
        config,
        "stage_complete",
        stage="software_engineer",
        stage_number=SOFTWARE_ENGINEER_STAGE_NUMBER,
        passed=execution_result.passed,
        attempts=execution_result.attempts,
        elapsed_s=round(time.monotonic() - t0, 1),
    )
    _inter_stage_escalation_check(config, "after software_engineer")

    qa_result = _run_loop_stage(config, agents, QA_STAGE)
    results.append(qa_result)
    _inter_stage_escalation_check(config, "after qa")

    print_stage_banner("LESSONS OPTIMIZER")
    _emit_structured_event(
        config,
        "stage_start",
        stage="lessons_optimizer",
        stage_number=LESSONS_STAGE_NUMBER,
        total_stages=TOTAL_WORKFLOW_STAGES,
    )
    t0 = time.monotonic()
    lessons_result = run_lessons_stage(config, agents)
    results.append(lessons_result)
    log(
        "OK",
        f"Stage 'lessons_optimizer' complete  elapsed={time.monotonic() - t0:.1f}s",
    )
    _emit_structured_event(
        config,
        "stage_complete",
        stage="lessons_optimizer",
        stage_number=LESSONS_STAGE_NUMBER,
        passed=lessons_result.passed,
        attempts=lessons_result.attempts,
        elapsed_s=round(time.monotonic() - t0, 1),
    )

    total_elapsed = time.monotonic() - workflow_start
    print_stage_banner(
        f"WORKFLOW COMPLETE  total_elapsed={total_elapsed:.1f}s  stages={len(results)}"
    )
    _emit_structured_event(
        config,
        "workflow_complete",
        total_elapsed_s=round(total_elapsed, 1),
        stage_count=len(results),
        all_passed=all(result.passed for result in results),
    )

    write_runner_log(
        config,
        "session_end",
        {
            "stages": [
                {
                    "stage_name": result.stage_name,
                    "passed": result.passed,
                    "attempts": result.attempts,
                    "artifacts": [str(path) for path in result.artifact_paths],
                }
                for result in results
            ]
        },
    )
    return results


def format_summary(results: list[StageResult]) -> dict[str, Any]:
    """Convert stage results into a serializable summary."""

    return {
        "status": "pass" if all(result.passed for result in results) else "fail",
        "stages": [
            {
                "stage_name": result.stage_name,
                "passed": result.passed,
                "attempts": result.attempts,
                "artifacts": [str(path) for path in result.artifact_paths],
                "details": result.details,
            }
            for result in results
        ],
    }
