"""Synthesize difficulty-tier eval suites from a locked dataset sample."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

if __package__ in {None, ""}:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import run_cmds
    from eval.dataset_manifest import load_dataset_lock, load_dataset_manifest, stable_manifest_hash
    from eval.models import AcceptanceCriterion, CheckDefinition, DatasetLock, DatasetManifest, EvalStory
    from eval.suite_io import dump_eval_story, workflow_fixture_from_story
    from eval.yaml_io import dump_yaml
else:
    import run_cmds
    from .dataset_manifest import load_dataset_lock, load_dataset_manifest, stable_manifest_hash
    from .models import AcceptanceCriterion, CheckDefinition, DatasetLock, DatasetManifest, EvalStory
    from .suite_io import dump_eval_story, workflow_fixture_from_story
    from .yaml_io import dump_yaml


VALID_TIERS = {"easy", "medium", "hard"}
VALID_MECHANISMS = {"contains", "matches", "command"}


class SynthesisError(RuntimeError):
    """Raised when suite synthesis cannot proceed safely."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Synthesize eval suites from a locked dataset sample.")
    parser.add_argument("--dataset", required=True, help="Path to eval/datasets/<dataset>.yaml")
    parser.add_argument("--output", default="eval/suites/", help="Suite output directory")
    parser.add_argument("--stories-output", default="eval/stories/", help="Workflow-compatible JSON output directory")
    parser.add_argument("--runner", default="copilot", choices=("claude", "copilot", "gemini"), help="LLM runner")
    parser.add_argument("--model", default="gpt-5-mini", help="Runner model")
    parser.add_argument("--agent", default="task-generator", help="Agent to use for story synthesis")
    parser.add_argument("--batch-size", type=int, default=5, help="Sample records per synthesis call")
    parser.add_argument(
        "--ac-hints",
        help="Optional calibration report JSON with flagged story hints; only flagged existing raw stories are re-synthesized",
    )
    return parser


def synthesize_suites(
    *,
    manifest_path: Path,
    output_dir: Path,
    stories_output_dir: Path,
    runner: str = "copilot",
    model: str = "gpt-5-mini",
    agent: str = "task-generator",
    batch_size: int = 5,
    ac_hints_path: Optional[Path] = None,
) -> Mapping[str, Any]:
    if batch_size <= 0:
        raise SynthesisError("--batch-size must be a positive integer")

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

    warnings = _compatibility_warnings(manifest, lock)
    hint_info = load_ac_hints(ac_hints_path) if ac_hints_path else {"flagged_story_ids": set(), "hints": {}}

    raw_dir = output_dir / "raw"
    existing_raw_stories = load_existing_raw_stories(raw_dir / "stories.jsonl") if ac_hints_path else {}
    flagged_ids = set(hint_info["flagged_story_ids"])
    can_reuse_existing = bool(ac_hints_path and existing_raw_stories)

    expected_items = [_sample_item(index, record) for index, record in enumerate(sample_records, start=1)]
    to_synthesize: List[Mapping[str, Any]] = []
    final_raw_by_id: Dict[str, Mapping[str, Any]] = {}
    for item in expected_items:
        story_id = str(item["story_id"])
        if can_reuse_existing and story_id in existing_raw_stories and story_id not in flagged_ids:
            final_raw_by_id[story_id] = existing_raw_stories[story_id]
        else:
            to_synthesize.append(item)

    synthesized_count = 0
    for batch in _chunks(to_synthesize, batch_size):
        prompt = build_synthesis_prompt(batch, manifest, lock, hint_info["hints"])
        raw_response = run_cmds.run_agent_cmd(runner, prompt, agent, runner_model=model)
        stories = parse_llm_stories(raw_response, expected_story_ids=[str(item["story_id"]) for item in batch])
        for story in stories:
            final_raw_by_id[str(story["story_id"])] = story
            synthesized_count += 1

    missing_ids = [str(item["story_id"]) for item in expected_items if str(item["story_id"]) not in final_raw_by_id]
    if missing_ids:
        raise SynthesisError(f"Synthesis did not produce required story id(s): {', '.join(missing_ids)}")

    ordered_raw_stories = [final_raw_by_id[str(item["story_id"])] for item in expected_items]
    warnings.extend(_missing_tier_warnings(ordered_raw_stories))
    raw_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(ordered_raw_stories, raw_dir / "stories.jsonl")
    for raw_story in ordered_raw_stories:
        ac_path = raw_dir / f"{_artifact_stem(str(raw_story['story_id']))}_acs.json"
        write_json(ac_path, {"story_id": raw_story["story_id"], "acceptance_criteria": raw_story["acceptance_criteria"]})

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
        "generated_runner": runner,
        "generated_model": model,
        "warnings": warnings,
        "tiers": tier_outputs,
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
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(dict(record), sort_keys=True, default=str) + "\n")


def write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")


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


def _sample_item(index: int, record: Mapping[str, Any]) -> Mapping[str, Any]:
    return {"story_id": f"story_{index:03d}", "record_index": index - 1, "record": dict(record)}


def _chunks(items: Sequence[Mapping[str, Any]], size: int) -> Iterable[Sequence[Mapping[str, Any]]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def build_synthesis_prompt(
    batch: Sequence[Mapping[str, Any]],
    manifest: DatasetManifest,
    lock: DatasetLock,
    hints: Mapping[str, str],
) -> str:
    hint_lines = []
    for item in batch:
        story_id = str(item["story_id"])
        if hints.get(story_id):
            hint_lines.append(f"- {story_id}: {hints[story_id]}")
    hint_text = "\n".join(hint_lines) if hint_lines else "None."
    payload = {
        "dataset_id": manifest.dataset_id,
        "display_name": manifest.display_name,
        "domain_context": manifest.domain_context,
        "schema": lock.schema,
        "records": list(batch),
    }
    return (
        "Synthesize evaluation stories and acceptance criteria from the locked dataset sample.\n"
        "Return ONLY valid JSON with shape {\"stories\": [...]}.\n"
        "For each requested record, produce exactly one story using the provided story_id.\n"
        "Each story requires story_id, title, description, and acceptance_criteria.\n"
        "Each acceptance criterion requires ac_id, tier, text, check_mechanism, check_subject, rationale.\n"
        "Allowed tiers: easy, medium, hard. Allowed check_mechanism: contains, matches, command.\n"
        "For contains/matches include expected when possible. For command include command as argv or a simple command string.\n"
        "Do not include markdown fences or commentary.\n\n"
        f"Calibration hints for flagged stories:\n{hint_text}\n\n"
        f"Input payload:\n{json.dumps(payload, indent=2, sort_keys=True, default=str)}"
    )


def parse_llm_stories(raw_response: str, *, expected_story_ids: Sequence[str]) -> List[Mapping[str, Any]]:
    payload = _extract_json(raw_response)
    if isinstance(payload, Mapping):
        raw_stories = payload.get("stories")
    else:
        raw_stories = payload
    if not isinstance(raw_stories, list):
        raise SynthesisError("Synthesis output must be a JSON object with a stories list or a stories list")

    by_id: Dict[str, Mapping[str, Any]] = {}
    expected = list(expected_story_ids)
    expected_set = set(expected)
    for item in raw_stories:
        story = validate_raw_story(item)
        story_id = str(story["story_id"])
        if story_id not in expected_set:
            raise SynthesisError(f"Synthesis output included unexpected story id: {story_id}")
        if story_id in by_id:
            raise SynthesisError(f"Synthesis output included duplicate story id: {story_id}")
        by_id[story_id] = story
    missing = [story_id for story_id in expected if story_id not in by_id]
    if missing:
        raise SynthesisError(f"Synthesis output missing story id(s): {', '.join(missing)}")
    return [by_id[story_id] for story_id in expected]


def _extract_json(raw_response: str) -> Any:
    text = raw_response.strip()
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
    raise SynthesisError("Synthesis output did not contain valid JSON")


def validate_raw_story(data: Any, *, expected_story_id: Optional[str] = None) -> Mapping[str, Any]:
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
    return {
        "story_id": story_id,
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
        ac["expected"] = _required_string(data.get("expected") or text, "expected")
    if mechanism == "command":
        command = data.get("command") or data.get("check_command")
        if isinstance(command, str):
            command = _required_string(command, "command")
        elif isinstance(command, list) and command and all(isinstance(part, str) and part.strip() for part in command):
            command = [part.strip() for part in command]
        else:
            raise SynthesisError(f"AC {ac_id} command checks require command as a non-empty string or string list")
        ac["command"] = command
    return ac


def _missing_tier_warnings(raw_stories: Sequence[Mapping[str, Any]]) -> List[str]:
    warnings: List[str] = []
    for raw_story in raw_stories:
        story_id = str(raw_story["story_id"])
        present_tiers = {str(ac["tier"]) for ac in raw_story["acceptance_criteria"]}
        missing_tiers = sorted(VALID_TIERS - present_tiers)
        if missing_tiers:
            warnings.append(
                f"Story {story_id} has no ACs for tier(s): {', '.join(missing_tiers)}; "
                "no placeholder checks were generated."
            )
    return warnings


def _required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SynthesisError(f"{field_name} must be a non-empty string")
    return value.strip()


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
            tier_acs = [ac for ac in raw_story["acceptance_criteria"] if ac["tier"] == tier]
            if not tier_acs:
                continue
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
            "warnings": list(warnings),
            "compatibility_story_ids": compatibility_story_ids,
            "metadata": {
                "lock_path": str(lock_path),
                "lock_hash": lock.source_fingerprint,
                "schema_hash": lock.schema.get("stable_hash"),
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
    return EvalStory(
        story_id=compatibility_story_id,
        change_id=compatibility_story_id,
        title=str(raw_story["title"]),
        description=str(raw_story["description"]),
        suite_tier=tier,  # type: ignore[arg-type]
        dataset_id=dataset_id,
        acceptance_criteria=criteria,
        metadata={
            "raw_story_id": raw_story["story_id"],
            "prompt": raw_story.get("prompt"),
            "lock_path": str(lock_path),
        },
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
        validated = validate_raw_story(story)
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
    for tier, summary in report["tiers"].items():
        print(f"{tier}: {summary['story_count']} stories, {summary['total_checks']} checks")
    for warning in report.get("warnings", []):
        print(f"warning: {warning}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = synthesize_suites(
            manifest_path=Path(args.dataset),
            output_dir=Path(args.output),
            stories_output_dir=Path(args.stories_output),
            runner=args.runner,
            model=args.model,
            agent=args.agent,
            batch_size=args.batch_size,
            ac_hints_path=Path(args.ac_hints) if args.ac_hints else None,
        )
    except (SynthesisError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
