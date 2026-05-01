"""Typed shared models for the evaluation framework."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Union, cast

Difficulty = Literal["low", "medium", "high"]
SuiteTier = Literal["easy", "medium", "hard"]
Mechanism = Literal["contains", "matches", "command"]
FailureReason = Literal["BUILD_ERROR", "ASSERTION_MISS", "TIMEOUT", "NO_ATTEMPT"]

VALID_DIFFICULTIES = {"low", "medium", "high"}
VALID_SUITE_TIERS = {"easy", "medium", "hard"}
VALID_MECHANISMS = {"contains", "matches", "command"}
VALID_FAILURE_REASONS = {"BUILD_ERROR", "ASSERTION_MISS", "TIMEOUT", "NO_ATTEMPT"}


def _require_non_empty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _validate_member(value: Any, allowed: set[str], field_name: str) -> str:
    if value not in allowed:
        expected = ", ".join(sorted(allowed))
        raise ValueError(f"{field_name} must be one of: {expected}")
    return cast(str, value)


def _mapping_copy(value: Mapping[str, Any], field_name: str) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    return dict(value)


def _clean(value: Any) -> Any:
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items() if item is not None}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    return value


@dataclass(frozen=True)
class CheckDefinition:
    """A single acceptance-check definition.

    For `contains` and `matches` checks, `expected` stores the substring or
    regular expression. For `command` checks, `command` stores either an argv
    sequence or a shell-like string that will be split safely by helpers.
    """

    id: str
    label: str
    mechanism: Mechanism
    subject: str
    expected: Optional[str] = None
    command: Optional[Union[str, Sequence[str]]] = None
    timeout_seconds: Optional[int] = None
    difficulty: Optional[Difficulty] = None
    suggested_difficulty: Optional[Difficulty] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty_string(self.id, "id")
        _require_non_empty_string(self.label, "label")
        _require_non_empty_string(self.subject, "subject")
        _validate_member(self.mechanism, VALID_MECHANISMS, "mechanism")
        if self.mechanism in {"contains", "matches"}:
            _require_non_empty_string(self.expected, "expected")
        if self.mechanism == "command" and self.command is None:
            raise ValueError("command checks require a command")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive when provided")
        if self.difficulty is not None:
            _validate_member(self.difficulty, VALID_DIFFICULTIES, "difficulty")
        if self.suggested_difficulty is not None:
            _validate_member(self.suggested_difficulty, VALID_DIFFICULTIES, "suggested_difficulty")
        if self.command is not None and not isinstance(self.command, str):
            object.__setattr__(self, "command", list(self.command))
        object.__setattr__(self, "metadata", _mapping_copy(self.metadata, "metadata"))

    def to_dict(self) -> Dict[str, Any]:
        return cast(Dict[str, Any], _clean(self))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CheckDefinition":
        payload = _mapping_copy(data, "check definition")
        return cls(
            id=payload["id"],
            label=payload.get("label", payload["id"]),
            mechanism=payload["mechanism"],
            subject=payload.get("subject", "agent_output"),
            expected=payload.get("expected"),
            command=payload.get("command"),
            timeout_seconds=payload.get("timeout_seconds"),
            difficulty=payload.get("difficulty"),
            suggested_difficulty=payload.get("suggested_difficulty"),
            metadata=payload.get("metadata", {}),
        )


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    passed: bool
    attempted: bool = True
    mechanism: Optional[Mechanism] = None
    subject: Optional[str] = None
    difficulty: Optional[Difficulty] = None
    failure_reason: Optional[FailureReason] = None
    message: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty_string(self.check_id, "check_id")
        if self.mechanism is not None:
            _validate_member(self.mechanism, VALID_MECHANISMS, "mechanism")
        if self.difficulty is not None:
            _validate_member(self.difficulty, VALID_DIFFICULTIES, "difficulty")
        if self.failure_reason is not None:
            _validate_member(self.failure_reason, VALID_FAILURE_REASONS, "failure_reason")
        object.__setattr__(self, "metadata", _mapping_copy(self.metadata, "metadata"))

    def to_dict(self) -> Dict[str, Any]:
        return cast(Dict[str, Any], _clean(self))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CheckResult":
        payload = _mapping_copy(data, "check result")
        return cls(
            check_id=payload["check_id"],
            passed=bool(payload["passed"]),
            attempted=bool(payload.get("attempted", True)),
            mechanism=payload.get("mechanism"),
            subject=payload.get("subject"),
            difficulty=payload.get("difficulty"),
            failure_reason=payload.get("failure_reason"),
            message=payload.get("message", ""),
            metadata=payload.get("metadata", {}),
        )


@dataclass(frozen=True)
class ScoreSummary:
    total_checks: int
    passed_checks: int
    attempted_checks: int
    score_tier_low: float = 0.0
    score_tier_medium: float = 0.0
    score_tier_high: float = 0.0
    weighted_composite: float = 0.0
    attempted_rate: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.total_checks < 0 or self.passed_checks < 0 or self.attempted_checks < 0:
            raise ValueError("check counts must be non-negative")
        if self.passed_checks > self.total_checks:
            raise ValueError("passed_checks cannot exceed total_checks")
        if self.attempted_checks > self.total_checks:
            raise ValueError("attempted_checks cannot exceed total_checks")
        object.__setattr__(self, "metadata", _mapping_copy(self.metadata, "metadata"))

    def to_dict(self) -> Dict[str, Any]:
        return cast(Dict[str, Any], _clean(self))


@dataclass(frozen=True)
class DatasetManifest:
    dataset_id: str
    display_name: str
    source: Mapping[str, Any]
    sampling: Mapping[str, Any]
    domain_context: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty_string(self.dataset_id, "dataset_id")
        _require_non_empty_string(self.display_name, "display_name")
        _require_non_empty_string(self.domain_context, "domain_context")
        object.__setattr__(self, "source", _mapping_copy(self.source, "source"))
        object.__setattr__(self, "sampling", _mapping_copy(self.sampling, "sampling"))
        object.__setattr__(self, "metadata", _mapping_copy(self.metadata, "metadata"))

    def to_dict(self) -> Dict[str, Any]:
        return cast(Dict[str, Any], _clean(self))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DatasetManifest":
        payload = _mapping_copy(data, "dataset manifest")
        return cls(
            dataset_id=payload["dataset_id"],
            display_name=payload["display_name"],
            source=payload["source"],
            sampling=payload["sampling"],
            domain_context=payload["domain_context"],
            metadata=payload.get("metadata", {}),
        )


@dataclass(frozen=True)
class DatasetLock:
    dataset_id: str
    source_fingerprint: str
    sample_path: str
    record_count: int
    schema: Mapping[str, Any]
    seed: Optional[int] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty_string(self.dataset_id, "dataset_id")
        _require_non_empty_string(self.source_fingerprint, "source_fingerprint")
        _require_non_empty_string(self.sample_path, "sample_path")
        if self.record_count < 0:
            raise ValueError("record_count must be non-negative")
        object.__setattr__(self, "schema", _mapping_copy(self.schema, "schema"))
        object.__setattr__(self, "metadata", _mapping_copy(self.metadata, "metadata"))

    def to_dict(self) -> Dict[str, Any]:
        return cast(Dict[str, Any], _clean(self))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DatasetLock":
        payload = _mapping_copy(data, "dataset lock")
        return cls(
            dataset_id=payload["dataset_id"],
            source_fingerprint=payload["source_fingerprint"],
            sample_path=payload["sample_path"],
            record_count=int(payload["record_count"]),
            schema=payload["schema"],
            seed=payload.get("seed"),
            metadata=payload.get("metadata", {}),
        )


@dataclass(frozen=True)
class AcceptanceCriterion:
    ac_id: str
    text: str
    tier: SuiteTier
    check: Optional[CheckDefinition] = None
    rationale: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty_string(self.ac_id, "ac_id")
        _require_non_empty_string(self.text, "text")
        _validate_member(self.tier, VALID_SUITE_TIERS, "tier")
        object.__setattr__(self, "metadata", _mapping_copy(self.metadata, "metadata"))

    def to_dict(self) -> Dict[str, Any]:
        return cast(Dict[str, Any], _clean(self))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AcceptanceCriterion":
        payload = _mapping_copy(data, "acceptance criterion")
        check_payload = payload.get("check") or payload.get("check_definition")
        return cls(
            ac_id=payload["ac_id"],
            text=payload["text"],
            tier=payload["tier"],
            check=CheckDefinition.from_dict(check_payload) if check_payload else None,
            rationale=payload.get("rationale", ""),
            metadata=payload.get("metadata", {}),
        )


@dataclass(frozen=True)
class EvalStory:
    story_id: str
    title: str
    description: str
    acceptance_criteria: Sequence[AcceptanceCriterion] = field(default_factory=list)
    change_id: Optional[str] = None
    suite_tier: Optional[SuiteTier] = None
    dataset_id: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty_string(self.story_id, "story_id")
        _require_non_empty_string(self.title, "title")
        _require_non_empty_string(self.description, "description")
        if self.change_id is not None:
            _require_non_empty_string(self.change_id, "change_id")
        if self.suite_tier is not None:
            _validate_member(self.suite_tier, VALID_SUITE_TIERS, "suite_tier")
        object.__setattr__(self, "acceptance_criteria", list(self.acceptance_criteria))
        object.__setattr__(self, "metadata", _mapping_copy(self.metadata, "metadata"))

    def to_dict(self) -> Dict[str, Any]:
        return cast(Dict[str, Any], _clean(self))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvalStory":
        payload = _mapping_copy(data, "eval story")
        criteria = [
            item if isinstance(item, AcceptanceCriterion) else AcceptanceCriterion.from_dict(item)
            for item in payload.get("acceptance_criteria", [])
        ]
        story_id = payload.get("story_id") or payload.get("change_id")
        return cls(
            story_id=story_id,
            title=payload["title"],
            description=payload["description"],
            acceptance_criteria=criteria,
            change_id=payload.get("change_id"),
            suite_tier=payload.get("suite_tier"),
            dataset_id=payload.get("dataset_id"),
            metadata=payload.get("metadata", {}),
        )


@dataclass(frozen=True)
class SuiteManifest:
    suite_id: str
    suite_tier: SuiteTier
    dataset_id: str
    stories: Sequence[str]
    total_checks: int = 0
    dataset_lock_hash: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty_string(self.suite_id, "suite_id")
        _validate_member(self.suite_tier, VALID_SUITE_TIERS, "suite_tier")
        _require_non_empty_string(self.dataset_id, "dataset_id")
        if self.total_checks < 0:
            raise ValueError("total_checks must be non-negative")
        object.__setattr__(self, "stories", list(self.stories))
        object.__setattr__(self, "metadata", _mapping_copy(self.metadata, "metadata"))

    def to_dict(self) -> Dict[str, Any]:
        return cast(Dict[str, Any], _clean(self))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SuiteManifest":
        payload = _mapping_copy(data, "suite manifest")
        return cls(
            suite_id=payload["suite_id"],
            suite_tier=payload["suite_tier"],
            dataset_id=payload["dataset_id"],
            stories=payload.get("stories", []),
            total_checks=int(payload.get("total_checks", 0)),
            dataset_lock_hash=payload.get("dataset_lock_hash"),
            metadata=payload.get("metadata", {}),
        )
