"""Synthesize repository-wide difficulty-tier eval suites from a locked dataset sample."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

if __package__ in {None, ""}:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from core.agent_cmd import run_agent_cmd, is_transient_runner_failure_text
    from core.runner_models import KNOWN_RUNNERS, resolve_runner_model
    from eval.dataset_manifest import load_dataset_lock, load_dataset_manifest, stable_manifest_hash
    from eval.models import AcceptanceCriterion, CheckDefinition, DatasetLock, DatasetManifest, EvalStory
    from eval.run_eval import StoryRun, TrialRunSpec, run_story_trials
    from eval.suite_io import dump_eval_story, workflow_fixture_from_story
    from eval.yaml_io import dump_yaml
else:
    from core.agent_cmd import run_agent_cmd, is_transient_runner_failure_text
    from core.runner_models import KNOWN_RUNNERS, resolve_runner_model
    from .dataset_manifest import load_dataset_lock, load_dataset_manifest, stable_manifest_hash
    from .models import AcceptanceCriterion, CheckDefinition, DatasetLock, DatasetManifest, EvalStory
    from .run_eval import StoryRun, TrialRunSpec, run_story_trials
    from .suite_io import dump_eval_story, workflow_fixture_from_story
    from .yaml_io import dump_yaml


VALID_TIERS = {"easy", "medium", "hard"}
VALID_MECHANISMS = {"contains", "matches", "command"}
COMMAND_CHECK_SUBJECT_FALLBACK = "repo"
DEFAULT_CALIBRATION_RUNNER_PROFILE = (
    "copilot-gemma4=2,"
    "copilot-deepseek-v4-flash=2,"
    "copilot-minimax-m2.7=2"
)
CALIBRATION_RUNS = 3
CALIBRATION_MAX_CONCURRENT = 2
CALIBRATION_MAX_ITERATIONS = 5
CALIBRATION_STORY_WORKERS = 2
CALIBRATION_FAST_MODE = True
CALIBRATION_METADATA_VERSION = 4
SYNTHESIS_METADATA_VERSION = 1
TIERING_METADATA_VERSION = 1
CALIBRATION_ROOT_PREFIX = "calibration_"
COMMON_REPO_SUBDIR_NAMES = frozenset({"app", "apps", "lib", "libs", "package", "packages", "services", "src"})
REPO_WIDE_STORY_SPECS: Tuple[Tuple[str, str], ...] = (
    ("story_001", "easy"),
    ("story_002", "medium"),
    ("story_003", "hard"),
)
PASS_RATE_BANDS: Mapping[str, Tuple[float, float]] = {
    "easy": (0.75, 1.0),
    "medium": (0.50, 0.74),
    "hard": (0.25, 0.49),
}
CALIBRATION_FAILURE_PREVIEW_LIMIT = 3
CALIBRATION_FAILURE_OUTPUT_LINE_LIMIT = 8
CALIBRATION_FAILURE_OUTPUT_CHAR_LIMIT = 1200


class SynthesisError(RuntimeError):
    """Raised when suite synthesis cannot proceed safely."""


def _legacy_calibration_runner_profile(*, runner: str, model: str, runs: int) -> list[Mapping[str, Any]]:
    return [{"runner": runner, "model": model, "count": runs}]


def parse_calibration_runner_profile(profile_text: str) -> list[Mapping[str, Any]]:
    entries: list[Mapping[str, Any]] = []
    for raw_item in profile_text.split(","):
        item = raw_item.strip()
        if not item:
            continue
        if "=" not in item:
            raise SynthesisError(
                "Invalid calibration runner profile entry "
                f"{item!r}. Expected comma-separated Copilot alias counts like "
                "'copilot-gemma4=2,copilot-deepseek-v4-flash=2'."
            )
        runner_text, count_text = item.rsplit("=", 1)
        runner_name = runner_text.strip()
        if not runner_name or runner_name in KNOWN_RUNNERS:
            raise SynthesisError(
                "Calibration runner profile entries must use custom runner aliases "
                f"(got {runner_name!r})."
            )
        try:
            count = int(count_text.strip())
        except ValueError as exc:
            raise SynthesisError(
                f"Invalid run count in calibration runner profile entry {item!r}."
            ) from exc
        if count <= 0:
            raise SynthesisError(
                f"Calibration runner profile count must be positive (got {count} for {runner_name!r})."
            )
        entries.append(
            {
                "runner": runner_name,
                "model": resolve_runner_model(runner_name),
                "count": count,
            }
        )
    if not entries:
        raise SynthesisError("Calibration runner profile must define at least one runner entry.")
    return entries


def _calibration_runner_profile_payload(profile: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [
        {
            "runner": str(entry["runner"]),
            "model": str(entry["model"]),
            "count": int(entry["count"]),
        }
        for entry in profile
    ]


def _expand_calibration_run_specs(profile: Sequence[Mapping[str, Any]]) -> list[TrialRunSpec]:
    run_specs: list[TrialRunSpec] = []
    run_index = 1
    for entry in profile:
        for _ in range(int(entry["count"])):
            run_specs.append(
                TrialRunSpec(
                    run_index=run_index,
                    runner=str(entry["runner"]),
                    model=str(entry["model"]),
                )
            )
            run_index += 1
    return run_specs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Synthesize repository-wide eval suites from a locked dataset sample.")
    parser.add_argument("--dataset", required=True, help="Path to eval/datasets/<dataset>.yaml")
    parser.add_argument("--output", default="eval/suites/", help="Suite output directory")
    parser.add_argument("--stories-output", default="eval/stories/", help="Workflow-compatible JSON output directory")
    parser.add_argument("--repo", help="Target repository root for calibration runs (optional; inferred from dataset source when omitted)")
    parser.add_argument("--runner", default="copilot", metavar="RUNNER",
                        help="LLM runner: 'claude', 'copilot', 'gemini', or a copilot alias like 'copilot-gemma4'")
    parser.add_argument("--model", default=None, help="Runner model override. Omit to use the runner's default (alias runners derive the model from the alias name).")
    parser.add_argument("--agent", default="task-generator", help="Agent to use for story synthesis")
    parser.add_argument(
        "--calibrate",
        action="store_true",
        help=(
            "Opt in to expensive empirical calibration workflow runs. When omitted, synthesize "
            "emits predicted tiers using deterministic heuristics and skips calibration."
        ),
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5,
        help="Legacy compatibility flag. Repository-wide synthesis ignores batching and emits one prompt for the whole sample.",
    )
    parser.add_argument(
        "--ac-hints",
        help="Optional hints JSON with flagged story guidance; only flagged existing raw stories are re-synthesized",
    )
    parser.add_argument(
        "--calibration-max-concurrent",
        type=int,
        default=CALIBRATION_MAX_CONCURRENT,
        help="Maximum concurrent workflow runs during calibration. Defaults to 2.",
    )
    parser.add_argument(
        "--calibration-runs",
        type=int,
        default=CALIBRATION_RUNS,
        help=(
            "Legacy single-runner workflow trial count used to score each calibration attempt. "
            "When --calibration-runner-profile is omitted and this value is changed, synthesize "
            "uses the story-generation runner/model for that many calibration trials."
        ),
    )
    parser.add_argument(
        "--calibration-runner-profile",
        help=(
            "Comma-separated Copilot alias runner counts for calibration workflow trials, "
            "for example "
            "'copilot-gemma4=2,copilot-deepseek-v4-flash=2,copilot-minimax-m2.7=2'. "
            "When omitted, the CLI defaults to that 2/2/2 six-run profile unless "
            "--calibration-runs is changed to request legacy single-runner behavior."
        ),
    )
    parser.add_argument(
        "--calibration-max-iterations",
        type=int,
        default=CALIBRATION_MAX_ITERATIONS,
        help="Maximum acceptance-criteria rewrite attempts per story. Defaults to 5.",
    )
    parser.add_argument(
        "--calibration-story-workers",
        type=int,
        default=CALIBRATION_STORY_WORKERS,
        help="Maximum stories to calibrate at the same time. Defaults to 2.",
    )
    parser.add_argument(
        "--calibration-fast-mode",
        action=argparse.BooleanOptionalAction,
        default=CALIBRATION_FAST_MODE,
        help="Use cheaper one-iteration workflow loops during calibration. Enabled by default.",
    )
    return parser


def synthesize_suites(
    *,
    manifest_path: Path,
    output_dir: Path,
    stories_output_dir: Path,
    repo: str | Path | None = None,
    runner: str = "copilot",
    model: str = "gpt-5-mini",
    agent: str = "task-generator",
    batch_size: int = 5,
    ac_hints_path: Optional[Path] = None,
    calibrate: bool = False,
    calibration_runs: int = CALIBRATION_RUNS,
    calibration_runner_profile: str | None = None,
    calibration_max_concurrent: int = CALIBRATION_MAX_CONCURRENT,
    calibration_max_iterations: int = CALIBRATION_MAX_ITERATIONS,
    calibration_story_workers: int = CALIBRATION_STORY_WORKERS,
    calibration_fast_mode: bool = CALIBRATION_FAST_MODE,
) -> Mapping[str, Any]:
    calibration_profile_payload: list[Mapping[str, Any]] = []
    calibration_run_specs: list[TrialRunSpec] = []
    story_worker_count = 1
    story_trial_concurrency = 0
    if calibrate:
        if calibration_runs <= 0:
            raise SynthesisError("calibration_runs must be a positive integer")
        if calibration_max_concurrent <= 0:
            raise SynthesisError("calibration_max_concurrent must be a positive integer")
        if calibration_max_iterations <= 0:
            raise SynthesisError("calibration_max_iterations must be a positive integer")
        if calibration_story_workers <= 0:
            raise SynthesisError("calibration_story_workers must be a positive integer")
        if calibration_runner_profile is None:
            calibration_profile = _legacy_calibration_runner_profile(
                runner=runner,
                model=model,
                runs=calibration_runs,
            )
        else:
            calibration_profile = parse_calibration_runner_profile(calibration_runner_profile)
        calibration_profile_payload = _calibration_runner_profile_payload(calibration_profile)
        calibration_run_specs = _expand_calibration_run_specs(calibration_profile_payload)
        calibration_runs = len(calibration_run_specs)
    manifest = load_dataset_manifest(manifest_path)
    lock_path = manifest_path.resolve().parent / f"{manifest.dataset_id}.lock"
    if not lock_path.is_file():
        raise SynthesisError(f"Missing dataset lock: {lock_path}. Run eval/init_dataset.py first.")
    lock = load_dataset_lock(lock_path)
    if lock.dataset_id != manifest.dataset_id:
        raise SynthesisError(f"Dataset lock id mismatch: manifest={manifest.dataset_id} lock={lock.dataset_id}")

    sample_path = _resolve_sample_path(lock.sample_path, manifest_path)
    if not sample_path.is_file():
        raise SynthesisError(f"Missing dataset sample from lock.sample_path: {sample_path}. Run eval/init_dataset.py first.")
    sample_records = read_jsonl(sample_path)
    resolved_repo = (
        resolve_calibration_repo(repo=repo, manifest=manifest, lock=lock, manifest_path=manifest_path)
        if calibrate
        else None
    )

    warnings = _compatibility_warnings(manifest, lock)
    hint_info = load_ac_hints(ac_hints_path) if ac_hints_path else {"flagged_story_ids": set(), "hints": {}}

    raw_dir = output_dir / "raw"
    existing_raw_stories = load_existing_raw_stories(raw_dir / "stories.jsonl")
    flagged_ids = set(hint_info["flagged_story_ids"])
    effective_hints = dict(hint_info["hints"])
    can_reuse_existing = bool(existing_raw_stories)

    expected_items = [
        {"story_id": story_id, "story_tier": story_tier}
        for story_id, story_tier in REPO_WIDE_STORY_SPECS
    ]
    to_synthesize: List[Mapping[str, Any]] = []
    pending_raw_by_id: Dict[str, Mapping[str, Any]] = {}
    final_raw_by_id: Dict[str, Mapping[str, Any]] = {}
    tiering_reports: Dict[str, Mapping[str, Any]] = {}
    calibration_reports: Dict[str, Mapping[str, Any]] = {}
    for item in expected_items:
        story_id = str(item["story_id"])
        if (
            can_reuse_existing
            and story_id in existing_raw_stories
            and story_id not in flagged_ids
            and calibrate
            and _story_has_compatible_calibration(
                existing_raw_stories[story_id],
                manifest=manifest,
                lock=lock,
                runner=runner,
                model=model,
                calibration_runner_profile=calibration_profile_payload,
                calibration_runs=calibration_runs,
                calibration_max_iterations=calibration_max_iterations,
                calibration_fast_mode=calibration_fast_mode,
            )
        ):
            reused_story = existing_raw_stories[story_id]
            reused_tiering = dict(_story_tiering_metadata(reused_story)) or classify_story_tier(
                raw_story=reused_story,
                sample_records=sample_records,
                manifest=manifest,
                lock=lock,
            )
            reused_story = _attach_tiering_metadata(reused_story, reused_tiering)
            final_raw_by_id[story_id] = reused_story
            tiering_reports[story_id] = {
                **reused_tiering,
                "reused": True,
            }
            calibration_reports[story_id] = {
                **dict(_story_calibration_metadata(reused_story)),
                "reused": True,
            }
        elif (
            can_reuse_existing
            and story_id in existing_raw_stories
            and story_id not in flagged_ids
            and not calibrate
            and _story_has_compatible_tiering(
                existing_raw_stories[story_id],
                manifest=manifest,
                lock=lock,
                runner=runner,
                model=model,
            )
        ):
            reused_story = existing_raw_stories[story_id]
            final_raw_by_id[story_id] = reused_story
            tiering_reports[story_id] = {
                **dict(_story_tiering_metadata(reused_story)),
                "reused": True,
            }
        elif (
            can_reuse_existing
            and story_id in existing_raw_stories
            and story_id not in flagged_ids
            and _story_has_compatible_synthesis(
                existing_raw_stories[story_id],
                manifest=manifest,
                lock=lock,
                runner=runner,
                model=model,
            )
        ):
            pending_raw_by_id[story_id] = existing_raw_stories[story_id]
        else:
            to_synthesize.append(item)

    synthesized_count = 0
    if to_synthesize:
        print(
            f"[synthesize] Generating {len(to_synthesize)} story shell(s) with "
            f"{runner}/{model} from dataset {manifest.dataset_id}..."
        )
        prompt = build_synthesis_prompt(
            expected_stories=to_synthesize,
            sample_records=sample_records,
            manifest=manifest,
            lock=lock,
            hints=effective_hints,
            calibrate=calibrate,
        )
        raw_response = run_agent_cmd(runner, prompt, agent, runner_model=model, stream_output=True)
        stories = parse_llm_stories(raw_response, expected_stories=to_synthesize)
        for story in stories:
            story_id = str(story["story_id"])
            synthesized_count += 1
            pending_raw_by_id[story_id] = _attach_synthesis_metadata(
                story,
                manifest=manifest,
                lock=lock,
                runner=runner,
                model=model,
            )
    if final_raw_by_id or pending_raw_by_id:
        _persist_raw_story_checkpoint(
            raw_dir=raw_dir,
            expected_items=expected_items,
            raw_story_by_id={**pending_raw_by_id, **final_raw_by_id},
        )

    stories_to_finalize = [
        pending_raw_by_id[str(item["story_id"])]
        for item in expected_items
        if str(item["story_id"]) in pending_raw_by_id
    ]
    if calibrate:
        story_worker_count = _calibration_story_worker_count(
            requested_workers=calibration_story_workers,
            story_count=len(stories_to_finalize),
        )
        story_trial_concurrency = _calibration_story_trial_concurrency(
            total_workflow_budget=calibration_max_concurrent,
            story_workers=story_worker_count,
        )
        if calibration_runner_profile is not None:
            story_trial_concurrency = max(len(calibration_run_specs), story_trial_concurrency)
        if stories_to_finalize:
            print(
                f"[synthesize] Calibrating {len(stories_to_finalize)} story shell(s) "
                f"with {story_worker_count} story worker(s) and up to "
                f"{story_trial_concurrency} workflow run(s) per story..."
            )
            calibration_kwargs = {
                "sample_records": sample_records,
                "manifest": manifest,
                "lock": lock,
                "lock_path": lock_path,
                "repo": resolved_repo,
                "runner": runner,
                "model": model,
                "agent": agent,
                "calibration_runs": calibration_runs,
                "calibration_run_specs": calibration_run_specs,
                "calibration_runner_profile": calibration_profile_payload,
                "calibration_max_iterations": calibration_max_iterations,
                "calibration_max_concurrent": story_trial_concurrency,
                "calibration_fast_mode": calibration_fast_mode,
            }
            if story_worker_count > 1:
                with ThreadPoolExecutor(max_workers=story_worker_count) as executor:
                    future_to_story_id = {
                        executor.submit(
                            calibrate_raw_story,
                            raw_story=story,
                            hint=effective_hints.get(str(story["story_id"])),
                            **calibration_kwargs,
                        ): str(story["story_id"])
                        for story in stories_to_finalize
                    }
                    for future in as_completed(future_to_story_id):
                        story_id = future_to_story_id[future]
                        calibrated_story, calibration_report = future.result()
                        final_raw_by_id[story_id] = calibrated_story
                        tiering_reports[story_id] = {
                            **dict(_story_tiering_metadata(calibrated_story)),
                            "reused": False,
                        }
                        calibration_reports[story_id] = calibration_report
                        pending_raw_by_id.pop(story_id, None)
                        _persist_raw_story_checkpoint(
                            raw_dir=raw_dir,
                            expected_items=expected_items,
                            raw_story_by_id={**pending_raw_by_id, **final_raw_by_id},
                        )
            else:
                for story in stories_to_finalize:
                    story_id = str(story["story_id"])
                    calibrated_story, calibration_report = calibrate_raw_story(
                        raw_story=story,
                        hint=effective_hints.get(story_id),
                        **calibration_kwargs,
                    )
                    final_raw_by_id[story_id] = calibrated_story
                    tiering_reports[story_id] = {
                        **dict(_story_tiering_metadata(calibrated_story)),
                        "reused": False,
                    }
                    calibration_reports[story_id] = calibration_report
                    pending_raw_by_id.pop(story_id, None)
                    _persist_raw_story_checkpoint(
                        raw_dir=raw_dir,
                        expected_items=expected_items,
                        raw_story_by_id={**pending_raw_by_id, **final_raw_by_id},
                    )
    else:
        for raw_story in stories_to_finalize:
            story_id = str(raw_story["story_id"])
            tiering_report = classify_story_tier(
                raw_story=raw_story,
                sample_records=sample_records,
                manifest=manifest,
                lock=lock,
            )
            finalized_story = _attach_tiering_metadata(raw_story, tiering_report)
            final_raw_by_id[story_id] = finalized_story
            tiering_reports[story_id] = {**tiering_report, "reused": False}
            pending_raw_by_id.pop(story_id, None)
        if stories_to_finalize:
            _persist_raw_story_checkpoint(
                raw_dir=raw_dir,
                expected_items=expected_items,
                raw_story_by_id=final_raw_by_id,
            )

    missing_ids = [str(item["story_id"]) for item in expected_items if str(item["story_id"]) not in final_raw_by_id]
    if missing_ids:
        raise SynthesisError(f"Synthesis did not produce required story id(s): {', '.join(missing_ids)}")

    ordered_raw_stories = [final_raw_by_id[str(item["story_id"])] for item in expected_items]
    _cleanup_generated_outputs(output_dir, stories_output_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(ordered_raw_stories, raw_dir / "stories.jsonl")

    tier_outputs = write_tier_suites(
        raw_stories=ordered_raw_stories,
        manifest=manifest,
        lock=lock,
        lock_path=lock_path,
        output_dir=output_dir,
        stories_output_dir=stories_output_dir,
        runner=runner,
        model=model,
        warnings=warnings,
    )

    report = {
        "dataset_id": manifest.dataset_id,
        "lock_path": str(lock_path),
        "sample_path": str(sample_path),
        "sample_count": len(sample_records),
        "synthesized_story_count": synthesized_count,
        "reused_story_count": len(expected_items) - synthesized_count,
        "tiering_mode": "calibrated" if calibrate else "predicted",
        "calibration_enabled": calibrate,
        "calibration_repo": str(resolved_repo) if resolved_repo is not None else None,
        "calibration_runs": calibration_runs if calibrate else 0,
        "calibration_max_concurrent": calibration_max_concurrent if calibrate else 0,
        "calibration_story_workers": story_worker_count if calibrate else 0,
        "calibration_story_trial_concurrency": story_trial_concurrency if calibrate else 0,
        "calibration_runner_profile": calibration_profile_payload,
        "calibration_max_iterations": calibration_max_iterations if calibrate else 0,
        "calibration_fast_mode": calibration_fast_mode if calibrate else False,
        "generated_runner": runner,
        "generated_model": model,
        "warnings": warnings,
        "tiers": tier_outputs,
        "tiering": {story_id: tiering_reports.get(story_id, {}) for story_id in [item["story_id"] for item in expected_items]},
        "calibration": {story_id: calibration_reports.get(story_id, {}) for story_id in [item["story_id"] for item in expected_items]},
        "ac_hints": {
            "path": str(ac_hints_path) if ac_hints_path else None,
            "flagged_story_ids": sorted(flagged_ids),
        },
    }
    write_json(output_dir / "synthesis_report.json", report)
    return report


def _resolve_sample_path(sample_path: str, manifest_path: Path) -> Path:
    path = Path(sample_path)
    if path.is_absolute():
        return path
    return (manifest_path.resolve().parent / path).resolve()


def read_jsonl(path: Path) -> List[Mapping[str, Any]]:
    records: List[Mapping[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise SynthesisError(f"Invalid JSONL in {path} line {line_number}: {exc}") from exc
            if not isinstance(payload, Mapping):
                raise SynthesisError(f"Sample record in {path} line {line_number} must be a JSON object")
            records.append(dict(payload))
    if not records:
        raise SynthesisError(f"Sample JSONL is empty: {path}")
    return records


def write_jsonl(records: Sequence[Mapping[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(dict(record), sort_keys=True, default=str) + "\n")
    temp_path.replace(path)


def write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
    temp_path.replace(path)


def _calibration_story_worker_count(*, requested_workers: int, story_count: int) -> int:
    if story_count <= 0:
        return 1
    return max(1, min(requested_workers, story_count))


def _calibration_story_trial_concurrency(*, total_workflow_budget: int, story_workers: int) -> int:
    return max(1, total_workflow_budget // max(1, story_workers))


def _attach_synthesis_metadata(
    raw_story: Mapping[str, Any],
    *,
    manifest: DatasetManifest,
    lock: DatasetLock,
    runner: str,
    model: str,
) -> Mapping[str, Any]:
    metadata = dict(raw_story.get("metadata") or {}) if isinstance(raw_story.get("metadata") or {}, Mapping) else {}
    metadata["synthesis"] = {
        "version": SYNTHESIS_METADATA_VERSION,
        "dataset_id": manifest.dataset_id,
        "lock_hash": lock.source_fingerprint,
        "generated_runner": runner,
        "generated_model": model,
    }
    enriched_story = dict(raw_story)
    enriched_story["metadata"] = metadata
    return enriched_story


def _story_synthesis_metadata(raw_story: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = raw_story.get("metadata")
    if not isinstance(metadata, Mapping):
        return {}
    synthesis = metadata.get("synthesis")
    if not isinstance(synthesis, Mapping):
        return {}
    return dict(synthesis)


def _story_has_compatible_synthesis(
    raw_story: Mapping[str, Any],
    *,
    manifest: DatasetManifest,
    lock: DatasetLock,
    runner: str,
    model: str,
) -> bool:
    synthesis = _story_synthesis_metadata(raw_story)
    return (
        int(synthesis.get("version", 0)) >= SYNTHESIS_METADATA_VERSION
        and synthesis.get("dataset_id") == manifest.dataset_id
        and synthesis.get("lock_hash") == lock.source_fingerprint
        and synthesis.get("generated_runner") == runner
        and synthesis.get("generated_model") == model
    )


def _story_has_compatible_calibration(
    raw_story: Mapping[str, Any],
    *,
    manifest: DatasetManifest,
    lock: DatasetLock,
    runner: str,
    model: str,
    calibration_runner_profile: Sequence[Mapping[str, Any]],
    calibration_runs: int,
    calibration_max_iterations: int,
    calibration_fast_mode: bool,
) -> bool:
    if not _story_has_compatible_synthesis(raw_story, manifest=manifest, lock=lock, runner=runner, model=model):
        return False
    calibration = _story_calibration_metadata(raw_story)
    return (
        int(calibration.get("version", 0)) >= CALIBRATION_METADATA_VERSION
        and list(calibration.get("runner_profile") or []) == _calibration_runner_profile_payload(calibration_runner_profile)
        and int(calibration.get("runs", 0)) == calibration_runs
        and int(calibration.get("max_iterations", 0)) == calibration_max_iterations
        and bool(calibration.get("fast_mode", False)) == calibration_fast_mode
        and calibration.get("status") == "in_band"
    )


def _persist_raw_story_checkpoint(
    *,
    raw_dir: Path,
    expected_items: Sequence[Mapping[str, Any]],
    raw_story_by_id: Mapping[str, Mapping[str, Any]],
) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    ordered_stories = [
        raw_story_by_id[str(item["story_id"])]
        for item in expected_items
        if str(item["story_id"]) in raw_story_by_id
    ]
    write_jsonl(ordered_stories, raw_dir / "stories.jsonl")


def _compatibility_warnings(manifest: DatasetManifest, lock: DatasetLock) -> List[str]:
    warnings: List[str] = []
    current_manifest_hash = stable_manifest_hash(manifest.to_dict())
    locked_manifest_hash = lock.metadata.get("manifest_hash")
    if locked_manifest_hash and locked_manifest_hash != current_manifest_hash:
        warnings.append(
            "Manifest hash differs from dataset lock metadata; re-run eval/init_dataset.py if the source schema changed."
        )
    if not locked_manifest_hash:
        warnings.append("Dataset lock metadata has no manifest_hash; manifest drift detection is limited.")
    if not lock.source_fingerprint:
        warnings.append("Dataset lock has no source_fingerprint; source drift detection is limited.")
    warnings.append(
        "Current source schema drift is not re-read during synthesis; comparison is limited to lock manifest_hash/source_fingerprint metadata."
    )
    return warnings


def _sample_record(index: int, record: Mapping[str, Any]) -> Mapping[str, Any]:
    return {"sample_index": index - 1, "record": dict(record)}


def resolve_calibration_repo(
    *,
    repo: str | Path | None,
    manifest: DatasetManifest,
    lock: DatasetLock,
    manifest_path: Path,
) -> Path:
    candidates: List[Path] = []
    if repo is not None:
        candidates.append(Path(repo).expanduser())

    source_path = manifest.source.get("path")
    if isinstance(source_path, str) and source_path.strip():
        candidates.append(_resolve_repo_candidate_path(source_path, manifest_path))

    source_metadata_path = lock.schema.get("source_metadata", {}).get("path")
    if isinstance(source_metadata_path, str) and source_metadata_path.strip():
        candidates.append(_resolve_repo_candidate_path(source_metadata_path, manifest_path))

    seen: set[Path] = set()
    for candidate in candidates:
        normalized = _normalize_repo_candidate(candidate)
        if normalized in seen:
            continue
        seen.add(normalized)
        git_root = _git_toplevel(normalized)
        if git_root is not None:
            return git_root
        if normalized.is_dir():
            if normalized.name.lower() in COMMON_REPO_SUBDIR_NAMES and normalized.parent.exists():
                return normalized.parent.resolve()
            return normalized.resolve()
        if normalized.parent.exists():
            return normalized.parent.resolve()
    raise SynthesisError("Could not resolve calibration repo. Pass --repo explicitly.")


def _resolve_repo_candidate_path(raw_path: str, manifest_path: Path) -> Path:
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (manifest_path.resolve().parent / candidate).resolve()


def _normalize_repo_candidate(path: Path) -> Path:
    candidate = path
    if candidate.is_file():
        candidate = candidate.parent
    return candidate.resolve()


def _git_toplevel(path: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    stdout = result.stdout.strip()
    if not stdout:
        return None
    return Path(stdout).resolve()


def calibrate_raw_story(
    *,
    raw_story: Mapping[str, Any],
    sample_records: Sequence[Mapping[str, Any]],
    manifest: DatasetManifest,
    lock: DatasetLock,
    lock_path: Path,
    repo: Path,
    runner: str,
    model: str,
    agent: str,
    calibration_runs: int = CALIBRATION_RUNS,
    calibration_run_specs: Sequence[TrialRunSpec] | None = None,
    calibration_runner_profile: Sequence[Mapping[str, Any]] | None = None,
    calibration_max_iterations: int = CALIBRATION_MAX_ITERATIONS,
    hint: str | None = None,
    calibration_max_concurrent: int = CALIBRATION_MAX_CONCURRENT,
    calibration_fast_mode: bool = CALIBRATION_FAST_MODE,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    validated_story = validate_raw_story(
        raw_story,
        expected_story_id=str(raw_story.get("story_id") or ""),
        expected_story_tier=str(raw_story.get("story_tier") or ""),
    )
    story_id = str(validated_story["story_id"])
    story_tier = str(validated_story["story_tier"])
    attempts: List[Mapping[str, Any]] = []
    current_story = dict(
        _attach_tiering_metadata(
            validated_story,
            classify_story_tier(
                raw_story=validated_story,
                sample_records=sample_records,
                manifest=manifest,
                lock=lock,
            ),
        )
    )

    for attempt_index in range(1, calibration_max_iterations + 1):
        print(
            f"[synthesize] Calibrating {story_id} ({story_tier}) attempt "
            f"{attempt_index}/{calibration_max_iterations}..."
        )
        if attempt_index > 1:
            prompt = build_ac_calibration_prompt(
                raw_story=current_story,
                sample_records=sample_records,
                manifest=manifest,
                lock=lock,
                hint=hint,
                previous_attempts=attempts,
                calibration_runs=calibration_runs,
            )
            raw_response = run_agent_cmd(runner, prompt, agent, runner_model=model, stream_output=True)
            current_story = dict(current_story)
            current_story["acceptance_criteria"] = parse_llm_acceptance_criteria(raw_response, story_id=story_id, story_tier=story_tier)
            current_story = dict(
                _attach_tiering_metadata(
                    current_story,
                    classify_story_tier(
                        raw_story=current_story,
                        sample_records=sample_records,
                        manifest=manifest,
                        lock=lock,
                    ),
                )
            )

        attempt_report = evaluate_story_calibration(
            raw_story=current_story,
            dataset_id=manifest.dataset_id,
            lock_path=lock_path,
            repo=repo,
            runner=runner,
            model=model,
            attempt_index=attempt_index,
            calibration_runs=calibration_runs,
            calibration_run_specs=calibration_run_specs,
            calibration_runner_profile=calibration_runner_profile,
            calibration_max_concurrent=calibration_max_concurrent,
            calibration_fast_mode=calibration_fast_mode,
        )
        attempts.append(attempt_report)
        print(
            f"[synthesize] {story_id} attempt {attempt_index}: "
            f"pass_rate={attempt_report['pass_rate']:.2f} "
            f"target={attempt_report['target_band']['min']:.2f}-{attempt_report['target_band']['max']:.2f} "
            f"status={attempt_report['status']}"
        )
        if attempt_report["status"] == "in_band":
            calibrated_story = _attach_calibration_metadata(
                current_story,
                attempt_report,
                attempts,
                calibration_runs=calibration_runs,
                calibration_runner_profile=calibration_runner_profile or [],
                calibration_max_iterations=calibration_max_iterations,
                calibration_fast_mode=calibration_fast_mode,
            )
            return calibrated_story, {
                "attempts": attempts,
                "selected_iteration": attempt_index,
                "pass_rate": attempt_report["pass_rate"],
                "passed_runs": attempt_report["passed_runs"],
                "total_runs": attempt_report["total_runs"],
                "completed_runs": attempt_report["completed_runs"],
                "target_band": _band_payload(story_tier),
                "runner_profile": _calibration_runner_profile_payload(calibration_runner_profile or []),
                "max_concurrent": calibration_max_concurrent,
                "fast_mode": calibration_fast_mode,
                "reused": False,
            }

    final_attempt = attempts[-1] if attempts else {}
    raise SynthesisError(
        f"Story {story_id} did not calibrate to {story_tier} after {calibration_max_iterations} attempts "
        f"(last pass_rate={final_attempt.get('pass_rate', 0.0):.2f})."
    )


def evaluate_story_calibration(
    *,
    raw_story: Mapping[str, Any],
    dataset_id: str,
    lock_path: Path | None,
    repo: Path,
    runner: str,
    model: str,
    attempt_index: int,
    calibration_runs: int,
    calibration_run_specs: Sequence[TrialRunSpec] | None,
    calibration_runner_profile: Sequence[Mapping[str, Any]] | None,
    calibration_max_concurrent: int,
    calibration_fast_mode: bool,
) -> Mapping[str, Any]:
    compatibility_story_id = f"{_artifact_stem(str(raw_story['story_id']))}_{raw_story['story_tier']}"
    calibration_change_id = f"{CALIBRATION_ROOT_PREFIX}{compatibility_story_id}_attempt_{attempt_index:02d}"
    eval_story = EvalStory(
        story_id=compatibility_story_id,
        change_id=calibration_change_id,
        title=str(raw_story["title"]),
        description=str(raw_story["description"]),
        suite_tier=str(raw_story["story_tier"]),  # type: ignore[arg-type]
        dataset_id=dataset_id,
        acceptance_criteria=[_acceptance_criterion(ac) for ac in raw_story["acceptance_criteria"]],
        metadata={
            "raw_story_id": raw_story["story_id"],
            "raw_story_tier": raw_story["story_tier"],
            "prompt": raw_story.get("prompt"),
            "lock_path": str(lock_path) if lock_path else None,
        },
    )
    temp_dir = Path(tempfile.mkdtemp(prefix="eval-synth-calibration-"))
    story_path = temp_dir / f"{compatibility_story_id}.yaml"
    dump_eval_story(eval_story, story_path)
    try:
        story_runs = _collect_calibration_story_runs(
            story_path=story_path,
            raw_story=raw_story,
            repo=str(repo),
            runner=runner,
            model=model,
            calibration_runs=calibration_runs,
            calibration_run_specs=calibration_run_specs or [],
            calibration_max_concurrent=calibration_max_concurrent,
            calibration_fast_mode=calibration_fast_mode,
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    failed_runs = [story_run for story_run in story_runs if story_run.subprocess_returncode != 0]
    if failed_runs:
        raise SynthesisError(
            format_calibration_failure(
                raw_story=raw_story,
                attempt_index=attempt_index,
                story_runs=story_runs,
                failed_runs=failed_runs,
                calibration_change_id=calibration_change_id,
                runner=runner,
            )
        )
    try:
        return summarize_calibration_attempt(
            raw_story=raw_story,
            story_runs=story_runs,
            attempt_index=attempt_index,
            planned_total_runs=calibration_runs,
        )
    finally:
        _cleanup_calibration_artifacts(calibration_change_id)


def _collect_calibration_story_runs(
    *,
    story_path: Path,
    raw_story: Mapping[str, Any],
    repo: str,
    runner: str,
    model: str,
    calibration_runs: int,
    calibration_run_specs: Sequence[TrialRunSpec],
    calibration_max_concurrent: int,
    calibration_fast_mode: bool,
) -> list[StoryRun]:
    story_runs: list[StoryRun] = []
    next_run_offset = 0
    while next_run_offset < len(calibration_run_specs):
        batch_run_specs = list(calibration_run_specs[next_run_offset : next_run_offset + calibration_max_concurrent])
        batch_runs = run_story_trials(
            story_path=story_path,
            repo=repo,
            runner=runner,
            model=model,
            runs=calibration_runs,
            run_specs=batch_run_specs,
            max_concurrent=min(calibration_max_concurrent, len(batch_run_specs)),
            skip_pipeline=False,
            calibration_fast_mode=calibration_fast_mode,
            stream_output=True,
        )
        story_runs.extend(batch_runs)
        if any(story_run.subprocess_returncode != 0 for story_run in batch_runs):
            break
        if _calibration_attempt_cannot_reach_band(raw_story=raw_story, story_runs=story_runs, planned_total_runs=calibration_runs):
            break
        next_run_offset += len(batch_run_specs)
    return story_runs


def summarize_calibration_attempt(
    *,
    raw_story: Mapping[str, Any],
    story_runs: Sequence[StoryRun],
    attempt_index: int,
    planned_total_runs: int,
) -> Mapping[str, Any]:
    completed_runs = len(story_runs)
    planned_total_runs = max(planned_total_runs, completed_runs)
    passed_runs = sum(
        1
        for story_run in story_runs
        if story_run.subprocess_returncode == 0 and story_run.checks and all(check.passed for check in story_run.checks)
    )
    remaining_runs = max(0, planned_total_runs - completed_runs)
    minimum_pass_rate = (passed_runs / planned_total_runs) if planned_total_runs else 0.0
    maximum_pass_rate = ((passed_runs + remaining_runs) / planned_total_runs) if planned_total_runs else 0.0
    story_tier = str(raw_story["story_tier"])
    if maximum_pass_rate < PASS_RATE_BANDS[story_tier][0]:
        status = "too_hard"
        pass_rate = maximum_pass_rate
    elif minimum_pass_rate > PASS_RATE_BANDS[story_tier][1]:
        status = "too_easy"
        pass_rate = minimum_pass_rate
    else:
        status = _pass_rate_status(story_tier, minimum_pass_rate)
        pass_rate = minimum_pass_rate
    ac_summaries: List[Mapping[str, Any]] = []
    for ac in raw_story["acceptance_criteria"]:
        check_id = str(ac["ac_id"])
        matching_checks = [check for story_run in story_runs for check in story_run.checks if check.check_id == check_id]
        ac_passed_runs = sum(1 for check in matching_checks if check.passed)
        ac_summaries.append(
            {
                "ac_id": check_id,
                "text": str(ac["text"]),
                "passed_runs": ac_passed_runs,
                "total_runs": completed_runs,
                "planned_total_runs": planned_total_runs,
                "pass_rate": (ac_passed_runs / completed_runs) if completed_runs else 0.0,
            }
        )
    return {
        "attempt": attempt_index,
        "story_id": str(raw_story["story_id"]),
        "story_tier": story_tier,
        "pass_rate": pass_rate,
        "passed_runs": passed_runs,
        "completed_runs": completed_runs,
        "total_runs": planned_total_runs,
        "pass_rate_bounds": {"min": minimum_pass_rate, "max": maximum_pass_rate},
        "stopped_early": completed_runs < planned_total_runs,
        "target_band": _band_payload(story_tier),
        "status": status,
        "acceptance_criteria": ac_summaries,
    }


def _calibration_attempt_cannot_reach_band(
    *,
    raw_story: Mapping[str, Any],
    story_runs: Sequence[StoryRun],
    planned_total_runs: int,
) -> bool:
    completed_runs = len(story_runs)
    planned_total_runs = max(planned_total_runs, completed_runs)
    if completed_runs >= planned_total_runs:
        return True
    passed_runs = sum(
        1
        for story_run in story_runs
        if story_run.subprocess_returncode == 0 and story_run.checks and all(check.passed for check in story_run.checks)
    )
    remaining_runs = max(0, planned_total_runs - completed_runs)
    minimum, maximum = PASS_RATE_BANDS[str(raw_story["story_tier"])]
    minimum_possible = (passed_runs / planned_total_runs) if planned_total_runs else 0.0
    maximum_possible = ((passed_runs + remaining_runs) / planned_total_runs) if planned_total_runs else 0.0
    return maximum_possible < minimum or minimum_possible > maximum


def format_calibration_failure(
    *,
    raw_story: Mapping[str, Any],
    attempt_index: int,
    story_runs: Sequence[StoryRun],
    failed_runs: Sequence[StoryRun],
    calibration_change_id: str,
    runner: str,
) -> str:
    run_details = _failed_calibration_run_details(failed_runs)
    artifacts_hint = _calibration_artifacts_hint(calibration_change_id)
    if _is_transient_infrastructure_failure(failed_runs, runner=runner):
        return (
            f"Calibration infrastructure failure for story {raw_story['story_id']} on attempt {attempt_index} "
            f"({len(failed_runs)}/{len(story_runs)} runs returned non-zero).\n"
            "Transient runner or backend transport failures were detected, so this attempt did not produce a usable "
            "model calibration signal.\n"
            f"{run_details}\n"
            f"{artifacts_hint}"
        )
    return (
        f"Calibration workflow failed for story {raw_story['story_id']} on attempt {attempt_index} "
        f"({len(failed_runs)}/{len(story_runs)} runs returned non-zero).\n"
        f"{run_details}\n"
        f"{artifacts_hint}"
    )


def _is_transient_infrastructure_failure(failed_runs: Sequence[StoryRun], *, runner: str) -> bool:
    if not failed_runs:
        return False
    return all(
        is_transient_runner_failure_text(story_run.artifact_text, runner=runner)
        for story_run in failed_runs
    )


def _failed_calibration_run_details(failed_runs: Sequence[StoryRun]) -> str:
    lines = ["Failed runs:"]
    for story_run in failed_runs[:CALIBRATION_FAILURE_PREVIEW_LIMIT]:
        lines.append(
            f"- {story_run.change_id} (exit {story_run.subprocess_returncode}): "
            f"{_failure_excerpt(story_run.artifact_text)}"
        )
    remaining = len(failed_runs) - CALIBRATION_FAILURE_PREVIEW_LIMIT
    if remaining > 0:
        lines.append(f"- ... {remaining} additional failed run(s) omitted")
    return "\n".join(lines)


def _failure_excerpt(artifact_text: str) -> str:
    if not artifact_text.strip():
        return "no workflow output captured"
    body = artifact_text.strip()
    workflow_marker = "--- workflow_output ---"
    if workflow_marker in body:
        body = body.split(workflow_marker, 1)[1].strip() or artifact_text.strip()
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if not lines:
        return "no workflow output captured"
    preview = " | ".join(lines[-CALIBRATION_FAILURE_OUTPUT_LINE_LIMIT :])
    if len(preview) > CALIBRATION_FAILURE_OUTPUT_CHAR_LIMIT:
        preview = preview[: CALIBRATION_FAILURE_OUTPUT_CHAR_LIMIT - 3].rstrip() + "..."
    return preview


def _calibration_artifacts_hint(calibration_change_id: str) -> str:
    artifacts_root = Path(__file__).resolve().parent.parent / "agent-context"
    return (
        "Calibration artifacts were preserved for inspection under "
        f"{artifacts_root / (calibration_change_id + '-RUN-*')}."
    )


def _cleanup_calibration_artifacts(change_id_prefix: str) -> None:
    agent_context_root = Path(__file__).resolve().parent.parent / "agent-context"
    if not agent_context_root.exists():
        return
    for path in agent_context_root.glob(f"{change_id_prefix}*"):
        shutil.rmtree(path, ignore_errors=True)


def _attach_calibration_metadata(
    raw_story: Mapping[str, Any],
    selected_attempt: Mapping[str, Any],
    attempts: Sequence[Mapping[str, Any]],
    *,
    calibration_runs: int,
    calibration_runner_profile: Sequence[Mapping[str, Any]],
    calibration_max_iterations: int,
    calibration_fast_mode: bool,
) -> Mapping[str, Any]:
    metadata = dict(raw_story.get("metadata") or {}) if isinstance(raw_story.get("metadata") or {}, Mapping) else {}
    metadata["calibration"] = {
        "version": CALIBRATION_METADATA_VERSION,
        "runs": calibration_runs,
        "runner_profile": _calibration_runner_profile_payload(calibration_runner_profile),
        "max_iterations": calibration_max_iterations,
        "fast_mode": calibration_fast_mode,
        "selected_iteration": selected_attempt["attempt"],
        "pass_rate": selected_attempt["pass_rate"],
        "passed_runs": selected_attempt["passed_runs"],
        "completed_runs": selected_attempt["completed_runs"],
        "total_runs": selected_attempt["total_runs"],
        "pass_rate_bounds": dict(selected_attempt.get("pass_rate_bounds") or {}),
        "stopped_early": bool(selected_attempt.get("stopped_early", False)),
        "target_band": dict(selected_attempt["target_band"]),
        "status": selected_attempt["status"],
        "attempts": [dict(attempt) for attempt in attempts],
    }
    calibrated_story = dict(raw_story)
    calibrated_story["metadata"] = metadata
    return calibrated_story


def _story_calibration_metadata(raw_story: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = raw_story.get("metadata")
    if not isinstance(metadata, Mapping):
        return {}
    calibration = metadata.get("calibration")
    if not isinstance(calibration, Mapping):
        return {}
    return dict(calibration)


def _story_has_calibration(raw_story: Mapping[str, Any]) -> bool:
    calibration = _story_calibration_metadata(raw_story)
    return int(calibration.get("version", 0)) >= CALIBRATION_METADATA_VERSION


def _story_tiering_metadata(raw_story: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = raw_story.get("metadata")
    if not isinstance(metadata, Mapping):
        return {}
    tiering = metadata.get("tiering")
    if not isinstance(tiering, Mapping):
        return {}
    return dict(tiering)


def _story_has_compatible_tiering(
    raw_story: Mapping[str, Any],
    *,
    manifest: DatasetManifest,
    lock: DatasetLock,
    runner: str,
    model: str,
) -> bool:
    if not _story_has_compatible_synthesis(raw_story, manifest=manifest, lock=lock, runner=runner, model=model):
        return False
    tiering = _story_tiering_metadata(raw_story)
    return (
        int(tiering.get("version", 0)) >= TIERING_METADATA_VERSION
        and tiering.get("method") == "heuristic"
        and tiering.get("requested_tier") == raw_story.get("story_tier")
        and tiering.get("predicted_tier") == raw_story.get("story_tier")
    )


def _band_payload(story_tier: str) -> Mapping[str, float]:
    minimum, maximum = PASS_RATE_BANDS[story_tier]
    return {"min": minimum, "max": maximum}


def _pass_rate_in_band(story_tier: str, pass_rate: float) -> bool:
    minimum, maximum = PASS_RATE_BANDS[story_tier]
    return minimum <= pass_rate <= maximum


def _pass_rate_status(story_tier: str, pass_rate: float) -> str:
    minimum, maximum = PASS_RATE_BANDS[story_tier]
    if pass_rate < minimum:
        return "too_hard"
    if pass_rate > maximum:
        return "too_easy"
    return "in_band"


def build_ac_calibration_prompt(
    *,
    raw_story: Mapping[str, Any],
    sample_records: Sequence[Mapping[str, Any]],
    manifest: DatasetManifest,
    lock: DatasetLock,
    hint: str | None,
    previous_attempts: Sequence[Mapping[str, Any]],
    calibration_runs: int,
) -> str:
    story_tier = str(raw_story["story_tier"])
    target_band = _band_payload(story_tier)
    feedback_payload = {
        "story": {
            "story_id": raw_story["story_id"],
            "story_tier": story_tier,
            "title": raw_story["title"],
            "description": raw_story["description"],
            "prompt": raw_story.get("prompt"),
        },
        "target_band": target_band,
        "hint": hint or "",
        "repository_summary": {
            "schema": lock.schema,
            "sample_count": len(sample_records),
            "source_metadata": lock.schema.get("source_metadata", {}),
            "sample_metadata": lock.schema.get("sample_metadata", {}),
        },
        "sample_records": [_sample_record(index, record) for index, record in enumerate(sample_records, start=1)],
        "previous_attempts": [dict(attempt) for attempt in previous_attempts],
    }
    return (
        "Refine acceptance criteria for a single repository-wide evaluation story.\n"
        "Keep story_id, story_tier, title, description, and prompt fixed. Change ONLY acceptance_criteria.\n"
        "Return ONLY valid JSON with shape {\"acceptance_criteria\": [...]}.\n"
        "Each acceptance criterion requires ac_id, tier, text, check_mechanism, check_subject, and rationale.\n"
        "Allowed tiers: easy, medium, hard. Allowed check_mechanism: contains, matches, command.\n"
        f"Target difficulty band for {story_tier}: min={target_band['min']:.2f}, max={target_band['max']:.2f}.\n"
        f"Calibration scoring uses {calibration_runs} workflow run(s). A run passes only if every AC passes.\n"
        f"Story pass rate = passed_runs / {calibration_runs}. If the last attempt was too_hard, make the AC set easier. "
        "If the last attempt was too_easy, make the AC set harder.\n"
        "Use realistic, repository-grounded checks; vary the number and strictness of ACs as needed to hit the target band.\n"
        "For calibration-bound stories, every acceptance criterion must be a repo-grounded command check: "
        "set check_mechanism to command, set check_subject to repo, and include command "
        "(or legacy argv) on every command-check acceptance criterion.\n"
        "Each command must fail on the starting repository state and pass only after the requested change is implemented.\n"
        "Do not use agent_output checks for calibration-bound stories.\n"
        "Do not include markdown fences or commentary.\n\n"
        f"Input payload:\n{json.dumps(feedback_payload, indent=2, sort_keys=True, default=str)}"
    )


def build_synthesis_prompt(
    *,
    expected_stories: Sequence[Mapping[str, Any]],
    sample_records: Sequence[Mapping[str, Any]],
    manifest: DatasetManifest,
    lock: DatasetLock,
    hints: Mapping[str, str],
    calibrate: bool,
) -> str:
    hint_lines = []
    for item in expected_stories:
        story_id = str(item["story_id"])
        if hints.get(story_id):
            hint_lines.append(f"- {story_id}: {hints[story_id]}")
    hint_text = "\n".join(hint_lines) if hint_lines else "None."
    if len(expected_stories) == len(REPO_WIDE_STORY_SPECS):
        requested_story_rule = (
            "The output must contain exactly three repository-wide stories total: one easy, one medium, and one hard.\n"
        )
    else:
        requested_story_rule = (
            "The requested stories are a subset of the repository-wide trio. Return only the requested story objects.\n"
        )
    tiering_guidance = (
        "These tiers are predicted by deterministic heuristics in the default synthesis flow. "
        "Aim for realistic easy/medium/hard task shapes without manufacturing difficulty via AC count alone.\n"
        if not calibrate
        else "These stories will be empirically calibrated later, so the initial acceptance_criteria should be a realistic first-pass candidate.\n"
    )
    payload = {
        "dataset_id": manifest.dataset_id,
        "display_name": manifest.display_name,
        "domain_context": manifest.domain_context,
        "repository_summary": {
            "schema": lock.schema,
            "sample_count": len(sample_records),
            "source_metadata": lock.schema.get("source_metadata", {}),
            "sample_metadata": lock.schema.get("sample_metadata", {}),
        },
        "requested_stories": list(expected_stories),
        "sample_records": [_sample_record(index, record) for index, record in enumerate(sample_records, start=1)],
    }
    return (
        "Synthesize repository-wide evaluation stories and acceptance criteria from the locked dataset sample.\n"
        "Return ONLY valid JSON with shape {\"stories\": [...]}.\n"
        "Generate exactly one story for each requested story_id/story_tier pair.\n"
        "Each story requires story_id, story_tier, title, description, and acceptance_criteria.\n"
        "Each acceptance criterion requires ac_id, tier, text, check_mechanism, check_subject, and rationale.\n"
        "Allowed tiers: easy, medium, hard. Allowed check_mechanism: contains, matches, command.\n"
        f"{requested_story_rule}"
        "Each story must have at least one acceptance criterion, and every acceptance criterion in a story must use the same tier as story_tier.\n"
        f"{tiering_guidance}"
        "Use the whole repository context summarized in the payload and the sampled evidence records collectively. "
        "Do not treat a single sample record as a standalone story seed.\n"
        "Every acceptance criterion should preferably be a repo-grounded command check: "
        "set check_mechanism to command, set check_subject to repo, and include command "
        "(or legacy argv) on every command-check acceptance criterion.\n"
        "Each command must fail on the starting repository state and pass only after the requested change is implemented.\n"
        "Avoid agent_output-only checks when a deterministic repo check is possible.\n"
        "Do not include markdown fences or commentary.\n\n"
        f"Calibration hints for flagged stories:\n{hint_text}\n\n"
        f"Input payload:\n{json.dumps(payload, indent=2, sort_keys=True, default=str)}"
    )


def parse_llm_stories(
    raw_response: str, *, expected_stories: Sequence[Mapping[str, Any]]
) -> List[Mapping[str, Any]]:
    payload = _extract_json(raw_response)
    if isinstance(payload, Mapping):
        raw_stories = payload.get("stories")
    else:
        raw_stories = payload
    if not isinstance(raw_stories, list):
        raise SynthesisError("Synthesis output must be a JSON object with a stories list or a stories list")

    expected_tiers = {str(item["story_id"]): str(item["story_tier"]) for item in expected_stories}
    by_id: Dict[str, Mapping[str, Any]] = {}
    expected = [str(item["story_id"]) for item in expected_stories]
    expected_set = set(expected)
    for item in raw_stories:
        if not isinstance(item, Mapping):
            raise SynthesisError("Each synthesized story must be a JSON object")
        story_id = _required_string(item.get("story_id"), "story_id")
        if story_id not in expected_set:
            raise SynthesisError(f"Synthesis output included unexpected story id: {story_id}")
        if story_id in by_id:
            raise SynthesisError(f"Synthesis output included duplicate story id: {story_id}")
        by_id[story_id] = validate_raw_story(
            item,
            expected_story_id=story_id,
            expected_story_tier=expected_tiers[story_id],
        )
    missing = [story_id for story_id in expected if story_id not in by_id]
    if missing:
        raise SynthesisError(f"Synthesis output missing story id(s): {', '.join(missing)}")
    return [by_id[story_id] for story_id in expected]


def parse_llm_acceptance_criteria(
    raw_response: str,
    *,
    story_id: str,
    story_tier: str,
) -> List[Mapping[str, Any]]:
    payload = _extract_json(raw_response)
    if isinstance(payload, Mapping):
        raw_acs = payload.get("acceptance_criteria") or payload.get("acs")
    else:
        raw_acs = payload
    if not isinstance(raw_acs, list) or not raw_acs:
        raise SynthesisError(f"Story {story_id} must include non-empty acceptance_criteria")
    acs = [validate_raw_ac(item, story_id=story_id) for item in raw_acs]
    mismatched_tiers = sorted({str(ac["tier"]) for ac in acs if str(ac["tier"]) != story_tier})
    if mismatched_tiers:
        raise SynthesisError(
            f"Story {story_id} acceptance criteria tiers must all match story_tier {story_tier}; "
            f"found {', '.join(mismatched_tiers)}"
        )
    return acs


def _extract_json(raw_response: str) -> Any:
    text = raw_response.strip()

    # Extract JSON from code fences anywhere in the response (not just at the start).
    fence_pattern = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)
    fence_matches = fence_pattern.findall(text)
    for block in fence_matches:
        candidate = block.strip()
        decoder = json.JSONDecoder()
        for index, char in enumerate(candidate):
            if char not in "[{":
                continue
            try:
                payload, _ = decoder.raw_decode(candidate[index:])
                return payload
            except json.JSONDecodeError:
                continue

    # Fall back to scanning the full text (handles responses without fences).
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            payload, _ = decoder.raw_decode(text[index:])
            return payload
        except json.JSONDecodeError:
            continue

    preview = raw_response.strip()[:800]
    raise SynthesisError(f"Synthesis output did not contain valid JSON. Raw response preview:\n{preview}")


def validate_raw_story(
    data: Any,
    *,
    expected_story_id: Optional[str] = None,
    expected_story_tier: Optional[str] = None,
    allow_tier_inference: bool = False,
) -> Mapping[str, Any]:
    if not isinstance(data, Mapping):
        raise SynthesisError("Each synthesized story must be a JSON object")
    story_id = _required_string(data.get("story_id") or expected_story_id, "story_id")
    if expected_story_id and story_id != expected_story_id:
        raise SynthesisError(f"Synthesized story_id mismatch: expected {expected_story_id}, got {story_id}")
    title = _required_string(data.get("title"), "title")
    description = _required_string(data.get("description"), "description")
    raw_acs = data.get("acceptance_criteria")
    if not isinstance(raw_acs, list) or not raw_acs:
        raise SynthesisError(f"Story {story_id} must include non-empty acceptance_criteria")
    acs = [validate_raw_ac(item, story_id=story_id) for item in raw_acs]
    story_tier_value = data.get("story_tier") or data.get("suite_tier") or data.get("tier")
    if story_tier_value is None and allow_tier_inference:
        ac_tiers = sorted({str(ac["tier"]) for ac in acs})
        if len(ac_tiers) != 1:
            raise SynthesisError(f"Story {story_id} must declare story_tier or use a single AC tier for inference")
        story_tier = ac_tiers[0]
    else:
        story_tier = _required_string(story_tier_value, "story_tier")
    if story_tier not in VALID_TIERS:
        raise SynthesisError(f"Story {story_id} story_tier must be one of: easy, medium, hard")
    if expected_story_tier and story_tier != expected_story_tier:
        raise SynthesisError(f"Story {story_id} story_tier mismatch: expected {expected_story_tier}, got {story_tier}")
    mismatched_tiers = sorted({str(ac["tier"]) for ac in acs if str(ac["tier"]) != story_tier})
    if mismatched_tiers:
        raise SynthesisError(
            f"Story {story_id} acceptance criteria tiers must all match story_tier {story_tier}; found {', '.join(mismatched_tiers)}"
        )
    return {
        "story_id": story_id,
        "story_tier": story_tier,
        "title": title,
        "description": description,
        "prompt": str(data.get("prompt") or description),
        "acceptance_criteria": acs,
        "metadata": dict(data.get("metadata") or {}) if isinstance(data.get("metadata") or {}, Mapping) else {},
    }


def validate_raw_ac(data: Any, *, story_id: str) -> Mapping[str, Any]:
    if not isinstance(data, Mapping):
        raise SynthesisError(f"AC for {story_id} must be a JSON object")
    ac_id = _required_string(data.get("ac_id"), "ac_id")
    tier = _required_string(data.get("tier"), "tier")
    if tier not in VALID_TIERS:
        raise SynthesisError(f"AC {ac_id} tier must be one of: easy, medium, hard")
    text = _required_string(data.get("text"), "text")
    mechanism = _required_string(data.get("check_mechanism"), "check_mechanism")
    if mechanism not in VALID_MECHANISMS:
        raise SynthesisError(f"AC {ac_id} check_mechanism must be one of: contains, matches, command")
    subject = _required_string(data.get("check_subject"), "check_subject")
    rationale = _required_string(data.get("rationale"), "rationale")

    ac: Dict[str, Any] = {
        "ac_id": ac_id,
        "tier": tier,
        "text": text,
        "check_mechanism": mechanism,
        "check_subject": subject,
        "rationale": rationale,
    }
    if mechanism in {"contains", "matches"}:
        normalized_mechanism, normalized_expected = _normalize_expected(
            data.get("expected"),
            mechanism=mechanism,
            fallback=text,
            ac_id=ac_id,
        )
        ac["check_mechanism"] = normalized_mechanism
        ac["expected"] = normalized_expected
    if mechanism == "command":
        command = data.get("command") or data.get("argv") or data.get("check_command")
        if command is None and subject != COMMAND_CHECK_SUBJECT_FALLBACK:
            command = subject
            ac["check_subject"] = COMMAND_CHECK_SUBJECT_FALLBACK
        if command is None and subject == COMMAND_CHECK_SUBJECT_FALLBACK:
            command = _infer_command_from_text(text)
        if isinstance(command, str):
            command = _required_string(command, "command")
        elif isinstance(command, list) and command and all(isinstance(part, str) and part.strip() for part in command):
            command = [part.strip() for part in command]
        else:
            raise SynthesisError(f"AC {ac_id} command checks require command as a non-empty string or string list")
        ac["command"] = command
    return ac

def _required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SynthesisError(f"{field_name} must be a non-empty string")
    return value.strip()


def _normalize_expected(value: Any, *, mechanism: str, fallback: str, ac_id: str) -> Tuple[str, str]:
    if value is None:
        return mechanism, _required_string(fallback, "expected")
    if isinstance(value, str):
        return mechanism, _required_string(value, "expected")
    if mechanism == "contains" and isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        parts = [_required_string(part, "expected list entry") for part in value]
        if len(parts) == 1:
            return mechanism, parts[0]
        lookaheads = "".join(f"(?=.*{re.escape(part)})" for part in parts)
        return "matches", f"(?s){lookaheads}.*"
    raise SynthesisError(f"AC {ac_id} expected must be a non-empty string")


def _slugify_command_target(value: str) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", value.lower())
    return "-".join(parts)


def _python_path_exists_command(path: str) -> List[str]:
    return [
        "python3",
        "-c",
        f"import pathlib, sys; sys.exit(0 if pathlib.Path({path!r}).exists() else 1)",
    ]


def _python_rglob_exists_command(pattern: str) -> List[str]:
    return [
        "python3",
        "-c",
        f"import pathlib, sys; sys.exit(0 if any(pathlib.Path('.').rglob({pattern!r})) else 1)",
    ]


def _extract_path_candidates(text: str) -> List[str]:
    return [
        match.rstrip(".,)")
        for match in re.findall(r"([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+)", text)
    ]


def _extract_symbol_candidate(text: str) -> Optional[str]:
    patterns = (
        r"named\s+([A-Za-z_][A-Za-z0-9_-]*)",
        r"function\s+([A-Za-z_][A-Za-z0-9_-]*)",
        r"method\s+([A-Za-z_][A-Za-z0-9_-]*)",
        r"include\s+(?:an?|the)\s+([A-Za-z][A-Za-z0-9_-]*)\s+element",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _infer_command_from_text(text: str) -> Optional[str | List[str]]:
    backtick_match = re.search(r"`([^`]+)`", text)
    if backtick_match:
        candidate = backtick_match.group(1).strip()
        return candidate or None
    colon_match = re.search(
        r":\s*((?:pnpm|npm|npx|nx|python3|python|pytest|yarn|go|cargo|dotnet|make)\b[^()\n]*)",
        text,
    )
    if colon_match:
        candidate = colon_match.group(1).strip().rstrip(".")
        if candidate:
            return candidate

    lower_text = text.lower()
    path_candidates = _extract_path_candidates(text)
    symbol = _extract_symbol_candidate(text)
    if path_candidates:
        path = path_candidates[0]
        if symbol and any(word in lower_text for word in ("contains", "implemented", "exported", "named", "method", "element")):
            return ["grep", symbol, path]
        if any(word in lower_text for word in ("exists", "created")):
            return _python_path_exists_command(path)

    filename_match = re.search(r"\b([A-Za-z0-9_-]+\.(?:component\.cy\.ts|spec\.ts|test\.ts|ts|tsx|js|jsx))\b", text)
    if filename_match and any(word in lower_text for word in ("exists", "created")):
        return _python_rglob_exists_command(filename_match.group(1))

    component_match = re.search(r"for the ([A-Za-z0-9-]+) component", lower_text)
    if component_match and "cypress component test" in lower_text:
        return _python_rglob_exists_command(f"{component_match.group(1)}.component.cy.ts")

    unit_test_match = re.search(r"the ([a-z0-9 /-]+?) unit tests pass without failures", lower_text)
    if unit_test_match:
        project = _slugify_command_target(unit_test_match.group(1))
        if project:
            return ["npx", "nx", "test", project]

    component_test_match = re.search(r"the ([a-z0-9 /-]+?) component tests pass without failures", lower_text)
    if component_test_match:
        project = _slugify_command_target(component_test_match.group(1))
        if project:
            return ["npx", "nx", "component-test", project]
    return None


def classify_story_tier(
    *,
    raw_story: Mapping[str, Any],
    sample_records: Sequence[Mapping[str, Any]],
    manifest: DatasetManifest,
    lock: DatasetLock,
) -> Mapping[str, Any]:
    requested_tier = str(raw_story["story_tier"])
    acceptance_criteria = [dict(ac) for ac in raw_story["acceptance_criteria"]]
    command_checks = [ac for ac in acceptance_criteria if str(ac.get("check_mechanism")) == "command"]
    matches_checks = [ac for ac in acceptance_criteria if str(ac.get("check_mechanism")) == "matches"]
    contains_checks = [ac for ac in acceptance_criteria if str(ac.get("check_mechanism")) == "contains"]

    unique_paths: set[str] = set()
    repo_roots: set[str] = set()
    cross_cutting_markers = 0
    public_surface_markers = 0
    test_markers = 0
    sophisticated_commands = 0
    simple_repo_checks = 0
    story_text = " ".join(
        [
            str(raw_story.get("title") or ""),
            str(raw_story.get("description") or ""),
            str(raw_story.get("prompt") or ""),
            *[str(ac.get("text") or "") for ac in acceptance_criteria],
        ]
    ).lower()

    marker_sets = {
        "cross_cutting": ("across", "workflow", "end-to-end", "boundary", "integration", "multiple"),
        "public_surface": ("api", "endpoint", "export", "public surface", "contract", "consumer"),
        "tests": ("test", "tests", "cypress", "storybook", "spec", "harness"),
    }
    for marker in marker_sets["cross_cutting"]:
        if marker in story_text:
            cross_cutting_markers += 1
    for marker in marker_sets["public_surface"]:
        if marker in story_text:
            public_surface_markers += 1
    for marker in marker_sets["tests"]:
        if marker in story_text:
            test_markers += 1

    for ac in acceptance_criteria:
        path_candidates = _extract_path_candidates(str(ac.get("text") or ""))
        command_value = ac.get("command")
        if isinstance(command_value, str):
            path_candidates.extend(_extract_path_candidates(command_value))
            command_text = command_value
        elif isinstance(command_value, Sequence) and not isinstance(command_value, (str, bytes)):
            command_text = " ".join(str(part) for part in command_value)
            path_candidates.extend(_extract_path_candidates(command_text))
        else:
            command_text = ""
        for path in path_candidates:
            unique_paths.add(path)
            repo_roots.add(path.split("/", 1)[0])
        normalized_command = command_text.lower()
        if normalized_command:
            if any(token in normalized_command for token in ("pytest", "nx test", "go test", "cargo test", "dotnet test", "npm test", "pnpm test", "build", "compileall")):
                sophisticated_commands += 1
            elif any(token in normalized_command for token in ("grep ", "pathlib.path", "rglob(", "exists()")):
                simple_repo_checks += 1

    sample_layers = lock.schema.get("source_metadata", {}).get("layer_distribution", {})
    layer_count = len(sample_layers) if isinstance(sample_layers, Mapping) else 0
    score = 0
    score += len(contains_checks) * 8
    score += len(matches_checks) * 34
    score += len(command_checks) * 64
    score += max(0, len(acceptance_criteria) - 1) * 8
    score += min(len(unique_paths), 6) * 6
    score += min(len(repo_roots), 4) * 7
    score += min(cross_cutting_markers, 3) * 5
    score += min(public_surface_markers, 3) * 4
    score += min(test_markers, 3) * 3
    score += sophisticated_commands * 8
    if command_checks and not sophisticated_commands:
        score += simple_repo_checks * 2
    if manifest.source.get("type") == "code_repository":
        score += min(layer_count, 6)
    elif sample_records:
        score += min(len(sample_records), 5)

    if score < 30:
        predicted_tier = "easy"
    elif score < 60:
        predicted_tier = "medium"
    else:
        predicted_tier = "hard"

    boundary_distance = min(abs(score - 30), abs(score - 60))
    confidence = "high" if boundary_distance >= 10 else "medium" if boundary_distance >= 5 else "borderline"
    return {
        "version": TIERING_METADATA_VERSION,
        "method": "heuristic",
        "requested_tier": requested_tier,
        "predicted_tier": predicted_tier,
        "alignment": predicted_tier == requested_tier,
        "confidence": confidence,
        "heuristic_score": score,
        "signals": {
            "acceptance_criteria": len(acceptance_criteria),
            "contains_checks": len(contains_checks),
            "matches_checks": len(matches_checks),
            "command_checks": len(command_checks),
            "unique_paths": len(unique_paths),
            "repo_roots": len(repo_roots),
            "cross_cutting_markers": cross_cutting_markers,
            "public_surface_markers": public_surface_markers,
            "test_markers": test_markers,
            "sophisticated_commands": sophisticated_commands,
            "simple_repo_checks": simple_repo_checks,
        },
    }


def _attach_tiering_metadata(
    raw_story: Mapping[str, Any],
    tiering_report: Mapping[str, Any],
) -> Mapping[str, Any]:
    metadata = dict(raw_story.get("metadata") or {}) if isinstance(raw_story.get("metadata") or {}, Mapping) else {}
    metadata.pop("calibration", None)
    metadata["tiering"] = dict(tiering_report)
    enriched_story = dict(raw_story)
    enriched_story["metadata"] = metadata
    return enriched_story


def _cleanup_generated_outputs(output_dir: Path, stories_output_dir: Path) -> None:
    for tier in ("easy", "medium", "hard"):
        tier_dir = output_dir / tier
        if tier_dir.is_dir():
            for story_file in tier_dir.glob("story_*.yaml"):
                story_file.unlink()
            manifest_path = tier_dir / "suite_manifest.yaml"
            if manifest_path.exists():
                manifest_path.unlink()
    if stories_output_dir.is_dir():
        for story_file in stories_output_dir.glob("story_*.json"):
            story_file.unlink()
    raw_dir = output_dir / "raw"
    if raw_dir.is_dir():
        stories_path = raw_dir / "stories.jsonl"
        if stories_path.exists():
            stories_path.unlink()
    synthesis_report = output_dir / "synthesis_report.json"
    if synthesis_report.exists():
        synthesis_report.unlink()


def write_tier_suites(
    *,
    raw_stories: Sequence[Mapping[str, Any]],
    manifest: DatasetManifest,
    lock: DatasetLock,
    lock_path: Path,
    output_dir: Path,
    stories_output_dir: Path,
    runner: str,
    model: str,
    warnings: Sequence[str],
) -> Mapping[str, Any]:
    tier_summaries: Dict[str, Any] = {}
    for tier in ("easy", "medium", "hard"):
        tier_dir = output_dir / tier
        story_files: List[str] = []
        compatibility_story_ids: List[str] = []
        total_checks = 0
        for raw_story in raw_stories:
            if str(raw_story["story_tier"]) != tier:
                continue
            tier_acs = list(raw_story["acceptance_criteria"])
            raw_story_id = str(raw_story["story_id"])
            artifact_stem = _artifact_stem(raw_story_id)
            compatibility_story_id = f"{artifact_stem}_{tier}"
            eval_story = _bucket_story(raw_story, tier_acs, tier, compatibility_story_id, manifest.dataset_id, lock_path)
            yaml_path = tier_dir / f"{compatibility_story_id}.yaml"
            dump_eval_story(eval_story, yaml_path)
            story_files.append(yaml_path.name)
            compatibility_story_ids.append(compatibility_story_id)
            total_checks += len(tier_acs)

            fixture = workflow_fixture_from_story(eval_story)
            fixture["raw_metadata"] = {
                "suite_yaml": str(yaml_path),
                "raw_story_id": raw_story_id,
                "tier": tier,
                "dataset_id": manifest.dataset_id,
            }
            write_json(stories_output_dir / f"{compatibility_story_id}.json", fixture)

        manifest_payload = {
            "suite_id": f"{manifest.dataset_id}-{tier}",
            "suite_tier": tier,
            "dataset_id": manifest.dataset_id,
            "dataset_lock_hash": lock.source_fingerprint,
            "stories": story_files,
            "total_checks": total_checks,
            "generated_runner": runner,
            "generated_model": model,
            "tiering_mode": "calibrated" if any(_story_has_calibration(story) for story in raw_stories if str(story["story_tier"]) == tier) else "predicted",
            "warnings": list(warnings),
            "compatibility_story_ids": compatibility_story_ids,
            "metadata": {
                "lock_path": str(lock_path),
                "lock_hash": lock.source_fingerprint,
                "schema_hash": lock.schema.get("stable_hash"),
                "tiering_method": "heuristic",
            },
        }
        dump_yaml(manifest_payload, tier_dir / "suite_manifest.yaml")
        tier_summaries[tier] = {
            "story_count": len(story_files),
            "total_checks": total_checks,
            "manifest_path": str(tier_dir / "suite_manifest.yaml"),
            "compatibility_story_ids": compatibility_story_ids,
        }
    return tier_summaries


def _bucket_story(
    raw_story: Mapping[str, Any],
    raw_acs: Sequence[Mapping[str, Any]],
    tier: str,
    compatibility_story_id: str,
    dataset_id: str,
    lock_path: Path,
) -> EvalStory:
    criteria = [_acceptance_criterion(ac) for ac in raw_acs]
    metadata = dict(raw_story.get("metadata") or {}) if isinstance(raw_story.get("metadata") or {}, Mapping) else {}
    metadata.update(
        {
            "raw_story_id": raw_story["story_id"],
            "raw_story_tier": raw_story["story_tier"],
            "prompt": raw_story.get("prompt"),
            "lock_path": str(lock_path),
        }
    )
    return EvalStory(
        story_id=compatibility_story_id,
        change_id=compatibility_story_id,
        title=str(raw_story["title"]),
        description=str(raw_story["description"]),
        suite_tier=tier,  # type: ignore[arg-type]
        dataset_id=dataset_id,
        acceptance_criteria=criteria,
        metadata=metadata,
    )


def _acceptance_criterion(ac: Mapping[str, Any]) -> AcceptanceCriterion:
    mechanism = str(ac["check_mechanism"])
    check_payload: Dict[str, Any] = {
        "id": str(ac["ac_id"]),
        "label": str(ac["text"]),
        "mechanism": mechanism,
        "subject": str(ac["check_subject"]),
        "metadata": {"rationale": ac["rationale"], "suite_tier": ac["tier"]},
    }
    if mechanism in {"contains", "matches"}:
        check_payload["expected"] = str(ac["expected"])
    if mechanism == "command":
        check_payload["command"] = ac["command"]
    return AcceptanceCriterion(
        ac_id=str(ac["ac_id"]),
        text=str(ac["text"]),
        tier=str(ac["tier"]),  # type: ignore[arg-type]
        rationale=str(ac["rationale"]),
        check=CheckDefinition.from_dict(check_payload),
        metadata={"check_mechanism": mechanism, "check_subject": ac["check_subject"]},
    )


def _artifact_stem(story_id: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", story_id).strip("_")
    if slug.startswith("story_"):
        return slug
    return f"story_{slug}"


def load_existing_raw_stories(path: Path) -> Mapping[str, Mapping[str, Any]]:
    if not path.is_file():
        return {}
    stories = read_jsonl(path)
    by_id: Dict[str, Mapping[str, Any]] = {}
    for story in stories:
        try:
            validated = validate_raw_story(story, allow_tier_inference=True)
        except SynthesisError:
            continue
        by_id[str(validated["story_id"])] = validated
    return by_id


def load_ac_hints(path: Optional[Path]) -> Mapping[str, Any]:
    if path is None:
        return {"flagged_story_ids": set(), "hints": {}}
    if not path.is_file():
        raise SynthesisError(f"AC hints calibration report not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    flagged: set[str] = set()
    hints: Dict[str, str] = {}
    _collect_hints(payload, flagged, hints)
    return {"flagged_story_ids": flagged, "hints": hints}


def _collect_hints(node: Any, flagged: set[str], hints: Dict[str, str]) -> None:
    if isinstance(node, Mapping):
        story_id = node.get("story_id") or node.get("id") or node.get("change_id")
        is_flagged = bool(node.get("flagged") or node.get("needs_resynthesis") or node.get("needs_revision"))
        hint = node.get("hint") or node.get("hint_text") or node.get("recommendation") or node.get("message")
        if isinstance(story_id, str) and (is_flagged or hint):
            flagged.add(story_id)
            if isinstance(hint, str) and hint.strip():
                hints[story_id] = hint.strip()
        for key in ("flagged_stories", "stories", "results", "items", "checks"):
            if key in node:
                _collect_hints(node[key], flagged, hints)
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, str):
                flagged.add(item)
            else:
                _collect_hints(item, flagged, hints)


def print_summary(report: Mapping[str, Any]) -> None:
    print(f"Synthesized eval suites for dataset: {report['dataset_id']}")
    print(f"Sample records: {report['sample_count']}")
    print(f"Stories synthesized: {report['synthesized_story_count']}")
    print(f"Stories reused: {report['reused_story_count']}")
    print(f"Tiering mode: {report.get('tiering_mode', 'predicted')}")
    if report.get("calibration_enabled"):
        calibration_profile = ", ".join(
            f"{entry['runner']}={entry['count']}"
            for entry in report.get("calibration_runner_profile", [])
        )
        print(
            "Calibration profile: "
            f"runs={report['calibration_runs']} "
            f"max_iterations={report['calibration_max_iterations']} "
            f"max_concurrent={report['calibration_max_concurrent']} "
            f"story_workers={report['calibration_story_workers']} "
            f"fast_mode={report['calibration_fast_mode']} "
            f"runner_profile={calibration_profile or 'legacy-single-runner'}"
        )
    for tier, summary in report["tiers"].items():
        print(f"{tier}: {summary['story_count']} stories, {summary['total_checks']} checks")
    for warning in report.get("warnings", []):
        print(f"warning: {warning}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    # Resolve effective model: alias runners encode the model in the runner name
    if args.model is not None:
        effective_model = args.model
    elif args.runner not in KNOWN_RUNNERS:
        effective_model = resolve_runner_model(args.runner)
    else:
        effective_model = "gpt-5-mini"
    if args.calibrate:
        if args.calibration_runner_profile is not None:
            calibration_runner_profile = args.calibration_runner_profile
        elif args.calibration_runs != CALIBRATION_RUNS:
            calibration_runner_profile = None
        else:
            calibration_runner_profile = DEFAULT_CALIBRATION_RUNNER_PROFILE
    else:
        calibration_runner_profile = None
    try:
        report = synthesize_suites(
            manifest_path=Path(args.dataset),
            output_dir=Path(args.output),
            stories_output_dir=Path(args.stories_output),
            repo=args.repo,
            runner=args.runner,
            model=effective_model,
            agent=args.agent,
            batch_size=args.batch_size,
            ac_hints_path=Path(args.ac_hints) if args.ac_hints else None,
            calibrate=args.calibrate,
            calibration_runs=args.calibration_runs,
            calibration_runner_profile=calibration_runner_profile,
            calibration_max_concurrent=args.calibration_max_concurrent,
            calibration_max_iterations=args.calibration_max_iterations,
            calibration_story_workers=args.calibration_story_workers,
            calibration_fast_mode=args.calibration_fast_mode,
        )
    except (SynthesisError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
