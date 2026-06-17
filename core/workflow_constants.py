"""Workflow stage, artifact, event, and status constants for run orchestration."""

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
