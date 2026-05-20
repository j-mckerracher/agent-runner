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
load_dotenv()

from core.cli_logging import normalize_log_level, to_logging_level
from core.workspace_cleanup import clean_change_workspace
from core.ssl_compat import configure_system_ssl
from core.runner_models import (
    KNOWN_RUNNERS,
    RUNNER_MODEL_CHOICES,
    resolve_runner_llm_config,
)
from core.workflow_inputs import DEFAULT_TEST_STORY_FILE, resolve_workflow_input

configure_system_ssl()

logger = logging.getLogger(__name__)

INPUT_VALIDATION_ERRORS = (FileNotFoundError, ValueError)

# ====================== HELPERS ====================== #

RUNNER_ROOT = Path(__file__).resolve().parent
AGENT_CONTEXT_ROOT = RUNNER_ROOT / "agent-context"
LOGS_ROOT = RUNNER_ROOT / "logs"
WORKFLOW_STATUS_FILENAME = "workflow_status.yaml"


def load_assignments(change_id: str) -> dict:
    """Read assignments.json produced by the task-assigner stage."""
    from core.artifact_utils import load_assignments_file
    path = AGENT_CONTEXT_ROOT / change_id / "planning" / "assignments.json"
    return load_assignments_file(path)


def _require_dir(change_id: str, stage: str, *relative_parts: str) -> Path:
    """Raise FileNotFoundError with a clear message if a stage output is missing."""
    path = AGENT_CONTEXT_ROOT.joinpath(change_id, *relative_parts)
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
    "lessons-optimizer-hyperagent",
]


def _emit(type: str, **fields) -> None:
    """No-op unless AGENT_RUNNER_EVENT_LOG is set (server-driven runs)."""
    if not os.environ.get("AGENT_RUNNER_EVENT_LOG"):
        return
    try:
        from server.events import emit
        emit(type, **fields)
    except Exception:
        pass


def _workflow_stage_names(*, skip_lessons_optimizer: bool) -> list[str]:
    stages = [
        "materialize",
        "intake",
        "task-generation",
        "task-assignment",
        "execution",
        "qa",
    ]
    if not skip_lessons_optimizer:
        stages.append("lessons-optimizer")
    return stages


class _Stage:
    """Context manager that emits stage.start/stage.end events around a block."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._previous_stage: str | None = None

    def __enter__(self):
        self._previous_stage = os.environ.get("AGENT_RUNNER_CURRENT_STAGE")
        os.environ["AGENT_RUNNER_CURRENT_STAGE"] = self.name
        logger.info("Stage START: %s", self.name)
        _emit("stage.start", stage=self.name)
        return self

    def __exit__(self, exc_type, exc, tb):
        status = "ok" if exc_type is None else "error"
        if exc_type is not None:
            logger.error("Stage ERROR: %s — %s: %s", self.name, exc_type.__name__, str(exc) or "")
            _emit(
                "log",
                level="error",
                kind="stage_failed",
                stage=self.name,
                msg=f"{exc_type.__name__}: {str(exc)}"[:500],
            )
        else:
            logger.info("Stage END: %s (ok)", self.name)
        _emit("stage.end", stage=self.name, status=status)
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
    return AGENT_CONTEXT_ROOT / change_id / "summary" / WORKFLOW_STATUS_FILENAME


def _parse_event_timestamp(value: str | None) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _read_event_rows(change_id: str) -> list[dict]:
    path = LOGS_ROOT / change_id / "events.jsonl"
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
    source = LOGS_ROOT / change_id / "events.jsonl"
    if not source.is_file():
        return None
    destination = AGENT_CONTEXT_ROOT / change_id / "summary" / "events.jsonl"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return "summary/events.jsonl"


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
        event_type = row.get("type")
        event_ts = _parse_event_timestamp(row.get("ts"))
        if event_type == "stage.start" and event_ts and row.get("stage"):
            stage_starts[str(row["stage"])] = event_ts
        elif event_type == "stage.end" and event_ts and row.get("stage") in stage_starts:
            stage = str(row["stage"])
            stage_durations[stage] = round((event_ts - stage_starts[stage]).total_seconds(), 3)
        elif event_type == "uow.start" and event_ts and row.get("uow_id"):
            uow_starts[str(row["uow_id"])] = event_ts
        elif event_type == "uow.end" and event_ts and row.get("uow_id") in uow_starts:
            uow_id = str(row["uow_id"])
            uow_durations[uow_id] = round((event_ts - uow_starts[uow_id]).total_seconds(), 3)
        elif event_type == "opik.start" and str(row.get("name", "")).startswith("uow-iteration-"):
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            uow_id = metadata.get("uow_id")
            if uow_id:
                uow_iterations[str(uow_id)] = uow_iterations.get(str(uow_id), 0) + 1
        elif event_type == "cli.exit":
            totals["cli_calls"] += 1
            agent = str(row.get("agent") or "unknown")
            summary = cli_calls_by_agent.setdefault(agent, {"count": 0, "duration_ms": 0})
            summary["count"] = int(summary["count"]) + 1
            summary["duration_ms"] = int(summary["duration_ms"]) + int(row.get("duration_ms") or 0)
        elif event_type == "metrics":
            totals["metrics_events"] += 1
            legacy_metric_totals["tokens_in"] += int(row.get("tokens_in") or 0)
            legacy_metric_totals["tokens_out"] += int(row.get("tokens_out") or 0)
            legacy_metric_totals["cost_usd"] = round(
                float(legacy_metric_totals["cost_usd"]) + float(row.get("cost_usd") or 0.0),
                6,
            )
        elif event_type == "llm.call":
            totals["llm_calls"] += 1
            agent = str(row.get("agent") or "unknown")
            model = str(row.get("model") or "unknown")
            duration_ms = row.get("duration_ms")
            if isinstance(duration_ms, (int, float)):
                llm_latencies_by_agent.setdefault(agent, []).append(float(duration_ms))
            for key, bucket_name in ((agent, "agent"), (model, "model")):
                bucket = llm_calls_by_agent if bucket_name == "agent" else llm_calls_by_model
                summary = bucket.setdefault(
                    key,
                    {"count": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "errors": 0, "retries": 0},
                )
                summary["count"] = int(summary["count"]) + 1
                summary["tokens_in"] = int(summary["tokens_in"]) + int(row.get("tokens_in") or row.get("prompt_est_tokens") or 0)
                summary["tokens_out"] = int(summary["tokens_out"]) + int(row.get("tokens_out") or row.get("response_est_tokens") or 0)
                summary["cost_usd"] = round(float(summary["cost_usd"]) + float(row.get("cost_usd") or 0.0), 6)
                if row.get("status") not in (None, "ok", "tool_call"):
                    summary["errors"] = int(summary["errors"]) + 1
                if int(row.get("attempt") or 1) > 1 or row.get("retryable"):
                    summary["retries"] = int(summary["retries"]) + 1
            category = row.get("error_category")
            if category:
                error_categories[str(category)] = error_categories.get(str(category), 0) + 1
            prompt_hash = row.get("prompt_sha256")
            if isinstance(prompt_hash, str) and prompt_hash:
                prompt_hash_counts[prompt_hash] = prompt_hash_counts.get(prompt_hash, 0) + 1
            llm_calls.append(
                {
                    key: row.get(key)
                    for key in (
                        "runner",
                        "agent",
                        "model",
                        "status",
                        "duration_ms",
                        "attempt",
                        "max_attempts",
                        "prompt_chars",
                        "response_chars",
                        "prompt_est_tokens",
                        "response_est_tokens",
                        "tokens_in",
                        "tokens_out",
                        "cost_usd",
                        "error_category",
                        "retryable",
                        "response_parse_ok",
                        "tool_call_count",
                        "tool_step_count",
                        "cache_static_prefix_est_tokens",
                        "connection_reuse_observable",
                        "max_tokens",
                        "temperature",
                        "prompt_sha256",
                        "response_sha256",
                    )
                    if key in row
                }
            )

    latency_by_agent = {agent: _latency_summary(values) for agent, values in llm_latencies_by_agent.items()}
    cost_values = [float(call.get("cost_usd") or 0.0) for call in llm_calls]
    cost_threshold = (sum(cost_values) / len(cost_values) * 3) if cost_values else 0.0
    repeated_prompts = {key: count for key, count in prompt_hash_counts.items() if count > 1}
    if llm_calls:
        totals["tokens_in"] = sum(int(call.get("tokens_in") or call.get("prompt_est_tokens") or 0) for call in llm_calls)
        totals["tokens_out"] = sum(int(call.get("tokens_out") or call.get("response_est_tokens") or 0) for call in llm_calls)
        totals["cost_usd"] = round(sum(float(call.get("cost_usd") or 0.0) for call in llm_calls), 6)
        totals["source"] = "llm.call"
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
            "overall": _latency_summary([float(call["duration_ms"]) for call in llm_calls if isinstance(call.get("duration_ms"), (int, float))]),
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
            if cost_threshold > 0 and float(call.get("cost_usd") or 0.0) > cost_threshold
        ],
        "totals": totals,
        "answerability_matrix": _answerability_matrix(),
    }


def _write_run_metrics(change_id: str) -> dict | None:
    run_dir = AGENT_CONTEXT_ROOT / change_id
    if not run_dir.is_dir():
        return None
    event_log_artifact = _copy_event_log_to_summary(change_id)
    rows = _read_event_rows(change_id)
    payload = {
        "change_id": change_id,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source_event_log": str(LOGS_ROOT / change_id / "events.jsonl"),
        "event_log_artifact": event_log_artifact,
        "events_observed": len(rows),
        "metrics": _summarize_event_rows(rows),
    }
    path = run_dir / "summary" / "run_metrics.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return {
        "path": "summary/run_metrics.yaml",
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
    run_dir = AGENT_CONTEXT_ROOT / change_id
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


# ====================== CLI ====================== #

def configure_logging(log_level: str) -> None:
    logging.basicConfig(
        level=to_logging_level(log_level),
        format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        force=True,
    )
    if os.environ.get("AGENT_RUNNER_EVENT_LOG"):
        from server.events import EventEmitHandler
        logging.getLogger().addHandler(EventEmitHandler())


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
        "--runner",
        default="claude",
        metavar="RUNNER",
        help=(
            "LLM provider or custom alias to use: 'claude' (Anthropic), "
            "'copilot' (OpenAI), 'gemini' (Google), or a custom alias "
            "defined in ~/.agent-runner/config.json under runner_aliases. "
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
        help="Skip the lessons optimizer stage at the end of the workflow.",
    )
    parser.add_argument(
        "--skip-materialize",
        action="store_true",
        help="Skip materialization of agents/skills into runner-specific directories.",
    )
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
    return args


# ====================== MAIN ====================== #

def main(
    repo: str | None = None,
    change_id: str | None = None,
    ado_url: str | None = None,
    story_file: str | None = None,
    runner: str = "claude",
    model: str | None = None,
    extra_context: str | None = None,
    skip_lessons_optimizer: bool = False,
    skip_materialize: bool = False,
    calibration_fast_mode: bool = False,
    headless: bool = False,
    log_level: str = "warning",
):
    configure_logging(log_level)
    if headless:
        os.environ["AGENT_RUNNER_HEADLESS"] = "1"
        os.environ.setdefault("AGENT_RUNNER_USER_ESCALATION", "auto")
    logger.info(
        "main: starting workflow repo=%s change_id=%s runner=%s model=%s",
        repo, change_id, runner, model,
    )
    final_status = "succeeded"
    final_exit = 0
    resolved_repo = repo or ""
    resolved_change_id = change_id or ""
    resolved_model: str | None = None
    failed_stage: str | None = None
    last_completed_stage: str | None = None
    tracer = None

    try:
        workflow_input = resolve_workflow_input(
            repo=repo,
            change_id=change_id,
            ado_url=ado_url,
            story_file=story_file,
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
        print("[DEBUG] clean_workspace completed, entering config load", flush=True)
        sys.stdout.flush()
        sys.stderr.flush()

        # Load config and resolve models
        logger.info("main: loading runner config")
        print("[DEBUG] main: loading runner config", flush=True)
        try:
            config = _load_runner_config()
            logger.info("main: config loaded, keys=%s", list(config.keys()) if config else "EMPTY")
        except Exception as exc:
            logger.error("main: _load_runner_config FAILED: %s: %s", type(exc).__name__, exc)
            print(f"[ERROR] Failed to load runner config: {type(exc).__name__}: {exc}")
            raise

        logger.info("main: resolving runner LLM config runner=%s model=%s", runner, model)
        try:
            runner_llm_config = resolve_runner_llm_config(runner, model, config)
            resolved_model = runner_llm_config["model"]
            logger.info("main: resolved_model=%s", resolved_model)
        except Exception as exc:
            logger.error("main: resolve_runner_llm_config FAILED runner=%s model=%s: %s: %s",
                         runner, model, type(exc).__name__, exc)
            print(f"[ERROR] Failed to resolve runner model config: {type(exc).__name__}: {exc}")
            raise

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

        _emit(
            "job.start",
            change_id=resolved_change_id,
            repo=resolved_repo,
            runner=runner,
            model=resolved_model,
            intake_mode=intake_mode,
        )

        # Cooperative cancellation
        def _on_term(signum, frame):  # noqa: ARG001
            logger.warning("main: SIGTERM received — emitting job.end cancelled and exiting 143")
            if resolved_change_id:
                _write_workflow_status(
                    change_id=resolved_change_id,
                    status="cancelled",
                    runner=runner,
                    model=resolved_model,
                    repo=resolved_repo,
                    exit_code=143,
                    failed_stage=failed_stage,
                    last_completed_stage=last_completed_stage,
                )
            _emit("job.end", status="cancelled", exit_code=143)
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

        runner_model_kwargs: dict = {"runner_model": resolved_model}
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
            with _Stage("materialize"):
                if not skip_materialize:
                    logger.info("main: materializing agents and skills from source trees")
                    print("Materializing agents and skills from source trees...")
                    run_materialization()
                    logger.info("main: materialization complete")
                else:
                    logger.info("main: skipping materialization (--skip-materialize)")
                    print("Skipping materialization (--skip-materialize).")
                last_completed_stage = "materialize"

            # ── Stage 1: Intake ──────────────────────────────────────────────
            with _Stage("intake"):
                failed_stage = "intake"
                logger.info("main: intake source=%s mode=%s runner=%s", intake_source, intake_mode, runner)
                # Always purge stale intake artifacts so agents never see data from a
                # previous run of the same change_id, regardless of how the workflow
                # was triggered.
                _intake_artifact_dir = AGENT_CONTEXT_ROOT / resolved_change_id / "intake"
                if _intake_artifact_dir.is_dir():
                    shutil.rmtree(_intake_artifact_dir)
                    logger.info("main: purged stale intake artifacts for change_id=%s", resolved_change_id)
                print(f"[intake] Starting intake stage: runner={runner} model={resolved_model}")
                steps.step_intake(
                    intake_source=intake_source,
                    repo=resolved_repo,
                    change_id=resolved_change_id,
                    intake_mode=intake_mode,
                    runner=runner,
                    extra_context=extra_context,
                    **runner_model_kwargs,
                )
                last_completed_stage = "intake"
                failed_stage = None
                _require_file(resolved_change_id, "intake", "intake", "story.yaml")
                logger.info("main: intake stage complete, story.yaml verified")

            # ── Stage 2: Task Generation (eval-optimizer loop) ───────────────
            with _Stage("task-generation"):
                failed_stage = "task-generation"
                # Always purge stale planning artifacts so the task-generator
                # never picks up a task plan or assignments from a previous run.
                _planning_artifact_dir = AGENT_CONTEXT_ROOT / resolved_change_id / "planning"
                if _planning_artifact_dir.is_dir():
                    shutil.rmtree(_planning_artifact_dir)
                    logger.info("main: purged stale planning artifacts for change_id=%s", resolved_change_id)
                task_gen_input = (
                    f"Generate a task plan for change {resolved_change_id} in {resolved_repo}.\n"
                    f"Read the intake artifacts from {AGENT_CONTEXT_ROOT}/{resolved_change_id}/intake/.\n"
                    f"Act autonomously where the available artifacts and repository evidence are sufficient. "
                    f"If a blocking ambiguity, approval decision, or human-only product decision prevents safe progress, "
                    f"use the user escalation protocol and continue after the response."
                )
                task_gen_evaluator_prompt = (
                    f"Evaluate the task plan for {resolved_change_id} in {resolved_repo}. "
                    f"Read {AGENT_CONTEXT_ROOT}/{resolved_change_id}/planning/tasks.yaml."
                )
                run_eval_optimizer_loop(
                    producer_func=steps.step_task_gen_producer,
                    producer_input=task_gen_input,
                    evaluator_func=steps.step_task_gen_evaluator,
                    evaluator_prompt=task_gen_evaluator_prompt,
                    iter_count=loop_iter_count,
                    runner=runner,
                    **runner_model_kwargs,
                )
                last_completed_stage = "task-generation"
                failed_stage = None
                _require_file(resolved_change_id, "task-generation", "planning", "tasks.yaml")

            # ── Stage 3: Task Assignment (eval-optimizer loop) ───────────────
            with _Stage("task-assignment"):
                failed_stage = "task-assignment"
                assigner_input = (
                    f"Create an execution schedule for change {resolved_change_id}.\n"
                    f"Read tasks from {AGENT_CONTEXT_ROOT}/{resolved_change_id}/planning/tasks.yaml.\n"
                    f"Read story context from {AGENT_CONTEXT_ROOT}/{resolved_change_id}/intake/story.yaml.\n"
                    f"Read constraints from {AGENT_CONTEXT_ROOT}/{resolved_change_id}/intake/constraints.md.\n"
                    f"Target repo: {resolved_repo}\n"
                    f"Act autonomously where the available artifacts and repository evidence are sufficient. "
                    f"If a blocking ambiguity, approval decision, or human-only product decision prevents safe progress, "
                    f"use the user escalation protocol and continue after the response."
                )
                assignment_evaluator_prompt = (
                    f"Evaluate the execution schedule for {resolved_change_id}. "
                    f"Read {AGENT_CONTEXT_ROOT}/{resolved_change_id}/planning/assignments.json and "
                    f"{AGENT_CONTEXT_ROOT}/{resolved_change_id}/planning/tasks.yaml."
                )
                run_eval_optimizer_loop(
                    producer_func=steps.step_task_assigner,
                    producer_input=assigner_input,
                    evaluator_func=steps.step_assignment_evaluator,
                    evaluator_prompt=assignment_evaluator_prompt,
                    iter_count=loop_iter_count,
                    runner=runner,
                    **runner_model_kwargs,
                )
                last_completed_stage = "task-assignment"
                failed_stage = None
                _require_file(resolved_change_id, "task-assignment", "planning", "assignments.json")

            # ── Stage 4: Execution — per-batch, parallel where safe ──────────
            with _Stage("execution"):
                failed_stage = "execution"
                assignments = load_assignments(resolved_change_id)
                batches = sorted(assignments.get("batches", []), key=lambda b: b["batch_id"])
                total_uows = sum(len(batch.get("uows", [])) for batch in batches)
                _emit(
                    "workflow.plan",
                    stages=_workflow_stage_names(skip_lessons_optimizer=skip_lessons_optimizer),
                    total_stages=len(_workflow_stage_names(skip_lessons_optimizer=skip_lessons_optimizer)),
                    total_uows=total_uows,
                )
                logger.info("main: execution stage — %d batch(es) to run", len(batches))

                def _run_uow_with_events(*, uow_id: str, batch_id: int, ordinal: int) -> None:
                    _emit(
                        "uow.start",
                        stage="execution",
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
                            runner=runner,
                            **runner_model_kwargs,
                        )
                    except BaseException as exc:
                        _emit(
                            "uow.end",
                            stage="execution",
                            batch_id=batch_id,
                            uow_id=uow_id,
                            ordinal=ordinal,
                            total_uows=total_uows,
                            status="error",
                            error=_summarize_exception(exc)[:500],
                        )
                        raise
                    _emit(
                        "uow.end",
                        stage="execution",
                        batch_id=batch_id,
                        uow_id=uow_id,
                        ordinal=ordinal,
                        total_uows=total_uows,
                        status="ok",
                    )

                for batch in batches:
                    uow_ids = [uow["uow_id"] for uow in batch.get("uows", [])]
                    is_parallel = batch.get("parallel_execution", False)
                    logger.info(
                        "main: batch %s — UoWs=%s parallel=%s",
                        batch["batch_id"], uow_ids, is_parallel,
                    )
                    print(f"Executing batch {batch['batch_id']} — UoWs: {uow_ids} (parallel={is_parallel})")

                    if is_parallel and len(uow_ids) > 1:
                        with ThreadPoolExecutor() as executor:
                            futures = [
                                executor.submit(
                                    _run_uow_with_events,
                                    uow_id=uid,
                                    batch_id=batch["batch_id"],
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
                                batch_id=batch["batch_id"],
                                ordinal=index,
                            )
                last_completed_stage = "execution"
                failed_stage = None

            # ── Stage 5: QA Validation (eval-optimizer loop) ─────────────────
            with _Stage("qa"):
                failed_stage = "qa"
                qa_evidence_root = AGENT_CONTEXT_ROOT / resolved_change_id / "qa" / "evidence"
                for evidence_dir in ("test_output", "logs", "screenshots"):
                    (qa_evidence_root / evidence_dir).mkdir(parents=True, exist_ok=True)
                qa_producer_input = (
                    f"Perform QA validation for change {resolved_change_id}.\n"
                    f"Read story ACs from {AGENT_CONTEXT_ROOT}/{resolved_change_id}/intake/story.yaml.\n"
                    f"Read task plan from {AGENT_CONTEXT_ROOT}/{resolved_change_id}/planning/tasks.yaml.\n"
                    f"Read assignments from {AGENT_CONTEXT_ROOT}/{resolved_change_id}/planning/assignments.json.\n"
                    f"Read all implementation reports from {AGENT_CONTEXT_ROOT}/{resolved_change_id}/execution/*/impl_report.yaml.\n"
                    f"Target repo: {resolved_repo}\n"
                    f"Write your report to {AGENT_CONTEXT_ROOT}/{resolved_change_id}/qa/qa_report.yaml.\n"
                    f"When you run tests, lint, build, or manual verification commands, save raw command output under "
                    f"{qa_evidence_root}/test_output/ or {qa_evidence_root}/logs/ and reference those files from qa_report.yaml.\n"
                    f"Act autonomously where the available artifacts and repository evidence are sufficient. "
                    f"If a blocking ambiguity, approval decision, or human-only product decision prevents safe progress, "
                    f"use the user escalation protocol and continue after the response."
                )
                qa_evaluator_prompt = (
                    f"Evaluate the QA report for {resolved_change_id}. "
                    f"Read {AGENT_CONTEXT_ROOT}/{resolved_change_id}/qa/qa_report.yaml and "
                    f"{AGENT_CONTEXT_ROOT}/{resolved_change_id}/intake/story.yaml."
                )
                run_eval_optimizer_loop(
                    producer_func=steps.step_qa_engineer,
                    producer_input=qa_producer_input,
                    evaluator_func=steps.step_qa_evaluator,
                    evaluator_prompt=qa_evaluator_prompt,
                    iter_count=loop_iter_count,
                    runner=runner,
                    **runner_model_kwargs,
                )
                last_completed_stage = "qa"
                failed_stage = None

            # ── Stage 6: Lessons Optimization (one-shot) ─────────────────────
            if skip_lessons_optimizer:
                logger.info("main: skipping lessons optimizer (--skip-lessons-optimizer)")
                print("Skipping lessons optimizer (--skip-lessons-optimizer).")
            else:
                with _Stage("lessons-optimizer"):
                    failed_stage = "lessons-optimizer"
                    steps.step_lessons_optimizer(
                        change_id=resolved_change_id,
                        repo=resolved_repo,
                        runner=runner,
                        **runner_model_kwargs,
                    )
                    last_completed_stage = "lessons-optimizer"
                    failed_stage = None


        if tracer is not None:
            tracer.flush()

        print(f"Workflow finished for {resolved_change_id}")

    except SystemExit:
        raise
    except BaseException as exc:
        final_status = "failed"
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
        _emit("log", level="error", kind="workflow_failed", msg=f"{type(exc).__name__}: {failure_summary}"[:1000])
        _emit("job.end", status=final_status, exit_code=final_exit)
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
        _emit("job.end", status=final_status, exit_code=final_exit)

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
            runner=args.runner,
            model=args.model,
            extra_context=args.extra_context,
            skip_lessons_optimizer=args.skip_lessons_optimizer,
            skip_materialize=args.skip_materialize,
            calibration_fast_mode=args.calibration_fast_mode,
            headless=args.headless,
            log_level=args.log_level,
        )
    except INPUT_VALIDATION_ERRORS:
        sys.exit(1)
