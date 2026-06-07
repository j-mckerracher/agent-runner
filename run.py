import argparse
import json
import logging
import os
import shutil
import signal
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import yaml

from dotenv import load_dotenv

from core.cli_logging import (
    DEFAULT_LOG_FORMAT,
    LocalTimezoneFormatter,
    install_httpx_healthcheck_filter,
    normalize_log_level,
    to_logging_level,
)
from core.workspace_cleanup import clean_change_workspace
from core.ssl_compat import configure_system_ssl
from core.runner_models import (
    KNOWN_RUNNERS,
    RUNNER_MODEL_CHOICES,
    resolve_runner_llm_config,
)
from core.runner_failover import RunnerFailoverPolicy, discover_prior_runner_candidates
from core.repo_prep import prepare_repo_branch
from core.story_inputs import count_acceptance_criteria
from core.workflow_inputs import DEFAULT_TEST_STORY_FILE, resolve_workflow_input

configure_system_ssl()

logger = logging.getLogger(__name__)

INPUT_VALIDATION_ERRORS = (FileNotFoundError, ValueError)

# ====================== HELPERS ====================== #

RUNNER_ROOT = Path(__file__).resolve().parent

from core.runtime_paths import agent_context_root, load_data_dir_override_from_env_file, logs_root  # noqa: E402

load_data_dir_override_from_env_file(RUNNER_ROOT / ".env")

AGENT_CONTEXT_ROOT = agent_context_root()
LOGS_ROOT = logs_root()

# Workflow stage names: used for stage event emission, status reporting, and
# the executable workflow plan shown to the UI.
STAGE_MATERIALIZE = "materialize"
STAGE_INTAKE = "intake"
STAGE_TASK_GENERATION = "task-generation"
STAGE_TASK_ASSIGNMENT = "task-assignment"
STAGE_EXECUTION = "execution"
STAGE_QA = "qa"
STAGE_PR_REVIEW = "pr-review"
WORKFLOW_STAGES = (
    STAGE_MATERIALIZE,
    STAGE_INTAKE,
    STAGE_TASK_GENERATION,
    STAGE_TASK_ASSIGNMENT,
    STAGE_EXECUTION,
    STAGE_QA,
    STAGE_PR_REVIEW,
)

# Agent artifact directories and filenames: used to build canonical workflow
# artifact paths passed between stages.
ARTIFACT_DIR_INTAKE = "intake"
ARTIFACT_DIR_PLANNING = "planning"
ARTIFACT_DIR_SUMMARY = "summary"
ARTIFACT_DIR_EXECUTION = "execution"
ARTIFACT_DIR_QA = "qa"
ARTIFACT_DIR_QA_EVIDENCE = "evidence"
ARTIFACT_DIR_QA_TEST_OUTPUT = "test_output"
ARTIFACT_DIR_QA_LOGS = "logs"
ARTIFACT_DIR_QA_SCREENSHOTS = "screenshots"
ARTIFACT_FILE_STORY = "story.yaml"
ARTIFACT_FILE_INTAKE_CONFIG = "config.yaml"
ARTIFACT_FILE_CONSTRAINTS = "constraints.md"
ARTIFACT_FILE_TASKS = "tasks.yaml"
ARTIFACT_FILE_ASSIGNMENTS = "assignments.json"
ARTIFACT_FILE_IMPL_REPORT = "impl_report.yaml"
ARTIFACT_FILE_QA_REPORT = "qa_report.yaml"
ARTIFACT_FILE_EVENTS = "events.jsonl"
ARTIFACT_FILE_RUN_METRICS = "run_metrics.yaml"
WORKFLOW_STATUS_FILENAME = "workflow_status.yaml"

# Event names, kinds, and fields: used by local observability summarization and
# server-side event streaming.
EVENT_TYPE_STAGE_START = "stage.start"
EVENT_TYPE_STAGE_END = "stage.end"
EVENT_TYPE_UOW_START = "uow.start"
EVENT_TYPE_UOW_END = "uow.end"
EVENT_TYPE_OPIK_START = "opik.start"
EVENT_TYPE_CLI_EXIT = "cli.exit"
EVENT_TYPE_METRICS = "metrics"
EVENT_TYPE_LLM_CALL = "llm.call"
EVENT_TYPE_JOB_START = "job.start"
EVENT_TYPE_JOB_END = "job.end"
EVENT_TYPE_LOG = "log"
EVENT_TYPE_STORY_SOURCE = "story.source"
EVENT_TYPE_STORY_NORMALIZED = "story.normalized"
EVENT_TYPE_WORKFLOW_PLAN = "workflow.plan"

EVENT_KIND_STAGE_FAILED = "stage_failed"
EVENT_KIND_WORKFLOW_FAILED = "workflow_failed"

EVENT_FIELD_TYPE = "type"
EVENT_FIELD_TS = "ts"
EVENT_FIELD_STAGE = "stage"
EVENT_FIELD_STATUS = "status"
EVENT_FIELD_UOW_ID = "uow_id"
EVENT_FIELD_METADATA = "metadata"
EVENT_FIELD_NAME = "name"
EVENT_FIELD_AGENT = "agent"
EVENT_FIELD_MODEL = "model"
EVENT_FIELD_DURATION_MS = "duration_ms"
EVENT_FIELD_TOKENS_IN = "tokens_in"
EVENT_FIELD_TOKENS_OUT = "tokens_out"
EVENT_FIELD_COST_USD = "cost_usd"
EVENT_FIELD_PROMPT_EST_TOKENS = "prompt_est_tokens"
EVENT_FIELD_RESPONSE_EST_TOKENS = "response_est_tokens"
EVENT_FIELD_ATTEMPT = "attempt"
EVENT_FIELD_RETRYABLE = "retryable"
EVENT_FIELD_ERROR_CATEGORY = "error_category"
EVENT_FIELD_PROMPT_SHA256 = "prompt_sha256"
EVENT_FIELD_BATCH_ID = "batch_id"
EVENT_FIELD_ORDINAL = "ordinal"
EVENT_FIELD_TOTAL_UOWS = "total_uows"
EVENT_FIELD_ERROR = "error"
EVENT_FIELD_SOURCE = "source"
EVENT_FIELD_STORY_FILE = "story_file"
EVENT_FIELD_MANUAL_STORY_FILE = "manual_story_file"
EVENT_FIELD_ORIGINAL_AC_COUNT = "original_ac_count"
EVENT_FIELD_NORMALIZED_AC_COUNT = "normalized_ac_count"
EVENT_FIELD_TOTAL_STAGES = "total_stages"
EVENT_FIELD_STAGES = "stages"
EVENT_FIELD_EXIT_CODE = "exit_code"
EVENT_FIELD_LEVEL = "level"
EVENT_FIELD_KIND = "kind"
EVENT_FIELD_MSG = "msg"
EVENT_FIELD_CHANGE_ID = "change_id"
EVENT_FIELD_REPO = "repo"
EVENT_FIELD_RUNNER = "runner"
EVENT_FIELD_INTAKE_MODE = "intake_mode"
EVENT_FIELD_FEATURE_BRANCH = "feature_branch"
EVENT_FIELD_RESPONSE_CHARS = "response_chars"
EVENT_FIELD_PROMPT_CHARS = "prompt_chars"
EVENT_FIELD_MAX_ATTEMPTS = "max_attempts"
EVENT_FIELD_RESPONSE_PARSE_OK = "response_parse_ok"
EVENT_FIELD_TOOL_CALL_COUNT = "tool_call_count"
EVENT_FIELD_TOOL_STEP_COUNT = "tool_step_count"
EVENT_FIELD_CACHE_STATIC_PREFIX_EST_TOKENS = "cache_static_prefix_est_tokens"
EVENT_FIELD_CONNECTION_REUSE_OBSERVABLE = "connection_reuse_observable"
EVENT_FIELD_MAX_TOKENS = "max_tokens"
EVENT_FIELD_TEMPERATURE = "temperature"
EVENT_FIELD_RESPONSE_SHA256 = "response_sha256"

LLM_CALL_SUMMARY_FIELDS = (
    EVENT_FIELD_RUNNER,
    EVENT_FIELD_AGENT,
    EVENT_FIELD_MODEL,
    EVENT_FIELD_STATUS,
    EVENT_FIELD_DURATION_MS,
    EVENT_FIELD_ATTEMPT,
    EVENT_FIELD_MAX_ATTEMPTS,
    EVENT_FIELD_PROMPT_CHARS,
    EVENT_FIELD_RESPONSE_CHARS,
    EVENT_FIELD_PROMPT_EST_TOKENS,
    EVENT_FIELD_RESPONSE_EST_TOKENS,
    EVENT_FIELD_TOKENS_IN,
    EVENT_FIELD_TOKENS_OUT,
    EVENT_FIELD_COST_USD,
    EVENT_FIELD_ERROR_CATEGORY,
    EVENT_FIELD_RETRYABLE,
    EVENT_FIELD_RESPONSE_PARSE_OK,
    EVENT_FIELD_TOOL_CALL_COUNT,
    EVENT_FIELD_TOOL_STEP_COUNT,
    EVENT_FIELD_CACHE_STATIC_PREFIX_EST_TOKENS,
    EVENT_FIELD_CONNECTION_REUSE_OBSERVABLE,
    EVENT_FIELD_MAX_TOKENS,
    EVENT_FIELD_TEMPERATURE,
    EVENT_FIELD_PROMPT_SHA256,
    EVENT_FIELD_RESPONSE_SHA256,
)

# Story and assignment schema keys: used when interpreting canonical artifacts.
STORY_KEY_ACCEPTANCE_CRITERIA = "acceptance_criteria"
ASSIGNMENTS_KEY_BATCHES = "batches"
ASSIGNMENTS_KEY_BATCH_ID = "batch_id"
ASSIGNMENTS_KEY_UOWS = "uows"
ASSIGNMENTS_KEY_UOW_ID = "uow_id"
ASSIGNMENTS_KEY_PARALLEL_EXECUTION = "parallel_execution"

# Status strings shared between event emission and workflow status documents.
STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_CANCELLED = "cancelled"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"


def _artifact_path(change_id: str, *relative_parts: str) -> Path:
    return AGENT_CONTEXT_ROOT.joinpath(change_id, *relative_parts)


def _artifact_ref(*relative_parts: str) -> str:
    return Path(*relative_parts).as_posix()


def _summary_artifact_ref(filename: str) -> str:
    return _artifact_ref(ARTIFACT_DIR_SUMMARY, filename)


def load_assignments(change_id: str) -> dict:
    """Read assignments.json produced by the task-assigner stage."""
    from core.artifact_utils import load_assignments_file
    path = _artifact_path(change_id, ARTIFACT_DIR_PLANNING, ARTIFACT_FILE_ASSIGNMENTS)
    return load_assignments_file(path)


def _require_dir(change_id: str, stage: str, *relative_parts: str) -> Path:
    """Raise FileNotFoundError with a clear message if a stage output is missing."""
    path = _artifact_path(change_id, *relative_parts)
    if not path.exists():
        raise FileNotFoundError(
            f"Stage '{stage}' did not produce expected output: {path}\n"
            f"The {stage} agent may have exited successfully but wrote no artifacts. "
            f"Check the agent runner output above for errors from the {stage} CLI call."
        )
    return path


def _require_file(change_id: str, stage: str, *relative_parts: str) -> Path:
    """Like _require_dir but also requires the path to be a file."""
    path = _require_dir(change_id, stage, *relative_parts)
    if not path.is_file():
        raise FileNotFoundError(
            f"Stage '{stage}' output exists but is not a file: {path}"
        )
    return path


# All agent names used in the workflow (for per-agent model resolution)
AGENT_NAMES = [
    "intake",
    "task-generator",
    "task-plan-evaluator",
    "task-assigner",
    "assignment-evaluator",
    "software-engineer-hyperagent",
    "implementation-evaluator",
    "qa-engineer",
    "qa-evaluator",
    "pr-reviewer",
]
AGENT_NAME_SET = frozenset(AGENT_NAMES)


def _parse_agent_override_arg(value: str) -> tuple[str, str]:
    agent, sep, override = value.partition("=")
    agent = agent.strip()
    override = override.strip()
    if not sep or not agent or not override:
        raise argparse.ArgumentTypeError("agent overrides must use AGENT=VALUE")
    return agent, override


def _agent_override_pairs(values: list[tuple[str, str]] | None, field: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for agent, override in values or []:
        result.setdefault(agent, {})[field] = override
    return result


def _merge_agent_override_maps(*maps: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    merged: dict[str, dict[str, str]] = {}
    for item in maps:
        for agent, override in item.items():
            merged.setdefault(agent, {}).update(override)
    return merged


def resolve_agent_llm_overrides(
    *,
    runner: str,
    model: str | None,
    config: dict | None,
    agent_llm_overrides: dict[str, dict[str, str | None]] | None = None,
) -> dict[str, dict[str, str | None]]:
    """Resolve the per-agent runner/model map for this workflow run."""
    overrides = agent_llm_overrides or {}
    for agent, override in overrides.items():
        if agent not in AGENT_NAME_SET:
            raise ValueError(
                f"Unknown agent override '{agent}'. Valid agents: {', '.join(AGENT_NAMES)}"
            )
        if not isinstance(override, dict):
            raise ValueError(f"agent_llm_overrides[{agent}] must be an object")
        unknown_keys = set(override) - {"runner", "model"}
        if unknown_keys:
            raise ValueError(
                f"agent_llm_overrides[{agent}] has unsupported keys: {', '.join(sorted(unknown_keys))}"
            )

    resolved: dict[str, dict[str, str | None]] = {}
    for agent in AGENT_NAMES:
        override = overrides.get(agent) or {}
        agent_runner = override.get("runner") or runner
        if "model" in override:
            agent_model = override.get("model")
        elif "runner" in override:
            agent_model = None
        else:
            agent_model = model
        if not isinstance(agent_runner, str) or not agent_runner.strip():
            raise ValueError(f"agent_llm_overrides[{agent}].runner must be a non-empty string")
        if agent_model is not None and not isinstance(agent_model, str):
            raise ValueError(f"agent_llm_overrides[{agent}].model must be a string")
        llm_config = resolve_runner_llm_config(agent_runner, agent_model, config)
        resolved[agent] = {"runner": agent_runner, "model": llm_config["model"]}
    return resolved


def _agent_llm_kwargs(agent_llms: dict[str, dict[str, str | None]], agent: str) -> dict[str, str | None]:
    llm = agent_llms[agent]
    return {"runner": llm["runner"], "runner_model": llm["model"]}


def _emit(type: str, **fields) -> None:
    """No-op unless AGENT_RUNNER_EVENT_LOG is set (server-driven runs)."""
    if not os.environ.get("AGENT_RUNNER_EVENT_LOG"):
        return
    try:
        from server.events import emit
        emit(type, **fields)
    except Exception:
        pass


def _acceptance_criteria_count(value: object) -> int | None:
    if isinstance(value, dict):
        return sum(1 for key, item in value.items() if isinstance(key, str) and key.strip() and isinstance(item, str) and item.strip())
    if isinstance(value, list):
        return sum(1 for item in value if isinstance(item, str) and item.strip())
    return None


def _story_payload_from_path(path: str | Path) -> dict | None:
    story_path = Path(path).expanduser()
    if not story_path.is_file():
        return None
    try:
        with story_path.open("r", encoding="utf-8") as handle:
            if story_path.suffix.lower() == ".json":
                payload = json.load(handle)
            else:
                payload = yaml.safe_load(handle)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _story_source_metadata(*, intake_mode: str, intake_source: str) -> dict:
    payload = _story_payload_from_path(intake_source) if intake_mode in {"synthetic", "manual"} else None
    if intake_mode == "synthetic":
        source = "story_file"
    elif intake_mode == "manual":
        source = "manual"
    elif intake_mode == "ado":
        source = "ado"
    else:
        source = "unknown"
    return {
        "source": source,
        "story_file": intake_source if intake_mode == "synthetic" else None,
        "manual_story_file": intake_source if intake_mode == "manual" else None,
        "original_ac_count": count_acceptance_criteria(payload.get(STORY_KEY_ACCEPTANCE_CRITERIA)) if payload else None,
    }


def _record_current_job_metadata(**fields) -> None:
    job_id = os.environ.get("AGENT_RUNNER_JOB_ID")
    if not job_id:
        return
    clean = {key: value for key, value in fields.items() if value is not None}
    if not clean:
        return
    try:
        from server import db
        db.update_job(job_id, **clean)
    except Exception:
        pass


def _build_runner_failover_policy(
    *,
    config: dict,
    current_runner: str,
    history_limit: int = 200,
) -> RunnerFailoverPolicy:
    try:
        from server import db
        prior_jobs = db.list_jobs(limit=history_limit)
    except Exception as exc:
        logger.warning("runner failover disabled: could not read local job history: %s", exc)
        return RunnerFailoverPolicy([])

    candidates = discover_prior_runner_candidates(
        prior_jobs,
        config=config,
        current_runner=current_runner,
    )
    logger.info(
        "runner failover initialized: current_runner=%s candidate_count=%d candidates=%s",
        current_runner,
        len(candidates),
        [candidate.runner for candidate in candidates],
    )
    return RunnerFailoverPolicy(candidates)


def _workflow_stage_names(*, skip_lessons_optimizer: bool = True) -> list[str]:
    # The lessons optimizer is disabled unconditionally. Keep the historical
    # parameter so older callers do not break, but never add that stage back into
    # the executable plan.
    return list(WORKFLOW_STAGES)


class _Stage:
    """Context manager that emits stage.start/stage.end events around a block."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._previous_stage: str | None = None

    def __enter__(self):
        self._previous_stage = os.environ.get("AGENT_RUNNER_CURRENT_STAGE")
        os.environ["AGENT_RUNNER_CURRENT_STAGE"] = self.name
        logger.info("Stage START: %s", self.name)
        _emit(EVENT_TYPE_STAGE_START, stage=self.name)
        return self

    def __exit__(self, exc_type, exc, tb):
        status = STATUS_OK if exc_type is None else STATUS_ERROR
        if exc_type is not None:
            logger.error("Stage ERROR: %s — %s: %s", self.name, exc_type.__name__, str(exc) or "")
            _emit(
                EVENT_TYPE_LOG,
                level=STATUS_ERROR,
                kind=EVENT_KIND_STAGE_FAILED,
                stage=self.name,
                msg=f"{exc_type.__name__}: {str(exc)}"[:500],
            )
        else:
            logger.info("Stage END: %s (ok)", self.name)
        _emit(EVENT_TYPE_STAGE_END, stage=self.name, status=status)
        if self._previous_stage is None:
            os.environ.pop("AGENT_RUNNER_CURRENT_STAGE", None)
        else:
            os.environ["AGENT_RUNNER_CURRENT_STAGE"] = self._previous_stage
        return False


def use_runner_root() -> None:
    logger.debug("use_runner_root: chdir -> %s", RUNNER_ROOT)
    os.chdir(RUNNER_ROOT)


def _summarize_exception(exc: BaseException | None) -> str:
    if exc is None:
        return ""
    detail = str(exc).strip()
    return detail or type(exc).__name__


def _serialize_exception(exc: BaseException) -> dict:
    return {
        "exception_type": type(exc).__name__,
        "message": _summarize_exception(exc),
    }


def _truncate_text(text: str | None, limit: int = 2000) -> str | None:
    if not text:
        return None
    if len(text) <= limit:
        return text
    return f"...{text[-limit:]}"


def _workflow_status_path(change_id: str) -> Path:
    return _artifact_path(change_id, ARTIFACT_DIR_SUMMARY, WORKFLOW_STATUS_FILENAME)


def _parse_event_timestamp(value: str | None) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _event_log_path(change_id: str) -> Path:
    configured = os.environ.get("AGENT_RUNNER_EVENT_LOG")
    if configured:
        return Path(configured)
    return LOGS_ROOT / change_id / ARTIFACT_FILE_EVENTS


def _read_event_rows(change_id: str) -> list[dict]:
    path = _event_log_path(change_id)
    if not path.is_file():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning("_read_event_rows: skipped malformed event line in %s: %s", path, exc)
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _copy_event_log_to_summary(change_id: str) -> str | None:
    source = _event_log_path(change_id)
    if not source.is_file():
        return None
    destination = _artifact_path(change_id, ARTIFACT_DIR_SUMMARY, ARTIFACT_FILE_EVENTS)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return _summary_artifact_ref(ARTIFACT_FILE_EVENTS)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    sorted_values = sorted(values)
    index = max(0, min(len(sorted_values) - 1, round((percentile / 100) * (len(sorted_values) - 1))))
    return round(sorted_values[index], 3)


def _latency_summary(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "mean_ms": None, "p50_ms": None, "p95_ms": None, "p99_ms": None, "max_ms": None}
    return {
        "count": len(values),
        "mean_ms": round(sum(values) / len(values), 3),
        "p50_ms": _percentile(values, 50),
        "p95_ms": _percentile(values, 95),
        "p99_ms": _percentile(values, 99),
        "max_ms": round(max(values), 3),
    }


def _answerability_matrix() -> dict[str, dict[str, object]]:
    direct = "direct"
    proxy = "proxy"
    limit = "instrumented_limit"
    return {
        "latency_wall_clock_distribution": {
            "status": direct,
            "data": ["llm_calls.latency_ms", "llm_calls.latency_summary", "stage_durations_seconds", "uow_durations_seconds"],
        },
        "latency_internal_breakdown_network_tokenization_prompt_postprocessing": {
            "status": limit,
            "data": ["llm_calls.duration_ms", "llm_calls.connection_reuse_observable"],
            "limit": "External CLI runners expose wall-clock only; OpenAI-compatible API calls expose per-request wall-clock but not provider-side tokenization/network subspans.",
        },
        "sequential_vs_parallel_calls": {
            "status": direct,
            "data": ["events.jsonl stage/uow/opik timestamps", "uow_iterations", "cli_calls_by_agent"],
        },
        "prompt_rebuild_and_cache_static_prefix": {
            "status": direct,
            "data": ["llm_calls.prompt_sha256", "llm_calls.cache_static_prefix_chars", "llm_calls.cache_static_prefix_est_tokens"],
        },
        "connection_keep_alive": {
            "status": limit,
            "data": ["llm_calls.connection_reuse_observable"],
            "limit": "Only local OpenAI-compatible HTTP calls are observable; hosted CLI connection reuse is hidden behind the provider CLI.",
        },
        "token_budget_and_context_utilization": {
            "status": direct,
            "data": ["llm_calls.tokens_in", "llm_calls.tokens_out", "llm_calls.prompt_est_tokens", "llm_calls.response_est_tokens"],
        },
        "repeated_prompt_parts_cached_pricing_eligibility": {
            "status": proxy,
            "data": ["llm_calls.prompt_sha256", "llm_calls.cache_static_prefix_est_tokens", "prompt_repetition"],
            "limit": "Provider cached-token billing is not exposed by every CLI; repeated prefixes and estimated cached-token eligibility are captured.",
        },
        "model_fit_by_subtask": {
            "status": direct,
            "data": ["llm_calls.agent", "llm_calls.model", "llm_calls.tokens_in", "llm_calls.cost_usd", "llm_calls.status"],
        },
        "max_tokens_and_temperature": {
            "status": proxy,
            "data": ["llm_calls.max_tokens", "llm_calls.temperature"],
            "limit": "Captured when configured for OpenAI-compatible transports; external CLIs may not expose defaults.",
        },
        "conversation_history_growth_and_pruning": {
            "status": proxy,
            "data": ["llm_calls.prompt_chars", "llm_calls.prompt_est_tokens", "llm_calls.prompt_sha256"],
            "limit": "The harness captures final prompt payload size/hash; semantic pruning quality requires output review or eval scoring.",
        },
        "rag_chunking_ranking_truncation": {
            "status": limit,
            "data": ["llm_calls.prompt_text", "llm_calls.tool_call_count"],
            "limit": "No dedicated RAG retriever telemetry exists unless a tool emits retrieval-specific artifacts.",
        },
        "prompt_response_logging": {
            "status": direct,
            "data": ["logs/<change-id>/<agent>/*_session.json prompt_text/response_text", "llm_calls prompt/response hashes"],
        },
        "quality_regression_tests": {
            "status": direct,
            "data": ["qa/qa_report.yaml", "qa/evidence", "eval artifacts when run_kind=evaluation"],
        },
        "output_quality_measurement": {
            "status": direct,
            "data": ["qa/qa_report.yaml", "eval_* artifacts", "evaluator pass/fail events"],
        },
        "retry_rate_error_categories_and_recovery": {
            "status": direct,
            "data": ["llm_calls.attempt", "llm_calls.max_attempts", "llm_calls.error_category", "llm_calls.retryable", "llm_calls.status"],
        },
        "parse_failure_rate": {
            "status": direct,
            "data": ["llm_calls.response_parse_ok", "llm_calls.error_category=malformed_response"],
        },
        "circuit_breakers_loop_depth": {
            "status": direct,
            "data": ["llm_calls.max_attempts", "llm_calls.tool_step_count", "uow_iterations"],
        },
        "cost_anomalies": {
            "status": direct,
            "data": ["llm_calls.cost_usd", "cost_by_agent", "cost_anomalies"],
        },
        "prompt_diff_output_distribution_changes": {
            "status": proxy,
            "data": ["llm_calls.prompt_sha256", "llm_calls.response_sha256", "eval run outputs"],
            "limit": "Hashes identify changed prompts/responses; distribution comparison requires running the fixed eval set across revisions.",
        },
        "llm_vs_non_llm_steps": {
            "status": direct,
            "data": ["stage_durations_seconds", "cli_calls_by_agent", "llm_calls_by_agent"],
        },
        "shared_mutable_state_scalability": {
            "status": limit,
            "data": ["artifact paths", "logs paths", "workflow_status"],
            "limit": "Runtime telemetry shows file locations and run IDs, but concurrency safety requires code/design review plus stress tests.",
        },
    }


def _summarize_event_rows(rows: list[dict]) -> dict:
    stage_starts: dict[str, datetime] = {}
    stage_durations: dict[str, float] = {}
    uow_starts: dict[str, datetime] = {}
    uow_durations: dict[str, float] = {}
    uow_iterations: dict[str, int] = {}
    cli_calls_by_agent: dict[str, dict[str, float | int]] = {}
    llm_calls: list[dict[str, object]] = []
    llm_latencies_by_agent: dict[str, list[float]] = {}
    llm_calls_by_agent: dict[str, dict[str, object]] = {}
    llm_calls_by_model: dict[str, dict[str, object]] = {}
    error_categories: dict[str, int] = {}
    prompt_hash_counts: dict[str, int] = {}
    legacy_metric_totals = {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}
    totals = {
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_usd": 0.0,
        "cli_calls": 0,
        "llm_calls": 0,
        "metrics_events": 0,
    }

    for row in rows:
        event_type = row.get(EVENT_FIELD_TYPE)
        event_ts = _parse_event_timestamp(row.get(EVENT_FIELD_TS))
        if event_type == EVENT_TYPE_STAGE_START and event_ts and row.get(EVENT_FIELD_STAGE):
            stage_starts[str(row[EVENT_FIELD_STAGE])] = event_ts
        elif event_type == EVENT_TYPE_STAGE_END and event_ts and row.get(EVENT_FIELD_STAGE) in stage_starts:
            stage = str(row[EVENT_FIELD_STAGE])
            stage_durations[stage] = round((event_ts - stage_starts[stage]).total_seconds(), 3)
        elif event_type == EVENT_TYPE_UOW_START and event_ts and row.get(EVENT_FIELD_UOW_ID):
            uow_starts[str(row[EVENT_FIELD_UOW_ID])] = event_ts
        elif event_type == EVENT_TYPE_UOW_END and event_ts and row.get(EVENT_FIELD_UOW_ID) in uow_starts:
            uow_id = str(row[EVENT_FIELD_UOW_ID])
            uow_durations[uow_id] = round((event_ts - uow_starts[uow_id]).total_seconds(), 3)
        elif event_type == EVENT_TYPE_OPIK_START and str(row.get(EVENT_FIELD_NAME, "")).startswith("uow-iteration-"):
            metadata = row.get(EVENT_FIELD_METADATA) if isinstance(row.get(EVENT_FIELD_METADATA), dict) else {}
            uow_id = metadata.get(EVENT_FIELD_UOW_ID)
            if uow_id:
                uow_iterations[str(uow_id)] = uow_iterations.get(str(uow_id), 0) + 1
        elif event_type == EVENT_TYPE_CLI_EXIT:
            totals["cli_calls"] += 1
            agent = str(row.get(EVENT_FIELD_AGENT) or "unknown")
            summary = cli_calls_by_agent.setdefault(agent, {"count": 0, "duration_ms": 0})
            summary["count"] = int(summary["count"]) + 1
            summary["duration_ms"] = int(summary["duration_ms"]) + int(row.get(EVENT_FIELD_DURATION_MS) or 0)
        elif event_type == EVENT_TYPE_METRICS:
            totals["metrics_events"] += 1
            legacy_metric_totals["tokens_in"] += int(row.get(EVENT_FIELD_TOKENS_IN) or 0)
            legacy_metric_totals["tokens_out"] += int(row.get(EVENT_FIELD_TOKENS_OUT) or 0)
            legacy_metric_totals["cost_usd"] = round(
                float(legacy_metric_totals["cost_usd"]) + float(row.get(EVENT_FIELD_COST_USD) or 0.0),
                6,
            )
        elif event_type == EVENT_TYPE_LLM_CALL:
            totals["llm_calls"] += 1
            agent = str(row.get(EVENT_FIELD_AGENT) or "unknown")
            model = str(row.get(EVENT_FIELD_MODEL) or "unknown")
            duration_ms = row.get(EVENT_FIELD_DURATION_MS)
            if isinstance(duration_ms, (int, float)):
                llm_latencies_by_agent.setdefault(agent, []).append(float(duration_ms))
            for key, bucket_name in ((agent, "agent"), (model, "model")):
                bucket = llm_calls_by_agent if bucket_name == "agent" else llm_calls_by_model
                summary = bucket.setdefault(
                    key,
                    {"count": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "errors": 0, "retries": 0},
                )
                summary["count"] = int(summary["count"]) + 1
                summary["tokens_in"] = int(summary["tokens_in"]) + int(row.get(EVENT_FIELD_TOKENS_IN) or row.get(EVENT_FIELD_PROMPT_EST_TOKENS) or 0)
                summary["tokens_out"] = int(summary["tokens_out"]) + int(row.get(EVENT_FIELD_TOKENS_OUT) or row.get(EVENT_FIELD_RESPONSE_EST_TOKENS) or 0)
                summary["cost_usd"] = round(float(summary["cost_usd"]) + float(row.get(EVENT_FIELD_COST_USD) or 0.0), 6)
                if row.get(EVENT_FIELD_STATUS) not in (None, STATUS_OK, "tool_call"):
                    summary["errors"] = int(summary["errors"]) + 1
                if int(row.get(EVENT_FIELD_ATTEMPT) or 1) > 1 or row.get(EVENT_FIELD_RETRYABLE):
                    summary["retries"] = int(summary["retries"]) + 1
            category = row.get(EVENT_FIELD_ERROR_CATEGORY)
            if category:
                error_categories[str(category)] = error_categories.get(str(category), 0) + 1
            prompt_hash = row.get(EVENT_FIELD_PROMPT_SHA256)
            if isinstance(prompt_hash, str) and prompt_hash:
                prompt_hash_counts[prompt_hash] = prompt_hash_counts.get(prompt_hash, 0) + 1
            llm_calls.append(
                {
                    key: row.get(key)
                    for key in LLM_CALL_SUMMARY_FIELDS
                    if key in row
                }
            )

    latency_by_agent = {agent: _latency_summary(values) for agent, values in llm_latencies_by_agent.items()}
    cost_values = [float(call.get(EVENT_FIELD_COST_USD) or 0.0) for call in llm_calls]
    cost_threshold = (sum(cost_values) / len(cost_values) * 3) if cost_values else 0.0
    repeated_prompts = {key: count for key, count in prompt_hash_counts.items() if count > 1}
    if llm_calls:
        totals["tokens_in"] = sum(int(call.get(EVENT_FIELD_TOKENS_IN) or call.get(EVENT_FIELD_PROMPT_EST_TOKENS) or 0) for call in llm_calls)
        totals["tokens_out"] = sum(int(call.get(EVENT_FIELD_TOKENS_OUT) or call.get(EVENT_FIELD_RESPONSE_EST_TOKENS) or 0) for call in llm_calls)
        totals["cost_usd"] = round(sum(float(call.get(EVENT_FIELD_COST_USD) or 0.0) for call in llm_calls), 6)
        totals["source"] = EVENT_TYPE_LLM_CALL
    else:
        totals["tokens_in"] = legacy_metric_totals["tokens_in"]
        totals["tokens_out"] = legacy_metric_totals["tokens_out"]
        totals["cost_usd"] = legacy_metric_totals["cost_usd"]
        totals["source"] = "metrics"
    return {
        "stage_durations_seconds": stage_durations,
        "uow_durations_seconds": uow_durations,
        "uow_iterations": uow_iterations,
        "cli_calls_by_agent": cli_calls_by_agent,
        "llm_calls": llm_calls,
        "llm_latency": {
            "overall": _latency_summary([float(call[EVENT_FIELD_DURATION_MS]) for call in llm_calls if isinstance(call.get(EVENT_FIELD_DURATION_MS), (int, float))]),
            "by_agent": latency_by_agent,
        },
        "llm_calls_by_agent": llm_calls_by_agent,
        "llm_calls_by_model": llm_calls_by_model,
        "error_categories": error_categories,
        "legacy_metric_totals": legacy_metric_totals,
        "prompt_repetition": {
            "repeated_prompt_hashes": repeated_prompts,
            "repeated_prompt_count": sum(count - 1 for count in repeated_prompts.values()),
        },
        "cost_anomalies": [
            call
            for call in llm_calls
            if cost_threshold > 0 and float(call.get(EVENT_FIELD_COST_USD) or 0.0) > cost_threshold
        ],
        "totals": totals,
        "answerability_matrix": _answerability_matrix(),
    }


def _write_run_metrics(change_id: str) -> dict | None:
    run_dir = _artifact_path(change_id)
    if not run_dir.is_dir():
        return None
    event_log_artifact = _copy_event_log_to_summary(change_id)
    rows = _read_event_rows(change_id)
    payload = {
        "change_id": change_id,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source_event_log": str(_event_log_path(change_id)),
        "event_log_artifact": event_log_artifact,
        "events_observed": len(rows),
        "metrics": _summarize_event_rows(rows),
    }
    path = _artifact_path(change_id, ARTIFACT_DIR_SUMMARY, ARTIFACT_FILE_RUN_METRICS)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return {
        "path": _summary_artifact_ref(ARTIFACT_FILE_RUN_METRICS),
        "event_log_artifact": event_log_artifact,
        "events_observed": len(rows),
        "totals": payload["metrics"]["totals"],
    }


def _write_workflow_status(
    *,
    change_id: str,
    status: str,
    runner: str,
    model: str | None,
    repo: str,
    exit_code: int,
    failed_stage: str | None = None,
    last_completed_stage: str | None = None,
    exc: BaseException | None = None,
) -> Path | None:
    run_dir = _artifact_path(change_id)
    if not run_dir.is_dir():
        logger.warning("_write_workflow_status: run directory missing for change_id=%s", change_id)
        return None

    payload: dict[str, object] = {
        "change_id": change_id,
        "status": status,
        "runner": runner,
        "model": model,
        "repo": repo,
        "exit_code": exit_code,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    run_metrics = _write_run_metrics(change_id)
    if run_metrics is not None:
        payload["observability"] = run_metrics
    if failed_stage:
        payload["failed_stage"] = failed_stage
    if last_completed_stage:
        payload["last_completed_stage"] = last_completed_stage
    if exc is not None:
        payload["failure_summary"] = _summarize_exception(exc)
        payload["exception"] = _serialize_exception(exc)
        payload["traceback"] = _truncate_text(
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            limit=8000,
        )

    path = _workflow_status_path(change_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    logger.info("_write_workflow_status: wrote %s", path)
    return path


def clean_workspace(change_id: str) -> None:
    clean_change_workspace(
        change_id,
        agent_context_root=AGENT_CONTEXT_ROOT,
        logs_root=LOGS_ROOT,
        announce=print,
    )


def _load_runner_config() -> dict:
    """Load runner config using the shared server config loader."""
    try:
        from server.config import load_config
        return load_config()
    except Exception:
        return {}


def _artifact_display_path(change_id: str, *relative_parts: str) -> str:
    return str(_artifact_path(change_id, *relative_parts))


def _stage_artifact_dir(change_id: str, directory: str) -> Path:
    return _artifact_path(change_id, directory)


def _qa_evidence_root(change_id: str) -> Path:
    return _artifact_path(change_id, ARTIFACT_DIR_QA, ARTIFACT_DIR_QA_EVIDENCE)


def _autonomy_instruction(*, require_must: bool = False) -> str:
    escalation_verb = "you MUST use" if require_must else "use"
    return (
        "Act autonomously where the available artifacts and repository evidence are sufficient. "
        "If a blocking ambiguity, approval decision, or human-only product decision prevents safe progress, "
        f"{escalation_verb} the user escalation protocol and continue after the response."
    )


def _task_generation_input(*, change_id: str, repo: str) -> str:
    intake_dir = _artifact_display_path(change_id, ARTIFACT_DIR_INTAKE)
    story_path = _artifact_display_path(change_id, ARTIFACT_DIR_INTAKE, ARTIFACT_FILE_STORY)
    config_path = _artifact_display_path(change_id, ARTIFACT_DIR_INTAKE, ARTIFACT_FILE_INTAKE_CONFIG)
    constraints_path = _artifact_display_path(change_id, ARTIFACT_DIR_INTAKE, ARTIFACT_FILE_CONSTRAINTS)
    return (
        f"Generate a task plan for change {change_id} in {repo}.\n"
        f"Read the intake artifacts from {intake_dir}/.\n"
        f"Use {story_path} for story scope, {config_path} for intake/runtime metadata, "
        f"and {constraints_path} for implementation constraints.\n"
        "Produce a plan whose tasks map back to acceptance criteria, call out dependencies, "
        "and avoid work outside the requested change.\n"
        f"{_autonomy_instruction(require_must=True)}"
    )


def _task_generation_evaluator_prompt(*, change_id: str, repo: str) -> str:
    tasks_path = _artifact_display_path(change_id, ARTIFACT_DIR_PLANNING, ARTIFACT_FILE_TASKS)
    return (
        f"Evaluate the task plan for {change_id} in {repo}. "
        f"Read {tasks_path}. Check acceptance-criteria coverage, missing dependencies, "
        "oversized or underspecified tasks, unsafe scope expansion, testability, and whether "
        "the plan gives downstream assignment enough information to schedule execution."
    )


def _assignment_input(*, change_id: str, repo: str) -> str:
    tasks_path = _artifact_display_path(change_id, ARTIFACT_DIR_PLANNING, ARTIFACT_FILE_TASKS)
    story_path = _artifact_display_path(change_id, ARTIFACT_DIR_INTAKE, ARTIFACT_FILE_STORY)
    constraints_path = _artifact_display_path(change_id, ARTIFACT_DIR_INTAKE, ARTIFACT_FILE_CONSTRAINTS)
    return (
        f"Create an execution schedule for change {change_id}.\n"
        f"Read tasks from {tasks_path}.\n"
        f"Read story context from {story_path}.\n"
        f"Read constraints from {constraints_path}.\n"
        f"Target repo: {repo}\n"
        f"{_autonomy_instruction()}"
    )


def _assignment_evaluator_prompt(*, change_id: str) -> str:
    assignments_path = _artifact_display_path(change_id, ARTIFACT_DIR_PLANNING, ARTIFACT_FILE_ASSIGNMENTS)
    tasks_path = _artifact_display_path(change_id, ARTIFACT_DIR_PLANNING, ARTIFACT_FILE_TASKS)
    return (
        f"Evaluate the execution schedule for {change_id}. "
        f"Read {assignments_path} and {tasks_path}. Check that every task is assigned exactly "
        "once, dependencies are respected, parallel batches are safe, UoW boundaries are coherent, "
        "and each UoW has enough context for implementation and evaluation."
    )


def _qa_producer_input(*, change_id: str, repo: str, evidence_root: Path) -> str:
    story_path = _artifact_display_path(change_id, ARTIFACT_DIR_INTAKE, ARTIFACT_FILE_STORY)
    tasks_path = _artifact_display_path(change_id, ARTIFACT_DIR_PLANNING, ARTIFACT_FILE_TASKS)
    assignments_path = _artifact_display_path(change_id, ARTIFACT_DIR_PLANNING, ARTIFACT_FILE_ASSIGNMENTS)
    impl_reports_glob = _artifact_display_path(
        change_id,
        ARTIFACT_DIR_EXECUTION,
        "*",
        ARTIFACT_FILE_IMPL_REPORT,
    )
    qa_report_path = _artifact_display_path(change_id, ARTIFACT_DIR_QA, ARTIFACT_FILE_QA_REPORT)
    return (
        f"Perform QA validation for change {change_id}.\n"
        f"Read story ACs from {story_path}.\n"
        f"Read task plan from {tasks_path}.\n"
        f"Read assignments from {assignments_path}.\n"
        f"Read all implementation reports from {impl_reports_glob}.\n"
        f"Target repo: {repo}\n"
        f"Write your report to {qa_report_path}.\n"
        f"When you run tests, lint, build, or manual verification commands, save raw command output under "
        f"{evidence_root / ARTIFACT_DIR_QA_TEST_OUTPUT}/ or {evidence_root / ARTIFACT_DIR_QA_LOGS}/ "
        f"and reference those files from {ARTIFACT_FILE_QA_REPORT}.\n"
        f"{_autonomy_instruction()}"
    )


def _qa_evaluator_prompt(*, change_id: str) -> str:
    qa_report_path = _artifact_display_path(change_id, ARTIFACT_DIR_QA, ARTIFACT_FILE_QA_REPORT)
    story_path = _artifact_display_path(change_id, ARTIFACT_DIR_INTAKE, ARTIFACT_FILE_STORY)
    return (
        f"Evaluate the QA report for {change_id}. "
        f"Read {qa_report_path} and {story_path}."
    )


# ====================== CLI ====================== #

def configure_logging(log_level: str) -> None:
    logging_level = to_logging_level(log_level)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging_level)
    console_handler.setFormatter(LocalTimezoneFormatter(DEFAULT_LOG_FORMAT))
    logging.basicConfig(
        level=logging_level,
        handlers=[console_handler],
        force=True,
    )
    install_httpx_healthcheck_filter()
    if os.environ.get("AGENT_RUNNER_EVENT_LOG"):
        from server.events import EventEmitHandler
        event_handler = EventEmitHandler()
        event_handler.setLevel(logging_level)
        logging.getLogger().addHandler(event_handler)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the agent workflow against an ADO story or local fixture."
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="Target repository path. Defaults to the current working directory.",
    )
    parser.add_argument(
        "--change-id",
        default=None,
        help="Workflow change id. Optional for ADO URLs or fixtures that include change_id.",
    )
    parser.add_argument(
        "--ado-url",
        default=None,
        help="Azure DevOps work item URL for a live intake run.",
    )
    parser.add_argument(
        "--story-file",
        default=None,
        help=(
            "Path to a synthetic story fixture JSON file for local testing. "
            f"Defaults to {DEFAULT_TEST_STORY_FILE} when neither --ado-url nor --story-file is provided."
        ),
    )
    parser.add_argument(
        "--manual-story-file",
        default=None,
        help="Path to a JSON file containing a manually entered story payload.",
    )
    parser.add_argument(
        "--runner",
        default="claude",
        metavar="RUNNER",
        help=(
            "LLM provider or custom alias to use: 'claude' (Anthropic), "
            "'codex' (OpenAI Codex CLI), 'copilot' (GitHub Copilot), "
            "'gemini' (Google), 'openai-compat' (local /api/chat endpoint), "
            "or a custom alias defined in ~/.agent-runner/config.json under runner_aliases. "
            "Defaults to 'claude'."
        ),
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "Model to use for the selected runner. "
            "Defaults to the runner's default model if omitted."
        ),
    )
    parser.add_argument(
        "--agent-runner",
        action="append",
        type=_parse_agent_override_arg,
        default=[],
        metavar="AGENT=RUNNER",
        help="Per-run runner override for one workflow agent. May be repeated.",
    )
    parser.add_argument(
        "--agent-model",
        action="append",
        type=_parse_agent_override_arg,
        default=[],
        metavar="AGENT=MODEL",
        help="Per-run model override for one workflow agent. May be repeated.",
    )
    parser.add_argument(
        "--extra-context",
        default=None,
        help=(
            "Optional free-form context to pass to the intake agent — e.g., a "
            "reference PR URL and notes. Appended verbatim to the intake prompt."
        ),
    )
    parser.add_argument(
        "--skip-lessons-optimizer",
        action="store_true",
        help="Deprecated compatibility flag. The lessons optimizer is always disabled.",
    )
    materialize_group = parser.add_mutually_exclusive_group()
    materialize_group.add_argument(
        "--materialize",
        dest="skip_materialize",
        action="store_false",
        help="Opt in to copying enabled agent/skill/script files into runner-specific generated directories before the workflow starts.",
    )
    materialize_group.add_argument(
        "--skip-materialize",
        dest="skip_materialize",
        action="store_true",
        help="Do not materialize runner assets. This is the default so prompt-file changes remain explicit.",
    )
    parser.set_defaults(skip_materialize=True)
    parser.add_argument(
        "--calibration-fast-mode",
        action="store_true",
        help="Use a cheaper one-iteration workflow profile intended for synthesis calibration runs.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run without human prompts; escalation requests are auto-answered for eval/CI runs.",
    )
    parser.add_argument(
        "--log-level",
        type=normalize_log_level,
        default="warning",
        help="Logging verbosity: debug, info, warning, error, or critical.",
    )
    args = parser.parse_args(argv)
    args.agent_llm_overrides = _merge_agent_override_maps(
        _agent_override_pairs(args.agent_runner, "runner"),
        _agent_override_pairs(args.agent_model, "model"),
    )
    return args


# ====================== MAIN ====================== #

def main(
    repo: str | None = None,
    change_id: str | None = None,
    ado_url: str | None = None,
    story_file: str | None = None,
    manual_story_file: str | None = None,
    runner: str = "claude",
    model: str | None = None,
    agent_llm_overrides: dict[str, dict[str, str | None]] | None = None,
    extra_context: str | None = None,
    skip_lessons_optimizer: bool = True,
    skip_materialize: bool = True,
    calibration_fast_mode: bool = False,
    headless: bool = False,
    log_level: str = "warning",
):
    load_dotenv(RUNNER_ROOT / ".env", override=False)
    configure_logging(log_level)
    from core.run_cmds import set_runner_failover_policy
    set_runner_failover_policy(None)
    if headless:
        os.environ["AGENT_RUNNER_HEADLESS"] = "1"
        os.environ.setdefault("AGENT_RUNNER_USER_ESCALATION", "auto")
    logger.info(
        "main: starting workflow repo=%s change_id=%s runner=%s model=%s",
        repo, change_id, runner, model,
    )
    final_status = STATUS_SUCCEEDED
    final_exit = 0
    resolved_repo = repo or ""
    resolved_change_id = change_id or ""
    resolved_model: str | None = None
    failed_stage: str | None = None
    last_completed_stage: str | None = None
    feature_branch: str | None = None
    tracer = None

    try:
        workflow_input = resolve_workflow_input(
            repo=repo,
            change_id=change_id,
            ado_url=ado_url,
            story_file=story_file,
            manual_story_file=manual_story_file,
        )
        use_runner_root()
        resolved_repo = workflow_input.repo
        resolved_change_id = workflow_input.change_id
        intake_mode = workflow_input.intake_mode
        intake_source = workflow_input.intake_source

        logger.info(
            "main: resolved change_id=%s repo=%s intake_mode=%s",
            resolved_change_id, resolved_repo, intake_mode,
        )
        os.environ["AGENT_RUNNER_CHANGE_ID"] = resolved_change_id
        os.environ["AGENT_RUNNER_REPO"] = resolved_repo

        if os.environ.get("AGENT_RUNNER_EVENT_LOG"):
            logger.info("main: skipping clean_workspace; server pre-cleaned artifacts for %s", resolved_change_id)
        else:
            clean_workspace(resolved_change_id)

        # Force flush to confirm we survived clean_workspace
        logger.debug("clean_workspace completed, entering config load", flush=True)
        sys.stdout.flush()
        sys.stderr.flush()

        # Load config and resolve models
        logger.info("main: loading runner config")
        logger.debug("main: loading runner config", flush=True)
        try:
            config = _load_runner_config()
            logger.info("main: config loaded, keys=%s", list(config.keys()) if config else "EMPTY")
        except Exception as exc:
            logger.error("main: _load_runner_config FAILED: %s: %s", type(exc).__name__, exc)
            raise

        logger.info("main: resolving runner LLM config runner=%s model=%s", runner, model)
        try:
            runner_llm_config = resolve_runner_llm_config(runner, model, config)
            agent_llms = resolve_agent_llm_overrides(
                runner=runner,
                model=model,
                config=config,
                agent_llm_overrides=agent_llm_overrides,
            )
            resolved_model = runner_llm_config["model"]
            logger.info("main: resolved_model=%s", resolved_model)
        except Exception as exc:
            logger.error("main: resolve_runner_llm_config FAILED runner=%s model=%s: %s: %s",
                         runner, model, type(exc).__name__, exc)
            raise

        set_runner_failover_policy(
            _build_runner_failover_policy(config=config, current_runner=runner)
        )

        from core.opik_tracing import build_opik_tracer, maybe_trace

        logger.info("main: building Opik tracer")
        tracer = build_opik_tracer(
            settings=config.get("opik") or {},
            change_id=resolved_change_id,
            runner=runner,
            model=resolved_model,
            emit_event=_emit,
        )
        if tracer is None:
            logger.info("main: Opik tracing disabled; continuing without Opik")
        else:
            logger.info("main: Opik tracer constructed successfully")

        print(f"Running workflow for {resolved_change_id}")
        print(f"Target repo: {resolved_repo}")
        print(f"Intake mode: {intake_mode}")
        print(f"Intake source: {intake_source}")
        print(f"Runner: {runner}")
        print(f"Model: {resolved_model}")
        if calibration_fast_mode:
            print("Calibration fast mode enabled (single-iteration workflow loops).")
        if headless:
            print("Headless mode enabled (human escalations will be auto-answered).")

        logger.info(
            "main: preparing working branch repo=%s change_id=%s description_source=%r",
            resolved_repo,
            resolved_change_id,
            workflow_input.branch_description_source,
        )
        feature_branch = prepare_repo_branch(
            repo=resolved_repo,
            change_id=resolved_change_id,
            description_source=workflow_input.branch_description_source,
        )
        logger.info("main: prepared working branch %s", feature_branch)

        _emit(
            EVENT_TYPE_JOB_START,
            change_id=resolved_change_id,
            repo=resolved_repo,
            runner=runner,
            model=resolved_model,
            intake_mode=intake_mode,
            feature_branch=feature_branch,
        )
        story_source_metadata = _story_source_metadata(intake_mode=intake_mode, intake_source=intake_source)
        _emit(
            EVENT_TYPE_STORY_SOURCE,
            stage=STAGE_INTAKE,
            source=story_source_metadata.get("source"),
            story_file=story_source_metadata.get("story_file"),
            manual_story_file=story_source_metadata.get("manual_story_file"),
            original_ac_count=story_source_metadata.get("original_ac_count"),
        )
        _record_current_job_metadata(
            original_ac_count=story_source_metadata.get("original_ac_count"),
            story_source=story_source_metadata.get("source"),
        )

        # Cooperative cancellation
        def _on_term(signum, frame):  # noqa: ARG001
            logger.warning("main: SIGTERM received — emitting job.end cancelled and exiting 143")
            if resolved_change_id:
                _write_workflow_status(
                    change_id=resolved_change_id,
                    status=STATUS_CANCELLED,
                    runner=runner,
                    model=resolved_model,
                    repo=resolved_repo,
                    exit_code=143,
                    failed_stage=failed_stage,
                    last_completed_stage=last_completed_stage,
                )
            _emit(EVENT_TYPE_JOB_END, status=STATUS_CANCELLED, exit_code=143)
            sys.exit(143)

        try:
            signal.signal(signal.SIGTERM, _on_term)
        except (ValueError, OSError) as exc:
            logger.debug("main: could not install SIGTERM handler: %s", exc)

        # ── Stage 0: Materialize agents + skills ──────────────────────────
        from concurrent.futures import ThreadPoolExecutor
        from core.materialize import run_materialization
        import core.steps as steps
        from core.evaluator_optimizer_loops import run_eval_optimizer_loop, run_uow_eval_loop

        loop_iter_count = 1 if calibration_fast_mode else 3

        logger.info("main: entering workflow trace context")
        with maybe_trace(
            tracer,
            name="workflow:run",
            input={
                "change_id": resolved_change_id,
                "repo": resolved_repo,
                "runner": runner,
                "model": resolved_model,
                "intake_mode": intake_mode,
            },
        ):
            logger.info("main: inside workflow trace context, starting stages")
            with _Stage(STAGE_MATERIALIZE):
                if not skip_materialize:
                    logger.info("main: materializing enabled agents and skills from source trees by explicit operator request")
                    print("Materializing enabled agents and skills from source trees (--materialize was provided)...")
                    run_materialization()
                    logger.info("main: materialization complete")
                else:
                    logger.info("main: skipping materialization by default; pass --materialize to opt in")
                    print("Skipping materialization by default. Pass --materialize to update generated runner assets.")
                last_completed_stage = STAGE_MATERIALIZE

            # ── Stage 1: Intake ──────────────────────────────────────────────
            with _Stage(STAGE_INTAKE):
                failed_stage = STAGE_INTAKE
                logger.info("main: intake source=%s mode=%s runner=%s", intake_source, intake_mode, runner)
                _intake_artifact_dir = _stage_artifact_dir(resolved_change_id, ARTIFACT_DIR_INTAKE)
                if _intake_artifact_dir.is_dir():

                    # Always purge stale intake artifacts so agents never see data from a
                    # previous run of the same change_id, regardless of how the workflow
                    # was triggered.
                    shutil.rmtree(_intake_artifact_dir)

                logger.info("main: purged stale intake artifacts for change_id=%s", resolved_change_id)
                intake_llm = agent_llms[STAGE_INTAKE]
                logger.info(f"Starting intake stage: runner={intake_llm['runner']} model={intake_llm['model']}")
                steps.step_intake(
                    intake_source=intake_source,
                    repo=resolved_repo,
                    change_id=resolved_change_id,
                    intake_mode=intake_mode,
                    extra_context=extra_context,
                    feature_branch=feature_branch,
                    **_agent_llm_kwargs(agent_llms, STAGE_INTAKE)
                )
                last_completed_stage = STAGE_INTAKE
                failed_stage = None
                story_artifact_path = _require_file(
                    resolved_change_id,
                    STAGE_INTAKE,
                    ARTIFACT_DIR_INTAKE,
                    ARTIFACT_FILE_STORY,
                )
                normalized_story = _story_payload_from_path(story_artifact_path)
                normalized_ac_count = (
                    _acceptance_criteria_count(normalized_story.get(STORY_KEY_ACCEPTANCE_CRITERIA))
                    if normalized_story else None
                )
                _emit(
                    EVENT_TYPE_STORY_NORMALIZED,
                    stage=STAGE_INTAKE,
                    original_ac_count=story_source_metadata.get("original_ac_count"),
                    normalized_ac_count=normalized_ac_count,
                )
                _record_current_job_metadata(
                    original_ac_count=story_source_metadata.get("original_ac_count"),
                    normalized_ac_count=normalized_ac_count,
                )
                logger.info("main: intake stage complete, story.yaml verified")

            # ── Stage 2: Task Generation (eval-optimizer loop) ───────────────
            task_gen_stage_name = STAGE_TASK_GENERATION
            with _Stage(task_gen_stage_name):
                failed_stage = task_gen_stage_name
                # Always purge stale planning artifacts so the task-generator
                # never picks up a task plan or assignments from a previous run.
                _planning_artifact_dir = _stage_artifact_dir(resolved_change_id, ARTIFACT_DIR_PLANNING)
                if _planning_artifact_dir.is_dir():
                    shutil.rmtree(_planning_artifact_dir)
                    logger.info("main: purged stale planning artifacts for change_id=%s", resolved_change_id)

                task_gen_input = _task_generation_input(change_id=resolved_change_id, repo=resolved_repo)
                task_gen_evaluator_prompt = _task_generation_evaluator_prompt(
                    change_id=resolved_change_id,
                    repo=resolved_repo,
                )
                run_eval_optimizer_loop(
                    producer_func=steps.step_task_gen_producer,
                    producer_input=task_gen_input,
                    evaluator_func=steps.step_task_gen_evaluator,
                    evaluator_prompt=task_gen_evaluator_prompt,
                    iter_count=loop_iter_count,
                    **_agent_llm_kwargs(agent_llms, "task-generator"),
                    evaluator_runner=agent_llms["task-plan-evaluator"]["runner"],
                    evaluator_runner_model=agent_llms["task-plan-evaluator"]["model"],
                )
                last_completed_stage = task_gen_stage_name
                failed_stage = None
                _require_file(resolved_change_id, task_gen_stage_name, ARTIFACT_DIR_PLANNING, ARTIFACT_FILE_TASKS)

            # ── Stage 3: Task Assignment (eval-optimizer loop) ───────────────
            task_assign_stage_name = STAGE_TASK_ASSIGNMENT
            with _Stage(task_assign_stage_name):
                failed_stage = task_assign_stage_name

                assigner_input = _assignment_input(change_id=resolved_change_id, repo=resolved_repo)
                assignment_evaluator_prompt = _assignment_evaluator_prompt(change_id=resolved_change_id)
                run_eval_optimizer_loop(
                    producer_func=steps.step_task_assigner,
                    producer_input=assigner_input,
                    evaluator_func=steps.step_assignment_evaluator,
                    evaluator_prompt=assignment_evaluator_prompt,
                    iter_count=loop_iter_count,
                    **_agent_llm_kwargs(agent_llms, "task-assigner"),
                    evaluator_runner=agent_llms["assignment-evaluator"]["runner"],
                    evaluator_runner_model=agent_llms["assignment-evaluator"]["model"],
                )
                last_completed_stage = task_assign_stage_name
                failed_stage = None
                _require_file(
                    resolved_change_id,
                    task_assign_stage_name,
                    ARTIFACT_DIR_PLANNING,
                    ARTIFACT_FILE_ASSIGNMENTS,
                )

            # ── Stage 4: Execution — per-batch, parallel where safe ──────────
            execution_stage_name = STAGE_EXECUTION
            with _Stage(execution_stage_name):
                failed_stage = execution_stage_name
                assignments = load_assignments(resolved_change_id)
                batches = sorted(assignments.get(ASSIGNMENTS_KEY_BATCHES, []), key=lambda b: b[ASSIGNMENTS_KEY_BATCH_ID])
                total_uows = sum(len(batch.get(ASSIGNMENTS_KEY_UOWS, [])) for batch in batches)
                _emit(
                    EVENT_TYPE_WORKFLOW_PLAN,
                    stages=_workflow_stage_names(skip_lessons_optimizer=skip_lessons_optimizer),
                    total_stages=len(_workflow_stage_names(skip_lessons_optimizer=skip_lessons_optimizer)),
                    total_uows=total_uows,
                )
                logger.info("main: execution stage — %d batch(es) to run", len(batches))

                def _run_uow_with_events(*, uow_id: str, batch_id: int, ordinal: int) -> None:
                    _emit(
                        EVENT_TYPE_UOW_START,
                        stage=execution_stage_name,
                        batch_id=batch_id,
                        uow_id=uow_id,
                        ordinal=ordinal,
                        total_uows=total_uows,
                    )
                    try:
                        run_uow_eval_loop(
                            uow_id=uow_id,
                            change_id=resolved_change_id,
                            repo=resolved_repo,
                            iter_count=loop_iter_count,
                            **_agent_llm_kwargs(agent_llms, "software-engineer-hyperagent"),
                            evaluator_runner=agent_llms["implementation-evaluator"]["runner"],
                            evaluator_runner_model=agent_llms["implementation-evaluator"]["model"],
                        )
                    except BaseException as exc:
                        _emit(
                            EVENT_TYPE_UOW_END,
                            stage=execution_stage_name,
                            batch_id=batch_id,
                            uow_id=uow_id,
                            ordinal=ordinal,
                            total_uows=total_uows,
                            status=STATUS_ERROR,
                            error=_summarize_exception(exc)[:500],
                        )
                        raise
                    _emit(
                        EVENT_TYPE_UOW_END,
                        stage=execution_stage_name,
                        batch_id=batch_id,
                        uow_id=uow_id,
                        ordinal=ordinal,
                        total_uows=total_uows,
                        status=STATUS_OK,
                    )

                for batch in batches:
                    uow_ids = [uow[ASSIGNMENTS_KEY_UOW_ID] for uow in batch.get(ASSIGNMENTS_KEY_UOWS, [])]
                    is_parallel = batch.get(ASSIGNMENTS_KEY_PARALLEL_EXECUTION, False)
                    logger.info(
                        "main: batch %s — UoWs=%s parallel=%s",
                        batch[ASSIGNMENTS_KEY_BATCH_ID], uow_ids, is_parallel,
                    )
                    print(f"Executing batch {batch[ASSIGNMENTS_KEY_BATCH_ID]} — UoWs: {uow_ids} (parallel={is_parallel})")

                    if is_parallel and len(uow_ids) > 1:
                        with ThreadPoolExecutor() as executor:
                            futures = [
                                executor.submit(
                                    _run_uow_with_events,
                                    uow_id=uid,
                                    batch_id=batch[ASSIGNMENTS_KEY_BATCH_ID],
                                    ordinal=index,
                                )
                                for index, uid in enumerate(uow_ids, start=1)
                            ]
                            for future in futures:
                                future.result()
                    else:
                        for index, uid in enumerate(uow_ids, start=1):
                            _run_uow_with_events(
                                uow_id=uid,
                                batch_id=batch[ASSIGNMENTS_KEY_BATCH_ID],
                                ordinal=index,
                            )
                last_completed_stage = execution_stage_name
                failed_stage = None

            # ── Stage 5: QA Validation (eval-optimizer loop) ─────────────────
            with _Stage(STAGE_QA):
                failed_stage = STAGE_QA
                qa_evidence_root = _qa_evidence_root(resolved_change_id)
                for evidence_dir in (
                    ARTIFACT_DIR_QA_TEST_OUTPUT,
                    ARTIFACT_DIR_QA_LOGS,
                    ARTIFACT_DIR_QA_SCREENSHOTS,
                ):
                    (qa_evidence_root / evidence_dir).mkdir(parents=True, exist_ok=True)
                qa_producer_input = _qa_producer_input(
                    change_id=resolved_change_id,
                    repo=resolved_repo,
                    evidence_root=qa_evidence_root,
                )
                qa_evaluator_prompt = _qa_evaluator_prompt(change_id=resolved_change_id)
                run_eval_optimizer_loop(
                    producer_func=steps.step_qa_engineer,
                    producer_input=qa_producer_input,
                    evaluator_func=steps.step_qa_evaluator,
                    evaluator_prompt=qa_evaluator_prompt,
                    iter_count=loop_iter_count,
                    **_agent_llm_kwargs(agent_llms, "qa-engineer"),
                    evaluator_runner=agent_llms["qa-evaluator"]["runner"],
                    evaluator_runner_model=agent_llms["qa-evaluator"]["model"],
                )
                last_completed_stage = STAGE_QA
                failed_stage = None

            # ── Stage 6: Pull Request Creation + Review ─────────────────────
            if os.environ.get("AGENT_RUNNER_EVALUATION_RUN", "").strip().lower() in {"1", "true", "yes"}:
                print("Evaluation run detected; skipping PR creation and review.")
                last_completed_stage = STAGE_QA
                failed_stage = None
            else:
                with _Stage(STAGE_PR_REVIEW):
                    failed_stage = STAGE_PR_REVIEW
                    pr_review_path = steps.step_pr_review(
                        change_id=resolved_change_id,
                        repo=resolved_repo,
                        **_agent_llm_kwargs(agent_llms, "pr-reviewer"),
                    )
                    print(f"PR review saved to {pr_review_path}")
                    last_completed_stage = STAGE_PR_REVIEW
                    failed_stage = None

            # The lessons optimizer previously ran here and could inject rules
            # into agent prompt files. It is now disabled unconditionally so
            # prompt changes remain explicit operator actions.
            logger.info("main: lessons optimizer disabled; no optimizer agent will be invoked")
            print("Lessons optimizer disabled; no automatic prompt optimization will run.")


        if tracer is not None:
            tracer.flush()

        print(f"Workflow finished for {resolved_change_id}")

    except SystemExit:
        raise
    except BaseException as exc:
        final_status = STATUS_FAILED
        final_exit = 1
        failure_summary = _summarize_exception(exc)
        if isinstance(exc, INPUT_VALIDATION_ERRORS):
            logger.error("main: workflow FAILED — %s: %s", type(exc).__name__, failure_summary)
        else:
            logger.exception("main: workflow FAILED — %s: %s", type(exc).__name__, failure_summary)
        # Belt-and-suspenders: also write failure to stderr in case logging is buffered/lost
        try:
            print(f"[FATAL] Workflow failed: {type(exc).__name__}: {failure_summary}", file=sys.stderr, flush=True)
            traceback.print_exc(file=sys.stderr)
        except Exception:
            pass
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        if resolved_change_id:
            _write_workflow_status(
                change_id=resolved_change_id,
                status=final_status,
                runner=runner,
                model=resolved_model,
                repo=resolved_repo,
                exit_code=final_exit,
                failed_stage=failed_stage,
                last_completed_stage=last_completed_stage,
                exc=exc,
            )
        _emit(EVENT_TYPE_LOG, level=STATUS_ERROR, kind=EVENT_KIND_WORKFLOW_FAILED, msg=f"{type(exc).__name__}: {failure_summary}"[:1000])
        _emit(EVENT_TYPE_JOB_END, status=final_status, exit_code=final_exit)
        if tracer is not None:
            tracer.flush()
        raise
    else:
        logger.info("main: workflow SUCCEEDED change_id=%s", resolved_change_id)
        if resolved_change_id:
            _write_workflow_status(
                change_id=resolved_change_id,
                status=final_status,
                runner=runner,
                model=resolved_model,
                repo=resolved_repo,
                exit_code=final_exit,
                failed_stage=failed_stage,
                last_completed_stage=last_completed_stage,
            )
        _emit(EVENT_TYPE_JOB_END, status=final_status, exit_code=final_exit)
    finally:
        set_runner_failover_policy(None)

    return intake_source


def _rtk_available() -> bool:
    """Check if the RTK CLI binary is on PATH (kept for back-compat with imports)."""
    from core.rtk_terminal import rtk_available
    return rtk_available()


main.fn = main


if __name__ == "__main__":
    args = parse_args()
    try:
        main(
            repo=args.repo,
            change_id=args.change_id,
            ado_url=args.ado_url,
            story_file=args.story_file,
            manual_story_file=args.manual_story_file,
            runner=args.runner,
            model=args.model,
            agent_llm_overrides=args.agent_llm_overrides,
            extra_context=args.extra_context,
            skip_lessons_optimizer=args.skip_lessons_optimizer,
            skip_materialize=args.skip_materialize,
            calibration_fast_mode=args.calibration_fast_mode,
            headless=args.headless,
            log_level=args.log_level,
        )
    except INPUT_VALIDATION_ERRORS:
        sys.exit(1)
