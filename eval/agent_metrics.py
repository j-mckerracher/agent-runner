"""Deterministic metrics for focused single-agent artifact evals.

The first supported agent is ``task-generator``. Its artifact is
``planning/tasks.yaml``. The scorer uses objective gates first: YAML/schema
validity, acceptance-criteria coverage, dependency sanity, task count, scope
control, and testability signals.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

TASK_GENERATOR_WEIGHTS: dict[str, float] = {
    "deterministic_gates": 0.40,
    "ac_coverage": 0.30,
    "dependency_and_granularity": 0.15,
    "scope_and_testability": 0.15,
}
REQUIRED_TASK_FIELDS = ("id", "title", "description", "ac_mapping", "dependencies", "priority", "complexity")
TEST_WORDS = (
    "test",
    "tests",
    "testing",
    "verify",
    "verification",
    "validate",
    "validation",
    "coverage",
    "assert",
    "evidence",
    "qa",
)


@dataclass(frozen=True)
class AgentEvalCase:
    id: str
    agent: str
    story: dict[str, Any]
    constraints_md: str = ""
    repo_context: dict[str, Any] | None = None
    expected: dict[str, Any] | None = None
    difficulty: str | None = None
    source: str | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "AgentEvalCase":
        story = payload.get("story")
        if not isinstance(story, Mapping):
            raise ValueError("dataset row must contain story mapping")
        case_id = str(payload.get("id") or story.get("change_id") or "").strip()
        if not case_id:
            raise ValueError("dataset row must contain id or story.change_id")
        expected = payload.get("expected") if isinstance(payload.get("expected"), Mapping) else {}
        repo_context = payload.get("repo_context") if isinstance(payload.get("repo_context"), Mapping) else {}
        return cls(
            id=case_id,
            agent=str(payload.get("agent") or "task-generator"),
            story=dict(story),
            constraints_md=str(payload.get("constraints_md") or ""),
            repo_context=dict(repo_context),
            expected=dict(expected),
            difficulty=str(payload.get("difficulty") or "") or None,
            source=str(payload.get("source") or "") or None,
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AgentEvalCase":
        return cls.from_mapping(payload)

    @property
    def change_id(self) -> str:
        return str(self.story.get("change_id") or self.id)

    def to_dataset_row(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "id": self.id,
            "agent": self.agent,
            "story": self.story,
            "constraints_md": self.constraints_md,
            "repo_context": self.repo_context or {},
            "expected": self.expected or {},
        }
        if self.difficulty:
            row["difficulty"] = self.difficulty
        if self.source:
            row["source"] = self.source
        return row

    def to_mapping(self) -> dict[str, Any]:
        return self.to_dataset_row()

    def to_dict(self) -> dict[str, Any]:
        return self.to_dataset_row()


@dataclass(frozen=True)
class ContextPack:
    id: str
    agent: str
    path: Path
    sha256: str
    description: str
    payload: dict[str, Any]
    rendered: str

    @classmethod
    def load(cls, path: str | Path) -> "ContextPack":
        from eval.context_packs import render_context_pack

        resolved = Path(path).expanduser().resolve()
        raw = resolved.read_bytes()
        payload = yaml.safe_load(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"context pack must be a YAML mapping: {resolved}")
        return cls(
            id=str(payload.get("id") or resolved.stem),
            agent=str(payload.get("agent") or "task-generator"),
            path=resolved,
            sha256=hashlib.sha256(raw).hexdigest(),
            description=str(payload.get("description") or ""),
            payload=payload,
            rendered=render_context_pack(payload),
        )


@dataclass(frozen=True)
class TaskGeneratorScore:
    status: str
    passed: bool
    score_weighted: float
    scores: dict[str, float]
    gates: dict[str, bool]
    checks: dict[str, Any]
    issues: list[str]
    warnings: list[str]
    errors: list[str]
    details: dict[str, Any]
    issue_records: list[dict[str, str]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "pass": self.passed,
            "passed": self.passed,
            "score": round(self.score_weighted, 4),
            "score_weighted": round(self.score_weighted, 4),
            "weighted_score": round(self.score_weighted, 4),
            "scores": {key: round(float(value), 4) for key, value in self.scores.items()},
            "components": {key: round(float(value), 4) for key, value in self.scores.items()},
            "gates": dict(self.gates),
            "checks": dict(self.checks),
            "issues": list(self.issues),
            "warnings": list(self.warnings),
            "errors": list(self.errors or self.issues),
            "issue_records": [dict(item) for item in self.issue_records],
            "details": dict(self.details),
            "missing_ac_ids": list(self.details.get("missing_ac_ids") or []),
            "unknown_ac_ids": list(self.details.get("unknown_ac_ids") or []),
            "forbidden_scope_hits": list(self.details.get("forbidden_scope_hits") or []),
            "task_count": int(self.details.get("task_count") or 0),
        }


def stable_json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_text(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: str | Path | None) -> str | None:
    if path is None:
        return None
    resolved = Path(path)
    if not resolved.is_file():
        return None
    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sanitize_id(value: Any) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip("-._")
    return cleaned or "case"


def dump_json(first: Any, second: Any) -> None:
    """Write JSON. Accepts both (payload, path) and (path, payload)."""
    if isinstance(first, (str, Path)) and not isinstance(second, (str, Path)):
        path, payload = Path(first), second
    else:
        payload, path = first, Path(second)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def load_json_report(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    resolved = Path(path)
    if not resolved.is_file():
        return {}
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    resolved = Path(path)
    if not resolved.is_file():
        raise FileNotFoundError(f"dataset not found: {resolved}")
    rows: list[dict[str, Any]] = []
    for line_number, raw in enumerate(resolved.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{resolved}:{line_number}: invalid JSONL row: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{resolved}:{line_number}: dataset row must be a JSON object")
        rows.append(row)
    if not rows:
        raise ValueError(f"dataset contains no rows: {resolved}")
    return rows


def load_jsonl_cases(path: str | Path, *, agent: str | None = None) -> list[AgentEvalCase]:
    cases = [AgentEvalCase.from_mapping(row) for row in read_jsonl(path)]
    if agent:
        cases = [case for case in cases if case.agent == agent]
    return cases


def normalize_ac_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.match(r"^\s*AC\s*[-_#:]?\s*(\d+)\b", text, flags=re.IGNORECASE)
    if match:
        return f"AC{int(match.group(1))}"
    compact = re.sub(r"\s+", "", text).upper()
    match = re.match(r"^AC(\d+)$", compact)
    if match:
        return f"AC{int(match.group(1))}"
    return compact


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def acceptance_criteria_map(story: Mapping[str, Any], expected: Mapping[str, Any] | None = None) -> dict[str, str]:
    raw = story.get("acceptance_criteria") or story.get("acceptance_criteria_raw") or []
    mapped: dict[str, str] = {}
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            ac_id = normalize_ac_id(key)
            if ac_id:
                mapped[ac_id] = str(value or "").strip()
    elif isinstance(raw, list):
        for index, item in enumerate(raw, start=1):
            if isinstance(item, Mapping):
                ac_id = normalize_ac_id(item.get("id") or item.get("ac_id") or f"AC{index}")
                text = str(item.get("text") or item.get("description") or "").strip()
            else:
                raw_text = str(item or "").strip()
                match = re.match(r"^\s*(AC\s*[-_#:]?\s*\d+)\s*[:\-.)]?\s*(.*)$", raw_text, flags=re.IGNORECASE)
                ac_id = normalize_ac_id(match.group(1)) if match else f"AC{index}"
                text = match.group(2).strip() if match and match.group(2).strip() else raw_text
            if ac_id:
                mapped[ac_id] = text
    explicit = (expected or {}).get("required_ac_ids") if isinstance(expected, Mapping) else None
    if isinstance(explicit, list) and explicit:
        result: dict[str, str] = {}
        for item in explicit:
            ac_id = normalize_ac_id(item)
            if ac_id:
                result[ac_id] = mapped.get(ac_id, "")
        return result
    return mapped


def acceptance_criteria_ids(story: Mapping[str, Any], expected: Mapping[str, Any] | None = None) -> list[str]:
    return list(acceptance_criteria_map(story, expected).keys())


def build_task_generator_user_prompt(
    case: AgentEvalCase | Mapping[str, Any],
    *,
    agent_context_root: str | Path = "agent-context",
    context_pack: Any | None = None,
    context_pack_text: str = "",
    repo: str | Path | None = None,
) -> str:
    normalized = case if isinstance(case, AgentEvalCase) else AgentEvalCase.from_mapping(case)
    root = Path(agent_context_root)
    change_id = normalized.change_id
    ac_map = acceptance_criteria_map(normalized.story, normalized.expected)
    rendered = context_pack_text or str(getattr(context_pack, "rendered", "") or "")
    lines = [
        f"Generate a task plan for agent-eval case {normalized.id}.",
        f"Target repo: `{repo}`" if repo else "Target repo: not supplied",
        "",
        "Inputs:",
        f"- Story: `{root / change_id / 'intake' / 'story.yaml'}`",
        f"- Constraints: `{root / change_id / 'intake' / 'constraints.md'}`",
        "",
        "Required output:",
        f"- Write `{root / change_id / 'planning' / 'tasks.yaml'}`.",
        "- Do not write prose instead of the artifact.",
        "- Preserve acceptance-criteria IDs exactly (AC1, AC2, ...).",
        "",
        "Acceptance criteria:",
    ]
    lines.extend(f"- {ac_id}: {text}" if text else f"- {ac_id}" for ac_id, text in ac_map.items())
    if normalized.repo_context:
        lines.extend(["", "Repository context hints:", yaml.safe_dump(normalized.repo_context, sort_keys=False).rstrip()])
    if normalized.expected:
        lines.extend(["", "Expected artifact properties:", yaml.safe_dump(normalized.expected, sort_keys=False).rstrip()])
    if rendered.strip():
        lines.extend(["", "Additional eval context pack:", rendered.strip()])
    return "\n".join(lines).strip() + "\n"


def _load_yaml_mapping(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    if not path.is_file():
        return None, [f"artifact not found: {path}"]
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return None, [f"YAML parse error: {exc}"]
    if not isinstance(data, dict):
        return None, ["artifact must be a YAML mapping"]
    return data, []


def _task_text(task: Mapping[str, Any]) -> str:
    parts = [str(task.get("id") or ""), str(task.get("title") or ""), str(task.get("description") or "")]
    dod = task.get("definition_of_done")
    if isinstance(dod, list):
        parts.extend(str(item) for item in dod)
    return " ".join(parts).lower()


def _task_ac_ids(task: Mapping[str, Any]) -> list[str]:
    raw = task.get("ac_mapping") if "ac_mapping" in task else task.get("acceptance_criteria_mapped")
    return [normalize_ac_id(item) for item in _as_list(raw) if normalize_ac_id(item)]


def _detect_cycle(graph: Mapping[str, Iterable[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for dep in graph.get(node, []):
            if dep in graph and visit(dep):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in graph)


def _expected_required_ac_ids(case: AgentEvalCase) -> list[str]:
    explicit = [normalize_ac_id(item) for item in _as_list((case.expected or {}).get("required_ac_ids"))]
    return [item for item in explicit if item] or acceptance_criteria_ids(case.story)


def _forbidden_scope_hits(tasks: Iterable[Mapping[str, Any]], forbidden_scope: Iterable[Any]) -> list[str]:
    patterns = [str(item).strip().lower() for item in forbidden_scope if str(item).strip()]
    if not patterns:
        return []
    joined = "\n".join(_task_text(task) for task in tasks)
    hits: list[str] = []
    for pattern in patterns:
        negated = any(f"{prefix} {pattern}" in joined for prefix in ("without", "no", "avoid", "avoiding"))
        if pattern in joined and not negated:
            hits.append(pattern)
    return hits


def _issue(code: str, message: str, *, severity: str = "error") -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message}


def _public_issue(issue: Mapping[str, str]) -> str:
    code = str(issue.get("code") or "issue")
    message = str(issue.get("message") or "").strip()
    aliases = {
        "missing_ac_coverage": "ac_coverage_missing: missing AC coverage",
        "unknown_ac_ids": "ac_coverage_unknown: AC ids not in the eval case",
        "forbidden_scope": "scope_violation: forbidden scope detected",
        "dependency_cycle": "dependency_cycle",
        "unknown_dependencies": "dependency_unknown",
        "task_count_out_of_range": "task_count_out_of_range",
        "missing_test_task": "testability_missing",
        "duplicate_task_ids": "task_ids_duplicate",
        "malformed_task_ids": "task_ids_malformed",
        "missing_task_fields": "schema_invalid",
        "tasks_not_list": "schema_invalid",
    }
    prefix = aliases.get(code, code)
    return f"{prefix}: {message}" if message else prefix


def score_task_plan(*args: Any, **kwargs: Any) -> TaskGeneratorScore:
    if "task_plan" in kwargs or "story" in kwargs:
        plan = kwargs.pop("task_plan", None)
        story = kwargs.pop("story", {}) or {}
        expected = kwargs.pop("expected", {}) or {}
        case = {
            "id": kwargs.pop("case_id", None) or str(story.get("change_id") or "case"),
            "agent": kwargs.pop("agent", "task-generator"),
            "story": dict(story),
            "expected": dict(expected),
        }
    else:
        plan = args[0] if args else None
        case = args[1] if len(args) > 1 else kwargs.pop("case")
    artifact_path = kwargs.pop("artifact_path", None)
    pass_threshold = float(kwargs.pop("pass_threshold", 0.80))
    yaml_errors = list(kwargs.pop("yaml_errors", []) or [])
    normalized = case if isinstance(case, AgentEvalCase) else AgentEvalCase.from_mapping(case)
    expected = normalized.expected or {}
    raw_issues: list[dict[str, str]] = []
    errors = list(yaml_errors)
    details: dict[str, Any] = {
        "case_id": normalized.id,
        "change_id": normalized.change_id,
        "artifact_path": str(artifact_path or ""),
        "weights": TASK_GENERATOR_WEIGHTS,
    }

    if not isinstance(plan, Mapping):
        gates = _alias_gates(
            artifact_present=False,
            schema_valid=False,
            ac_coverage_complete=False,
            dependencies_valid=False,
            dependency_graph_acyclic=False,
            task_count_in_range=False,
            scope_controlled=False,
            testability_present=False,
            task_ids_unique=False,
        )
        scores = {key: 0.0 for key in TASK_GENERATOR_WEIGHTS}
        raw_issues.append(_issue("invalid_artifact", "task plan is missing or is not a YAML mapping"))
        return _score_result("ERROR" if errors else "FAIL", False, 0.0, scores, gates, {}, raw_issues, errors, details)

    tasks_raw = plan.get("tasks")
    tasks = tasks_raw if isinstance(tasks_raw, list) else []
    task_maps = [task for task in tasks if isinstance(task, Mapping)]
    schema_valid = bool(tasks) and len(tasks) == len(task_maps) and "story_id" in plan and "ac_coverage_matrix" in plan
    if not isinstance(tasks_raw, list):
        raw_issues.append(_issue("tasks_not_list", "top-level tasks must be a list"))
        schema_valid = False

    task_ids: list[str] = []
    duplicate_ids: list[str] = []
    tasks_missing_fields: list[dict[str, Any]] = []
    malformed_ids: list[str] = []
    for index, task in enumerate(task_maps, start=1):
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            malformed_ids.append(f"<task-{index}>")
        if task_id and task_id in task_ids:
            duplicate_ids.append(task_id)
        if task_id:
            task_ids.append(task_id)
        missing = [field for field in REQUIRED_TASK_FIELDS if field not in task]
        if missing:
            tasks_missing_fields.append({"task_id": task_id or f"task-{index}", "missing": missing})
    if duplicate_ids or malformed_ids or tasks_missing_fields:
        schema_valid = False

    required_ac_ids = _expected_required_ac_ids(normalized)
    covered_ac_ids: list[str] = []
    unknown_ac_ids: list[str] = []
    for task in task_maps:
        for ac_id in _task_ac_ids(task):
            if ac_id not in covered_ac_ids:
                covered_ac_ids.append(ac_id)
            if ac_id not in required_ac_ids and ac_id not in unknown_ac_ids:
                unknown_ac_ids.append(ac_id)
    matrix = plan.get("ac_coverage_matrix")
    if isinstance(matrix, Mapping):
        for ac_id, mapped_tasks in matrix.items():
            norm = normalize_ac_id(ac_id)
            if norm and mapped_tasks and norm not in covered_ac_ids:
                covered_ac_ids.append(norm)
            if norm and norm not in required_ac_ids and norm not in unknown_ac_ids:
                unknown_ac_ids.append(norm)
    missing_ac_ids = [ac_id for ac_id in required_ac_ids if ac_id not in covered_ac_ids]

    dependency_graph: dict[str, list[str]] = {}
    unknown_dependencies: list[dict[str, str]] = []
    for task in task_maps:
        task_id = str(task.get("id") or "").strip()
        deps = [str(dep).strip() for dep in _as_list(task.get("dependencies")) if str(dep).strip()]
        if task_id:
            dependency_graph[task_id] = deps
        for dep in deps:
            if dep not in task_ids:
                unknown_dependencies.append({"task_id": task_id, "dependency": dep})
    has_cycle = _detect_cycle(dependency_graph)
    dependencies_valid = not unknown_dependencies and not has_cycle

    min_tasks = int(expected.get("min_tasks") or 1)
    max_tasks = int(expected.get("max_tasks") or 20)
    task_count = len(task_maps)
    task_count_in_range = min_tasks <= task_count <= max_tasks
    forbidden_hits = _forbidden_scope_hits(task_maps, _as_list(expected.get("forbidden_scope")))
    requires_test_task = bool(expected.get("must_include_test_task", False))
    test_tasks = [task for task in task_maps if any(word in _task_text(task) for word in TEST_WORDS)]
    testability_present = bool(test_tasks) or not requires_test_task
    ac_coverage_complete = bool(required_ac_ids) and not missing_ac_ids
    scope_controlled = not forbidden_hits
    task_ids_unique = bool(task_ids) and not duplicate_ids and not malformed_ids

    if duplicate_ids:
        raw_issues.append(_issue("duplicate_task_ids", ", ".join(sorted(set(duplicate_ids)))))
    if malformed_ids:
        raw_issues.append(_issue("malformed_task_ids", ", ".join(malformed_ids)))
    if tasks_missing_fields:
        raw_issues.append(_issue("missing_task_fields", stable_json_dumps(tasks_missing_fields)))
    if missing_ac_ids:
        raw_issues.append(_issue("missing_ac_coverage", ", ".join(missing_ac_ids)))
    if unknown_ac_ids:
        raw_issues.append(_issue("unknown_ac_ids", ", ".join(unknown_ac_ids), severity="warning"))
    if unknown_dependencies:
        raw_issues.append(_issue("unknown_dependencies", stable_json_dumps(unknown_dependencies)))
    if has_cycle:
        raw_issues.append(_issue("dependency_cycle", "dependency graph contains a cycle"))
    if not task_count_in_range:
        raw_issues.append(_issue("task_count_out_of_range", f"got {task_count}, expected {min_tasks}-{max_tasks}", severity="warning"))
    if forbidden_hits:
        raw_issues.append(_issue("forbidden_scope", ", ".join(forbidden_hits)))
    if not testability_present:
        raw_issues.append(_issue("missing_test_task", "expected at least one test or verification task", severity="warning"))

    gates = _alias_gates(
        artifact_present=True,
        schema_valid=schema_valid,
        ac_coverage_complete=ac_coverage_complete,
        dependencies_valid=dependencies_valid,
        dependency_graph_acyclic=not has_cycle,
        task_count_in_range=task_count_in_range,
        scope_controlled=scope_controlled,
        testability_present=testability_present,
        task_ids_unique=task_ids_unique,
    )
    deterministic = _fraction(
        [
            gates["artifact_present"],
            gates["schema_valid"],
            gates["dependencies_valid"],
            gates["dependency_graph_acyclic"],
            gates["task_count_in_range"],
            gates["task_ids_unique"],
        ]
    )
    ac_score = 0.0 if not required_ac_ids else (len(required_ac_ids) - len(missing_ac_ids)) / len(required_ac_ids)
    dependency_and_granularity = _fraction([dependencies_valid, not has_cycle, task_count_in_range])
    scope_and_testability = _fraction([scope_controlled, testability_present])
    scores = {
        "deterministic_gates": deterministic,
        "ac_coverage": ac_score,
        "dependency_and_granularity": dependency_and_granularity,
        "scope_and_testability": scope_and_testability,
    }
    weighted = sum(scores[key] * TASK_GENERATOR_WEIGHTS[key] for key in TASK_GENERATOR_WEIGHTS)
    hard_gate_pass = all(
        gates[key]
        for key in (
            "artifact_present",
            "schema_valid",
            "ac_coverage_complete",
            "dependencies_valid",
            "dependency_graph_acyclic",
            "scope_controlled",
            "task_ids_unique",
        )
    )
    blocking_issues = [issue for issue in raw_issues if issue.get("severity") != "warning"]
    passed = hard_gate_pass and weighted >= pass_threshold and not blocking_issues
    status = "PASS" if passed else ("ERROR" if errors else "FAIL")
    checks = {
        "task_count": task_count,
        "min_tasks": min_tasks,
        "max_tasks": max_tasks,
        "required_ac_ids": required_ac_ids,
        "covered_ac_ids": covered_ac_ids,
        "unknown_ac_ids": unknown_ac_ids,
        "missing_ac_ids": missing_ac_ids,
        "unknown_dependencies": unknown_dependencies,
        "dependency_cycle": has_cycle,
        "forbidden_scope_hits": forbidden_hits,
        "test_task_count": len(test_tasks),
        **gates,
    }
    details.update(
        {
            "task_ids": task_ids,
            "missing_ac_ids": missing_ac_ids,
            "covered_ac_ids": covered_ac_ids,
            "unknown_ac_ids": unknown_ac_ids,
            "forbidden_scope_hits": forbidden_hits,
            "task_count": task_count,
            "pass_threshold": pass_threshold,
        }
    )
    return _score_result(status, passed, weighted, scores, gates, checks, raw_issues, errors, details)


def score_task_plan_file(*args: Any, **kwargs: Any) -> TaskGeneratorScore:
    if "tasks_path" in kwargs:
        artifact_path = kwargs.pop("tasks_path")
    elif args:
        artifact_path = args[0]
        args = args[1:]
    else:
        artifact_path = kwargs.pop("artifact_path")
    if "case" in kwargs:
        case = kwargs.pop("case")
    elif args:
        case = args[0]
    else:
        story = kwargs.pop("story", {}) or {}
        expected = kwargs.pop("expected", {}) or {}
        case = {
            "id": kwargs.pop("case_id", None) or str(story.get("change_id") or "case"),
            "agent": kwargs.pop("agent", "task-generator"),
            "story": dict(story),
            "expected": dict(expected),
        }
    pass_threshold = float(kwargs.pop("pass_threshold", kwargs.pop("threshold", 0.80)))
    path = Path(artifact_path)
    plan, yaml_errors = _load_yaml_mapping(path)
    return score_task_plan(plan, case, artifact_path=path, pass_threshold=pass_threshold, yaml_errors=yaml_errors)


def score_task_generator_case(*args: Any, **kwargs: Any) -> TaskGeneratorScore:
    if len(args) >= 2 and isinstance(args[0], (Mapping, AgentEvalCase)):
        case, artifact_path = args[0], args[1]
        return score_task_plan_file(artifact_path, case, **kwargs)
    if len(args) >= 2:
        artifact_path, case = args[0], args[1]
        return score_task_plan_file(artifact_path, case, **kwargs)
    return score_task_plan_file(kwargs.pop("tasks_path"), kwargs.pop("case"), **kwargs)


def score_task_generator_artifact(artifact_path: str | Path, case: AgentEvalCase | Mapping[str, Any], *, pass_threshold: float = 0.80) -> dict[str, Any]:
    return score_task_plan_file(artifact_path, case, pass_threshold=pass_threshold).to_dict()


def score_agent_artifact(agent: str, artifact_path: str | Path, case: AgentEvalCase | Mapping[str, Any], *, pass_threshold: float = 0.80) -> dict[str, Any]:
    if agent != "task-generator":
        raise NotImplementedError(f"single-agent eval scoring is not implemented for {agent!r}")
    return score_task_generator_artifact(artifact_path, case, pass_threshold=pass_threshold)


def summarize_agent_eval_results(results: Iterable[Mapping[str, Any]], baseline: Mapping[str, Any] | None = None) -> dict[str, Any]:
    rows = list(results)
    total = len(rows)
    scores = [float(row.get("score_weighted") or row.get("score") or 0.0) for row in rows]
    pass_count = sum(1 for row in rows if _result_passed(row))
    gate_counts: dict[str, int] = {}
    gate_totals: dict[str, int] = {}
    failure_categories: dict[str, int] = {}
    failed_checks: dict[str, int] = {}
    wall_seconds = []
    tokens_total = []
    cost_usd = []
    for row in rows:
        metrics = row.get("metrics") if isinstance(row.get("metrics"), Mapping) else {}
        wall_seconds.append(float(metrics.get("wall_seconds") or 0.0))
        tokens_total.append(float(metrics.get("tokens_total") or 0.0))
        cost_usd.append(float(metrics.get("cost_usd") or 0.0))
        gates = row.get("gates") if isinstance(row.get("gates"), Mapping) else {}
        for key, value in gates.items():
            gate_totals[key] = gate_totals.get(key, 0) + 1
            if bool(value):
                gate_counts[key] = gate_counts.get(key, 0) + 1
            elif not _result_passed(row):
                failed_checks[key] = failed_checks.get(key, 0) + 1
                failure_categories[f"gate:{key}"] = failure_categories.get(f"gate:{key}", 0) + 1
        for item in list(row.get("issues") or []) + list(row.get("errors") or []):
            category = _message_category(item)
            failure_categories[category] = failure_categories.get(category, 0) + 1
    gate_pass_rates = {key: round(gate_counts.get(key, 0) / total_count, 4) for key, total_count in sorted(gate_totals.items()) if total_count}
    summary = {
        "case_count": total,
        "runs": total,
        "pass_count": pass_count,
        "passed": pass_count,
        "fail_count": total - pass_count,
        "failed": total - pass_count,
        "pass_rate": round(pass_count / total, 4) if total else 0.0,
        "score_mean": _mean(scores),
        "score_min": round(min(scores), 4) if scores else 0.0,
        "score_max": round(max(scores), 4) if scores else 0.0,
        "gate_pass_rates": gate_pass_rates,
        "failure_categories": dict(sorted(failure_categories.items())),
        "failed_checks": dict(sorted(failed_checks.items())),
        "wall_seconds_mean": _mean(wall_seconds),
        "tokens_total_mean": _mean(tokens_total),
        "cost_usd_mean": _mean(cost_usd),
    }
    if baseline:
        base = baseline.get("summary") if isinstance(baseline.get("summary"), Mapping) else baseline
        if isinstance(base, Mapping):
            summary["baseline_delta"] = {
                "score_mean": round(summary["score_mean"] - _summary_score(base), 4),
                "pass_rate": round(summary["pass_rate"] - _summary_pass_rate(base), 4),
            }
    return summary


def aggregate_scores(results: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    flat = summarize_agent_eval_results(results)
    quality = {
        "weighted_score": flat["score_mean"],
        "score_min": flat["score_min"],
        "score_max": flat["score_max"],
        "gate_pass_rates": flat["gate_pass_rates"],
    }
    reliability = {
        "runs": flat["runs"],
        "passed": flat["passed"],
        "failed": flat["failed"],
        "pass_rate": flat["pass_rate"],
        "failure_categories": flat["failure_categories"],
        "failed_checks": flat["failed_checks"],
    }
    efficiency = {
        "wall_seconds_mean": flat["wall_seconds_mean"],
        "tokens_total_mean": flat["tokens_total_mean"],
        "cost_usd_mean": flat["cost_usd_mean"],
    }
    return {
        "quality": quality,
        "reliability": reliability,
        "efficiency": efficiency,
        "warnings": [],
        # Flat compatibility aliases used by older report consumers/tests.
        "case_count": flat["case_count"],
        "runs": flat["runs"],
        "pass_count": flat["pass_count"],
        "passed": flat["passed"],
        "fail_count": flat["fail_count"],
        "failed": flat["failed"],
        "pass_rate": flat["pass_rate"],
        "score_mean": flat["score_mean"],
        "score_min": flat["score_min"],
        "score_max": flat["score_max"],
        "gate_pass_rates": flat["gate_pass_rates"],
        "failure_categories": flat["failure_categories"],
        "failed_checks": flat["failed_checks"],
        "wall_seconds_mean": flat["wall_seconds_mean"],
        "tokens_total_mean": flat["tokens_total_mean"],
        "cost_usd_mean": flat["cost_usd_mean"],
    }


def classify_agent_trend(current: Mapping[str, Any], baseline: Mapping[str, Any] | None = None, *, quality_pp: float = 5.0) -> dict[str, Any]:
    if not baseline:
        return {"status": "no_baseline", "classification": "no-baseline", "score_delta": None, "quality_delta": None, "pass_rate_delta": None}
    current_summary = current.get("summary") if isinstance(current.get("summary"), Mapping) else current
    baseline_summary = baseline.get("summary") if isinstance(baseline.get("summary"), Mapping) else baseline
    score_delta = round(_summary_score(current_summary or {}) - _summary_score(baseline_summary or {}), 4)
    pass_rate_delta = round(_summary_pass_rate(current_summary or {}) - _summary_pass_rate(baseline_summary or {}), 4)
    threshold = quality_pp / 100.0
    if score_delta <= -threshold or pass_rate_delta < -0.001:
        classification = "decreased"
        status = "regressed"
    elif score_delta >= threshold or pass_rate_delta > 0.001:
        classification = "increased"
        status = "improved"
    else:
        classification = "same"
        status = "unchanged"
    return {"status": status, "classification": classification, "score_delta": score_delta, "quality_delta": score_delta, "pass_rate_delta": pass_rate_delta}


def _alias_gates(
    *,
    artifact_present: bool,
    schema_valid: bool,
    ac_coverage_complete: bool,
    dependencies_valid: bool,
    dependency_graph_acyclic: bool,
    task_count_in_range: bool,
    scope_controlled: bool,
    testability_present: bool,
    task_ids_unique: bool,
) -> dict[str, bool]:
    return {
        "artifact_present": artifact_present,
        "schema_valid": schema_valid,
        "ac_coverage_complete": ac_coverage_complete,
        "dependencies_valid": dependencies_valid,
        "task_count_in_range": task_count_in_range,
        "scope_controlled": scope_controlled,
        "testability_present": testability_present,
        "file_exists": artifact_present,
        "valid_yaml_mapping": artifact_present,
        "schema_fields_present": schema_valid,
        "task_ids_unique": task_ids_unique,
        "dependencies_known": dependencies_valid,
        "dependency_graph_acyclic": dependency_graph_acyclic,
        "required_ac_ids_covered": ac_coverage_complete,
        "no_forbidden_scope": scope_controlled,
        "test_or_verification_task_present": testability_present,
    }


def _score_result(
    status: str,
    passed: bool,
    weighted: float,
    scores: Mapping[str, float],
    gates: Mapping[str, bool],
    checks: Mapping[str, Any],
    raw_issues: list[dict[str, str]],
    errors: list[str],
    details: Mapping[str, Any],
) -> TaskGeneratorScore:
    issues = [_public_issue(issue) for issue in raw_issues if issue.get("severity") != "warning"]
    warnings = [_public_issue(issue) for issue in raw_issues if issue.get("severity") == "warning"]
    return TaskGeneratorScore(
        status=status,
        passed=passed,
        score_weighted=weighted,
        scores=dict(scores),
        gates=dict(gates),
        checks=dict(checks),
        issues=issues,
        warnings=warnings,
        errors=list(errors) or issues,
        details=dict(details),
        issue_records=[dict(issue) for issue in raw_issues],
    )


def _fraction(values: Iterable[bool]) -> float:
    vals = list(values)
    return sum(1 for item in vals if item) / len(vals) if vals else 0.0


def _mean(values: Iterable[float]) -> float:
    vals = list(values)
    return round(sum(vals) / len(vals), 4) if vals else 0.0


def _result_passed(row: Mapping[str, Any]) -> bool:
    if "pass" in row:
        return bool(row.get("pass"))
    if "passed" in row:
        return bool(row.get("passed"))
    return str(row.get("status") or "").upper() == "PASS"


def _message_category(item: Any) -> str:
    if isinstance(item, Mapping):
        return str(item.get("code") or item.get("category") or "unknown")
    return str(item).split(":", 1)[0] or "unknown"


def _summary_score(summary: Mapping[str, Any]) -> float:
    if "score_mean" in summary:
        return float(summary.get("score_mean") or 0.0)
    quality = summary.get("quality") if isinstance(summary.get("quality"), Mapping) else {}
    return float(quality.get("weighted_score") or 0.0)


def _summary_pass_rate(summary: Mapping[str, Any]) -> float:
    if "pass_rate" in summary:
        return float(summary.get("pass_rate") or 0.0)
    reliability = summary.get("reliability") if isinstance(summary.get("reliability"), Mapping) else {}
    return float(reliability.get("pass_rate") or 0.0)
