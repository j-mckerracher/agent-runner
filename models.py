from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

WORKFLOW_ASSETS_ROOT = Path(__file__).resolve().parents[3] / ".claude"
DEFAULT_TIMEOUT_SECONDS = 3600
DEFAULT_ADO_ORGANIZATION = "https://dev.azure.com/mclm"
DEFAULT_ADO_PROJECT = "Mayo Collaborative Services"


class WorkflowError(RuntimeError):
    """Raised when the workflow cannot continue safely."""


@dataclass(frozen=True)
class BackendSpec:
    """Describes a supported interactive AI CLI backend."""

    key: str
    label: str
    command: str
    default_model: str | None = None


@dataclass(frozen=True)
class AgentSpec:
    """Describes a discovered custom agent prompt."""

    key: str
    name: str
    description: str
    path: Path


@dataclass
class WorkflowConfig:
    """Runtime configuration for the workflow runner."""

    repo_root: Path
    workflow_assets_root: Path
    change_id: str
    context: str
    artifact_root: Path
    cli_backend: str = "copilot"
    cli_bin: str = "copilot"
    model: str | None = None
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    dry_run: bool = False
    continue_on_failure: bool = False
    max_task_plan_attempts: int = 3
    max_assignment_attempts: int = 2
    max_implementation_attempts: int = 3
    max_qa_attempts: int = 3
    additional_dirs: list[Path] = field(default_factory=list)
    reuse_existing_intake: bool = False
    observability_sink: Any | None = None


@dataclass(frozen=True)
class ResumeCandidate:
    """A change with complete intake artifacts that can be reused."""

    change_id: str
    base_path: Path
    updated_at: float


@dataclass(frozen=True)
class WorkItemReference:
    """The Azure DevOps coordinates required to fetch a work item."""

    organization_url: str
    project: str
    work_item_id: str


@dataclass
class CommandResult:
    """Captures a completed subprocess invocation."""

    command: list[str]
    exit_code: int
    stdout: str
    stderr: str


@dataclass
class StageResult:
    """Result metadata for a stage or evaluator loop."""

    stage_name: str
    passed: bool
    attempts: int
    artifact_paths: list[Path]
    details: dict[str, Any] = field(default_factory=dict)


BACKEND_SPECS: tuple[BackendSpec, ...] = (
    BackendSpec(
        key="copilot",
        label="GitHub Copilot",
        command="copilot",
        default_model="claude-sonnet-4.6",
    ),
    BackendSpec(
        key="claude",
        label="Claude Code",
        command="claude",
        default_model="claude-sonnet-4-6",
    ),
)

STAGE_AGENT_ALIASES: dict[str, tuple[str, ...]] = {
    "intake": ("01-intake", "intake-agent"),
    "task_generator": ("02-task-generator", "task-generator"),
    "task_plan_evaluator": ("06-task-plan-evaluator", "task-plan-evaluator"),
    "task_assigner": ("03-task-assigner", "task-assigner"),
    "assignment_evaluator": ("07-assignment-evaluator", "assignment-evaluator"),
    "software_engineer": (
        "04-software-engineer-hyperagent",
        "software-engineer-hyperagent",
    ),
    "implementation_evaluator": (
        "08-implementation-evaluator",
        "implementation-evaluator",
    ),
    "qa": ("05-qa", "qa-engineer"),
    "qa_evaluator": ("09-qa-evaluator", "qa-evaluator"),
    "lessons_optimizer": (
        "11-lessons-optimizer-hyperagent",
        "lessons-optimizer-hyperagent",
    ),
}