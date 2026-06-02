#!/usr/bin/env python3
"""Focused single-agent eval runner for prompt and context optimization.

The pilot target is ``task-generator``. Each case writes a minimal
``agent-context/<change-id>`` tree, invokes only the target agent unless
``--dry-run`` is set, scores the resulting artifact, and writes a comparable
JSON report.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agent_prompts import PROMPT_OVERRIDES_ENV, prompt_override_env_name
from core.runtime_paths import (
    agent_context_root,
    eval_agent_reports_root,
    eval_data_root,
    load_data_dir_override_from_env_file,
)

load_data_dir_override_from_env_file(ROOT / ".env")
from eval.agent_metrics import (
    AgentEvalCase,
    acceptance_criteria_map,
    aggregate_scores,
    build_task_generator_user_prompt,
    classify_agent_trend,
    dump_json,
    load_json_report,
    read_jsonl,
    sanitize_id,
    score_task_plan_file,
    sha256_file,
    sha256_text,
)
from eval.context_packs import ContextPack, load_context_pack as _load_context_pack
from eval.opik_reporting import build_agent_eval_tracer, flush_tracer, maybe_trace, safe_update_current_trace

logger = logging.getLogger(__name__)

SUPPORTED_AGENTS = ("task-generator",)
DEFAULT_AGENT = "task-generator"
DEFAULT_CONTEXT_PACK = "baseline"
DEFAULT_DATASET = eval_data_root() / DEFAULT_AGENT / "smoke.jsonl"
DEFAULT_REPORTS = eval_agent_reports_root()
DEFAULT_AGENT_CONTEXT = agent_context_root()
LEGACY_DATASET = ROOT / "eval" / "agent_datasets" / DEFAULT_AGENT / "smoke.jsonl"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextlib.contextmanager
def patched_environ(updates: Mapping[str, str | None]) -> Iterator[None]:
    old_values = {key: os.environ.get(key) for key in updates}
    try:
        for key, value in updates.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, old_value in old_values.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value


@contextlib.contextmanager
def patched_agent_context_root(path: Path) -> Iterator[None]:
    """Point existing workflow helpers at the eval artifact root."""
    patched: list[tuple[Any, str, Any]] = []
    try:
        from core import run_cmds, steps

        for obj, attr in ((steps, "AGENT_CONTEXT_ROOT"), (run_cmds, "_OPENAI_COMPAT_AGENT_CONTEXT_ROOT")):
            if hasattr(obj, attr):
                patched.append((obj, attr, getattr(obj, attr)))
                setattr(obj, attr, path)
        yield
    finally:
        for obj, attr, value in patched:
            setattr(obj, attr, value)


@contextlib.contextmanager
def prompt_override_env(agent: str, prompt_path: Path | None) -> Iterator[dict[str, Any]]:
    if prompt_path is None:
        yield {"prompt_override_applied": False}
        return
    prompt_path = prompt_path.expanduser().resolve()
    if not prompt_path.is_file():
        raise FileNotFoundError(f"prompt path not found: {prompt_path}")
    mapping = json.dumps({agent: str(prompt_path)}, sort_keys=True)
    with patched_environ({prompt_override_env_name(agent): str(prompt_path), PROMPT_OVERRIDES_ENV: mapping}):
        yield {
            "prompt_override_applied": True,
            "prompt_override_path": str(prompt_path),
            "prompt_override_sha256": sha256_file(prompt_path),
        }


def _case_to_mapping(case: dict[str, Any] | AgentEvalCase) -> dict[str, Any]:
    if isinstance(case, AgentEvalCase):
        return case.to_mapping()
    if hasattr(case, "to_mapping") and callable(getattr(case, "to_mapping")):
        return dict(case.to_mapping())
    if hasattr(case, "to_dict") and callable(getattr(case, "to_dict")):
        return dict(case.to_dict())
    return dict(case)


def latest_agent_prompt_path(agent: str) -> Path | None:
    source = ROOT / "agent-definition-source" / agent
    if not source.is_dir():
        return None
    versions: list[tuple[int, Path]] = []
    for child in source.iterdir():
        if not child.is_dir() or not child.name.startswith("v"):
            continue
        try:
            version = int(child.name[1:])
        except ValueError:
            continue
        prompt = child / "prompt.md"
        if prompt.is_file():
            versions.append((version, prompt))
    return sorted(versions, key=lambda item: item[0])[-1][1] if versions else None


def resolve_prompt_path(agent: str, explicit: str | Path | None) -> Path | None:
    if explicit:
        return Path(explicit).expanduser().resolve()
    return latest_agent_prompt_path(agent)


def load_context_pack(agent: str, context_pack: str | Path | None = DEFAULT_CONTEXT_PACK) -> ContextPack | None:
    raw = str(context_pack or "").strip()
    if raw.lower() in {"", "none", "off", "false"}:
        return None
    return _load_context_pack(agent, raw)


def make_run_id(case_id: str, trial_index: int, variant_sha: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S%f")
    return f"agent-eval-{sanitize_id(case_id)}-t{trial_index}-{sanitize_id(variant_sha)[:8]}-{stamp}"


def normalize_case_for_run(case: dict[str, Any] | AgentEvalCase, change_id: str) -> dict[str, Any]:
    case_map = _case_to_mapping(case)
    run_case = dict(case_map)
    story = dict(run_case.get("story") or {})
    original_change_id = story.get("change_id") or run_case.get("id") or change_id
    story["change_id"] = change_id
    story.setdefault("title", str(run_case.get("id") or change_id))
    story.setdefault("description", "")
    story.setdefault("acceptance_criteria", [])
    raw_input = story.get("raw_input") if isinstance(story.get("raw_input"), dict) else {}
    raw_input = dict(raw_input)
    raw_input.setdefault("agent_eval_case_id", run_case.get("id"))
    raw_input.setdefault("original_change_id", original_change_id)
    story["raw_input"] = raw_input
    run_case["story"] = story
    run_case.setdefault("agent", DEFAULT_AGENT)
    run_case.setdefault("expected", {})
    run_case.setdefault("repo_context", {})
    return run_case


def _format_list(values: Any) -> str:
    if not isinstance(values, list) or not values:
        return "- none"
    return "\n".join(f"- {item}" for item in values if str(item).strip()) or "- none"


def render_repo_context(case: dict[str, Any] | AgentEvalCase) -> str:
    case_map = _case_to_mapping(case)
    repo_context = case_map.get("repo_context") if isinstance(case_map.get("repo_context"), dict) else {}
    if not repo_context:
        return ""
    lines = ["# Repo Context Hints", ""]
    if repo_context.get("files_hint"):
        lines.extend(["## Files Hint", _format_list(repo_context.get("files_hint")), ""])
    if repo_context.get("known_patterns"):
        lines.extend(["## Known Patterns", _format_list(repo_context.get("known_patterns")), ""])
    extra_keys = sorted(set(repo_context) - {"files_hint", "known_patterns"})
    for key in extra_keys:
        lines.extend([f"## {key}", yaml.safe_dump(repo_context[key], sort_keys=False).rstrip(), ""])
    return "\n".join(lines).strip() + "\n"


def write_case_inputs(
    case: dict[str, Any] | AgentEvalCase,
    *,
    change_id: str,
    artifact_root: Path,
    repo: Path,
    context_pack: ContextPack | None,
    context_pack_text: str = "",
) -> dict[str, Path]:
    run_case = normalize_case_for_run(case, change_id)
    change_dir = artifact_root / change_id
    intake_dir = change_dir / "intake"
    planning_dir = change_dir / "planning"
    eval_dir = change_dir / "eval"
    for directory in (intake_dir, planning_dir, eval_dir):
        directory.mkdir(parents=True, exist_ok=True)

    story_path = intake_dir / "story.yaml"
    constraints_path = intake_dir / "constraints.md"
    case_path = eval_dir / "case.json"
    context_path = intake_dir / "agent-eval-context.md"
    repo_context_path = intake_dir / "repo-context.md"
    tasks_path = planning_dir / "tasks.yaml"

    story_path.write_text(yaml.safe_dump(run_case["story"], sort_keys=False, allow_unicode=True), encoding="utf-8")
    constraints_path.write_text(str(run_case.get("constraints_md") or "").rstrip() + "\n", encoding="utf-8")
    dump_json(run_case, case_path)
    rendered_context = context_pack_text or str(getattr(context_pack, "rendered", "") or "")
    context_path.write_text(rendered_context, encoding="utf-8")
    repo_context_path.write_text(render_repo_context(run_case), encoding="utf-8")
    (eval_dir / "repo.txt").write_text(str(repo) + "\n", encoding="utf-8")
    return {
        "change_dir": change_dir,
        "intake": intake_dir,
        "planning": planning_dir,
        "eval": eval_dir,
        "story": story_path,
        "constraints": constraints_path,
        "case": case_path,
        "context_pack": context_path,
        "repo_context": repo_context_path,
        "tasks": tasks_path,
    }


def build_task_generator_prompt(
    *,
    case: dict[str, Any] | AgentEvalCase,
    change_id: str,
    paths: Mapping[str, Path],
    repo: Path,
    context_pack: ContextPack | None,
    context_pack_text: str = "",
) -> str:
    run_case = normalize_case_for_run(case, change_id)
    prompt = build_task_generator_user_prompt(
        run_case,
        agent_context_root=paths["change_dir"].parent,
        context_pack=context_pack,
        context_pack_text=context_pack_text,
        repo=repo,
    )
    return prompt


def dry_run_task_plan(case: dict[str, Any] | AgentEvalCase, *, change_id: str) -> dict[str, Any]:
    case_map = _case_to_mapping(case)
    story = case_map.get("story") if isinstance(case_map.get("story"), dict) else {}
    expected = case_map.get("expected") if isinstance(case_map.get("expected"), dict) else {}
    ac_map = acceptance_criteria_map(story, expected)
    ac_ids = list(ac_map) or ["AC1"]
    min_tasks = int(expected.get("min_tasks") or 2)
    max_tasks = int(expected.get("max_tasks") or max(min_tasks, 2))
    task_count = min(max(min_tasks, 2), max_tasks)

    tasks: list[dict[str, Any]] = []
    if task_count <= 1:
        tasks.append(
            {
                "id": "T1",
                "title": "Implement and verify acceptance behavior",
                "description": "Implement the requested behavior and add tests covering " + ", ".join(ac_ids) + ".",
                "ac_mapping": ac_ids,
                "dependencies": [],
                "priority": "high",
                "complexity": "moderate",
                "definition_of_done": ["Implementation is complete.", "Automated tests or verification evidence pass."],
            }
        )
    else:
        midpoint = max(1, len(ac_ids) - 1)
        implementation_acs = ac_ids[:midpoint] or ac_ids
        tasks.append(
            {
                "id": "T1",
                "title": "Implement scoped acceptance behavior",
                "description": "Implement the local behavior for " + ", ".join(implementation_acs) + " without broad changes.",
                "ac_mapping": implementation_acs,
                "dependencies": [],
                "priority": "high",
                "complexity": "moderate",
                "definition_of_done": ["Implementation remains scoped to the requested behavior."],
            }
        )
        tasks.append(
            {
                "id": "T2",
                "title": "Verify behavior with tests",
                "description": "Add or update tests and validation for " + ", ".join(ac_ids) + ".",
                "ac_mapping": ac_ids,
                "dependencies": ["T1"],
                "priority": "high",
                "complexity": "simple",
                "definition_of_done": ["Relevant automated tests pass.", "Acceptance criteria are verified."],
            }
        )
    for index in range(len(tasks) + 1, task_count + 1):
        tasks.append(
            {
                "id": f"T{index}",
                "title": f"Refine implementation slice {index}",
                "description": "Keep the change minimal and aligned with the acceptance criteria.",
                "ac_mapping": ac_ids,
                "dependencies": [f"T{index - 1}"],
                "priority": "medium",
                "complexity": "simple",
                "definition_of_done": ["Only requested scope is introduced."],
            }
        )
    selected = tasks[:task_count]
    return {
        "story_id": change_id,
        "tasks": selected,
        "ac_coverage_matrix": {ac_id: [task["id"] for task in selected if ac_id in task.get("ac_mapping", [])] for ac_id in ac_ids},
    }


def collect_event_metrics(start: float) -> dict[str, Any]:
    return {"wall_seconds": round(time.monotonic() - start, 6), "tokens_total": 0, "cost_usd": 0.0}


def first_error(score: Mapping[str, Any], execution_error: str = "") -> str:
    if execution_error:
        return execution_error
    if score.get("status") == "PASS" or score.get("pass") is True:
        return ""
    for issue in score.get("issues") or []:
        return str(issue)
    for error in score.get("errors") or []:
        return str(error)
    return "score below pass threshold"


def _opik_enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").lower() in {"1", "true", "yes", "on", "auto"}


def run_case(
    *,
    case: dict[str, Any] | AgentEvalCase,
    trial_index: int,
    args: argparse.Namespace,
    variant: dict[str, Any],
    context_pack: ContextPack | None = None,
    context_pack_text: ContextPack | str | None = None,
) -> dict[str, Any]:
    agent = str(getattr(args, "agent", DEFAULT_AGENT))
    if agent not in SUPPORTED_AGENTS:
        raise ValueError(f"unsupported agent: {agent}")
    if context_pack is None and isinstance(context_pack_text, ContextPack):
        context_pack = context_pack_text
    inline_context_text = context_pack_text if isinstance(context_pack_text, str) else ""

    case_map = _case_to_mapping(case)
    case_id = str(case_map.get("id") or f"case-{trial_index}")
    change_id = make_run_id(case_id, trial_index, str(variant.get("variant_sha256") or "variant"))
    artifact_root = Path(getattr(args, "artifact_root", DEFAULT_AGENT_CONTEXT)).expanduser().resolve()
    artifact_root.mkdir(parents=True, exist_ok=True)
    repo = Path(getattr(args, "repo", ROOT) or ROOT).expanduser().resolve()
    run_case = normalize_case_for_run(case_map, change_id)
    paths = write_case_inputs(
        run_case,
        change_id=change_id,
        artifact_root=artifact_root,
        repo=repo,
        context_pack=context_pack,
        context_pack_text=inline_context_text,
    )
    prompt = build_task_generator_prompt(
        case=run_case,
        change_id=change_id,
        paths=paths,
        repo=repo,
        context_pack=context_pack,
        context_pack_text=inline_context_text,
    )

    event_log = paths["change_dir"] / "agent-eval-events.jsonl"
    start = time.monotonic()
    runner_output = ""
    execution_error = ""
    prompt_override_metadata: dict[str, Any] = {}
    tracer = build_agent_eval_tracer(
        enabled=_opik_enabled(getattr(args, "opik", False)),
        change_id=change_id,
        runner=str(getattr(args, "runner", "openai-compat")),
        model=getattr(args, "model", None),
    )
    trace_metadata = {
        "eval_type": "single_agent",
        "agent": agent,
        "case_id": case_id,
        "change_id": change_id,
        "trial_index": trial_index,
        "variant_sha256": variant.get("variant_sha256"),
        "context_pack_id": variant.get("context_pack_id"),
        "prompt_sha256": variant.get("prompt_sha256"),
    }
    try:
        with maybe_trace(tracer, f"agent-eval:{agent}", metadata=trace_metadata, input={"case_id": case_id, "prompt_sha256": sha256_text(prompt)}):
            with patched_environ(
                {
                    "AGENT_RUNNER_EVENT_LOG": str(event_log),
                    "AGENT_RUNNER_CHANGE_ID": change_id,
                    "AGENT_RUNNER_CURRENT_STAGE": f"agent-eval:{agent}",
                    "AGENT_RUNNER_EVALUATION_RUN": "1",
                    "AGENT_RUNNER_HEADLESS": "1",
                }
            ), patched_agent_context_root(artifact_root), prompt_override_env(
                agent,
                Path(getattr(args, "prompt_path", "")).expanduser().resolve() if getattr(args, "prompt_path", None) else None,
            ) as prompt_override_metadata:
                if getattr(args, "dry_run", False):
                    payload = dry_run_task_plan(run_case, change_id=change_id)
                    paths["tasks"].write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
                    runner_output = "dry-run task plan written"
                else:
                    from core.steps import step_task_gen_producer

                    runner_output = step_task_gen_producer(
                        prompt,
                        runner=getattr(args, "runner", "openai-compat"),
                        runner_model=getattr(args, "model", None),
                    )
    except Exception as exc:  # noqa: BLE001 - keep one bad case from hiding the rest
        execution_error = f"{type(exc).__name__}: {exc}"
        logger.exception("agent eval case failed: %s", case_id)

    raw_score = score_task_plan_file(paths["tasks"], run_case, pass_threshold=float(getattr(args, "pass_threshold", 0.8)))
    score = raw_score.to_dict() if hasattr(raw_score, "to_dict") else dict(raw_score)
    status = "FAIL" if execution_error else str(score.get("status") or "FAIL")
    result = {
        "case_id": case_id,
        "agent": agent,
        "run_id": change_id,
        "change_id": change_id,
        "trial_index": trial_index,
        "status": status,
        "pass": status == "PASS",
        "passed": status == "PASS",
        "error": first_error(score, execution_error=execution_error),
        "score_weighted": float(score.get("score_weighted") or score.get("weighted_score") or score.get("score") or 0.0),
        "scores": score.get("scores") or score.get("components") or {},
        "components": score.get("components") or score.get("scores") or {},
        "gates": score.get("gates") or {},
        "checks": score.get("checks") or {},
        "score_detail": score,
        "issues": score.get("issues") or [],
        "warnings": score.get("warnings") or [],
        "details": score.get("details") or {},
        "missing_ac_ids": score.get("missing_ac_ids") or [],
        "artifact_paths": {key: str(value) for key, value in paths.items()},
        "artifacts": {key: str(value) for key, value in paths.items()},
        "event_log": str(event_log),
        "runner_output_tail": str(runner_output or "")[-3000:],
        "variant": {key: value for key, value in variant.items() if key != "prompt_path_obj"},
        "prompt_override": prompt_override_metadata,
        "metrics": collect_event_metrics(start),
    }
    if execution_error:
        result["issues"] = [f"agent_error: {execution_error}", *result["issues"]]
    safe_update_current_trace(result=result, metadata=trace_metadata)
    flush_tracer(tracer)
    if not getattr(args, "keep_context", False) and not execution_error and result.get("status") == "PASS":
        shutil.rmtree(paths["change_dir"], ignore_errors=True)
    return result


def run_task_generator_case(
    *,
    case: dict[str, Any] | AgentEvalCase,
    args: argparse.Namespace,
    trial_index: int,
    variant: dict[str, Any],
    context_pack: ContextPack | str | None = None,
) -> dict[str, Any]:
    return run_case(
        case=case,
        trial_index=trial_index,
        args=args,
        variant=variant,
        context_pack=context_pack if isinstance(context_pack, ContextPack) else None,
        context_pack_text=context_pack if isinstance(context_pack, str) else None,
    )


def aggregate_results(results: list[dict[str, Any]], baseline: dict[str, Any] | None = None, *, quality_pp: float = 5.0) -> dict[str, Any]:
    summary = aggregate_scores(results)
    reliability = summary.get("reliability") if isinstance(summary.get("reliability"), dict) else {}
    quality = summary.get("quality") if isinstance(summary.get("quality"), dict) else {}
    summary.setdefault("pass_rate", reliability.get("pass_rate", 0.0))
    summary.setdefault("score_mean", quality.get("weighted_score", 0.0))
    summary.setdefault("case_count", reliability.get("runs", len(results)))
    summary.setdefault("runs", reliability.get("runs", len(results)))
    summary.setdefault("pass_count", reliability.get("passed", 0))
    summary.setdefault("fail_count", reliability.get("failed", 0))
    warnings: list[str] = []
    if len(results) < 3:
        warnings.append("Fewer than 3 runs; compare variants cautiously because stochastic variance is under-sampled.")
    if baseline:
        baseline_summary = baseline.get("summary") if isinstance(baseline.get("summary"), dict) else baseline
        summary["trend"] = classify_agent_trend(summary, baseline_summary, quality_pp=quality_pp)
    else:
        summary["trend"] = classify_agent_trend(summary, None, quality_pp=quality_pp)
    summary["warnings"] = warnings
    return summary


def build_variant(args: argparse.Namespace) -> tuple[dict[str, Any], ContextPack | None]:
    agent = str(getattr(args, "agent", DEFAULT_AGENT))
    prompt_path = resolve_prompt_path(agent, getattr(args, "prompt_path", None))
    context_pack = load_context_pack(agent, getattr(args, "context_pack", DEFAULT_CONTEXT_PACK))
    variant = {
        "agent": agent,
        "runner": getattr(args, "runner", None),
        "model": getattr(args, "model", None),
        "prompt_path": str(prompt_path) if prompt_path else None,
        "prompt_sha256": sha256_file(prompt_path),
        "context_pack_id": context_pack.id if context_pack else None,
        "context_pack_path": str(context_pack.path) if context_pack and context_pack.path else None,
        "context_pack_sha256": context_pack.sha256 if context_pack else None,
        "dataset": str(getattr(args, "dataset", "")),
        "dry_run": bool(getattr(args, "dry_run", False)),
    }
    variant["variant_sha256"] = sha256_text(json.dumps(variant, sort_keys=True, default=str))
    variant["prompt_path_obj"] = prompt_path
    return variant, context_pack


def _load_baseline(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    if not path.is_file():
        raise FileNotFoundError(f"baseline report not found: {path}")
    return load_json_report(path)


def build_report(results: list[dict[str, Any]], args: argparse.Namespace, variant: dict[str, Any], baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    summary = aggregate_results(results, baseline=baseline, quality_pp=float(getattr(args, "regression_quality_pp", 5.0)))
    return {
        "schema_version": "agent-eval-report.v1",
        "created_at": utc_now(),
        "agent": getattr(args, "agent", DEFAULT_AGENT),
        "dataset": str(getattr(args, "dataset", "")),
        "runs_per_case": int(getattr(args, "runs", 1)),
        "variant": {key: value for key, value in variant.items() if key != "prompt_path_obj"},
        "summary": summary,
        "results": results,
    }


def report_dir_for_args(args: argparse.Namespace) -> Path:
    base_report_dir = Path(getattr(args, "reports_dir", DEFAULT_REPORTS)).expanduser().resolve()
    agent = str(getattr(args, "agent", DEFAULT_AGENT))
    return base_report_dir if base_report_dir.name == agent else base_report_dir / agent


def write_report(results: list[dict[str, Any]], args: argparse.Namespace, variant: dict[str, Any], baseline: dict[str, Any] | None = None) -> Path | None:
    if not getattr(args, "write_report", True):
        return None
    report_dir = report_dir_for_args(args)
    report_dir.mkdir(parents=True, exist_ok=True)
    report = build_report(results, args, variant, baseline=baseline)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    report_path = report_dir / f"{stamp}.json"
    latest_path = report_dir / "latest.json"
    dump_json(report, report_path)
    dump_json(report, latest_path)
    if getattr(args, "update_baseline", False):
        dump_json(report, report_dir / "baseline.json")
    return latest_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", default=DEFAULT_AGENT, choices=SUPPORTED_AGENTS)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--runner", default=os.environ.get("AGENT_RUNNER_DEFAULT", "openai-compat"))
    parser.add_argument("--model", default=os.environ.get("AGENT_RUNNER_MODEL"))
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--pass-threshold", type=float, default=0.80)
    parser.add_argument("--context-pack", default=DEFAULT_CONTEXT_PACK)
    parser.add_argument("--prompt-path", type=Path)
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_AGENT_CONTEXT)
    parser.add_argument("--reports-dir", type=Path, default=DEFAULT_REPORTS)
    parser.add_argument("--dry-run", action="store_true", help="Write a deterministic artifact instead of invoking the agent.")
    parser.add_argument("--keep-context", action="store_true", help="Keep passing case work directories for inspection.")
    parser.add_argument("--materialize", action="store_true", help="Materialize agent assets before the run when supported.")
    parser.add_argument("--opik", nargs="?", const="true", default="false", help="Enable optional Opik trace/feedback logging.")
    parser.add_argument("--no-write-report", dest="write_report", action="store_false")
    parser.set_defaults(write_report=True)
    parser.add_argument("--compare-to", type=Path)
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--regression-quality-pp", type=float, default=5.0)
    parser.add_argument("--fail-under", type=float, help="Fail when summary quality.weighted_score is below this value.")
    parser.add_argument("--require-pass-rate", type=float, help="Fail when summary reliability.pass_rate is below this value.")
    parser.add_argument("--allow-failures", action="store_true", help="Always exit 0 after writing the report.")
    parser.add_argument("--log-level", default="info", choices=("debug", "info", "warning", "error"))
    return parser


def maybe_materialize(args: argparse.Namespace) -> None:
    if not getattr(args, "materialize", False):
        return
    try:
        from core.materialize import main as materialize_main

        materialize_main(["--agent", str(getattr(args, "agent", DEFAULT_AGENT))])
    except TypeError:
        logger.warning("core.materialize.main does not support direct argv; skipping materialize")
    except Exception as exc:  # noqa: BLE001
        logger.warning("materialize failed; continuing with current assets: %s: %s", type(exc).__name__, exc)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=getattr(logging, str(args.log_level).upper()), format="%(levelname)s %(name)s: %(message)s")
    if args.runs < 1:
        raise SystemExit("--runs must be >= 1")
    maybe_materialize(args)
    dataset_path = Path(args.dataset).expanduser()
    if dataset_path == DEFAULT_DATASET and not dataset_path.exists() and LEGACY_DATASET.exists():
        dataset_path = LEGACY_DATASET
    dataset_path = dataset_path.resolve()
    rows = read_jsonl(dataset_path)
    if not rows:
        raise SystemExit(f"dataset contains no cases: {dataset_path}")
    cases = [row for row in rows if str(row.get("agent") or DEFAULT_AGENT) == args.agent]
    if not cases:
        raise SystemExit(f"dataset contains no cases for agent {args.agent}: {dataset_path}")
    args.dataset = dataset_path
    variant, context_pack = build_variant(args)
    baseline = _load_baseline(args.compare_to.expanduser().resolve() if args.compare_to else None)

    results: list[dict[str, Any]] = []
    for trial in range(1, args.runs + 1):
        for case in cases:
            result = run_case(case=case, trial_index=trial, args=args, variant=variant, context_pack=context_pack)
            results.append(result)
            logger.info("%s trial=%s status=%s score=%.4f", result["case_id"], trial, result["status"], result["score_weighted"])

    report_path = write_report(results, args, variant, baseline=baseline)
    summary = aggregate_results(results, baseline=baseline, quality_pp=args.regression_quality_pp)
    if report_path:
        print(f"agent eval report: {report_path}")
    print(
        "summary: "
        f"pass_rate={summary['reliability']['pass_rate']:.4f} "
        f"weighted_score={summary['quality']['weighted_score']:.4f} "
        f"runs={summary['reliability']['runs']}"
    )

    failed = False
    if args.require_pass_rate is not None and summary["reliability"]["pass_rate"] < float(args.require_pass_rate):
        print(f"require-pass-rate failed: {summary['reliability']['pass_rate']:.4f} < {float(args.require_pass_rate):.4f}", file=sys.stderr)
        failed = True
    if args.fail_under is not None and summary["quality"]["weighted_score"] < float(args.fail_under):
        print(f"fail-under failed: {summary['quality']['weighted_score']:.4f} < {float(args.fail_under):.4f}", file=sys.stderr)
        failed = True
    if baseline and summary.get("trend", {}).get("status") == "regressed":
        print("baseline comparison regressed", file=sys.stderr)
        failed = True
    return 0 if args.allow_failures or not failed else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
