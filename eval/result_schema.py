from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class TestSummary:
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    cases: list[dict[str, Any]] = field(default_factory=list)
    ac_results: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class EvalMetrics:
    wall_seconds: float
    llm_session_seconds: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_total: int = 0
    cost_usd: float = 0.0
    agent_sessions: int = 0
    by_agent: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class BenchmarkResult:
    name: str
    run_id: str
    status: str
    error: str
    score_weighted: float
    trial_index: int
    project_tests: TestSummary | None
    hidden_tests: TestSummary | None
    quality: dict[str, Any]
    metrics: EvalMetrics
    story: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
