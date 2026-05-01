"""Initialize deterministic eval dataset samples and lock files."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

if __package__ in {None, ""}:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from eval.dataset_manifest import dump_dataset_lock, load_dataset_manifest, stable_manifest_hash
    from eval.dataset_sources import DatasetSourceError, read_source
    from eval.models import DatasetLock, DatasetManifest
else:
    from .dataset_manifest import dump_dataset_lock, load_dataset_manifest, stable_manifest_hash
    from .dataset_sources import DatasetSourceError, read_source
    from .models import DatasetLock, DatasetManifest


class DatasetInitError(RuntimeError):
    """Raised when dataset initialization fails."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Initialize an eval dataset sample and lock file.")
    parser.add_argument("--dataset", required=True, help="Path to eval/datasets/<dataset>.yaml")
    return parser


def initialize_dataset(manifest_path: Path) -> Tuple[DatasetLock, Mapping[str, Any]]:
    manifest = load_dataset_manifest(manifest_path)
    source_records = read_source(manifest.source, manifest_path)
    records = [dict(record) for record in source_records.records]
    sampled_records, sample_metadata = sample_records(records, manifest)

    datasets_dir = manifest_path.resolve().parent
    samples_dir = datasets_dir / "samples"
    sample_path = samples_dir / f"{manifest.dataset_id}_sample.jsonl"
    lock_path = datasets_dir / f"{manifest.dataset_id}.lock"

    write_jsonl(sampled_records, sample_path)
    schema = infer_schema(
        records=sampled_records,
        source_metadata=source_records.metadata,
        manifest=manifest,
        sample_metadata=sample_metadata,
    )
    lock_payload = {
        "dataset_id": manifest.dataset_id,
        "sample_path": str(sample_path),
        "record_count": len(sampled_records),
        "schema": schema,
        "seed": manifest.sampling.get("seed"),
        "metadata": {
            "display_name": manifest.display_name,
            "source_type": manifest.source.get("type"),
            "sampling_strategy": manifest.sampling.get("strategy"),
            "domain_context": manifest.domain_context,
            "manifest_hash": stable_manifest_hash(manifest.to_dict()),
        },
    }
    source_fingerprint = stable_manifest_hash(lock_payload)
    lock = DatasetLock(source_fingerprint=source_fingerprint, **lock_payload)
    dump_dataset_lock(lock, lock_path)

    summary = {
        "sample_count": len(sampled_records),
        "fields": schema["fields"],
        "source_type": manifest.source.get("type"),
        "sampling_strategy": manifest.sampling.get("strategy"),
        "stratum_distribution": sample_metadata.get("stratum_distribution", {}),
        "sample_path": str(sample_path),
        "lock_path": str(lock_path),
    }
    return lock, summary


def sample_records(records: Sequence[Mapping[str, Any]], manifest: DatasetManifest) -> Tuple[List[Mapping[str, Any]], Dict[str, Any]]:
    strategy = str(manifest.sampling["strategy"])
    sample_size = int(manifest.sampling["sample_size"])
    seed = int(manifest.sampling["seed"])
    if sample_size > len(records) and strategy != "manual":
        raise DatasetInitError(f"sampling.sample_size ({sample_size}) exceeds source record count ({len(records)})")

    if strategy == "head":
        sample = list(records[:sample_size])
        return sample, {"strategy": strategy}

    if strategy == "random":
        rng = random.Random(seed)
        indexes = rng.sample(range(len(records)), sample_size)
        return [records[index] for index in indexes], {"strategy": strategy, "indexes": indexes}

    if strategy == "stratified":
        return _sample_stratified(records, manifest, sample_size, seed)

    if strategy == "manual":
        return _sample_manual(records, manifest, sample_size)

    raise DatasetInitError(f"Unsupported sampling.strategy: {strategy}")


def _sample_stratified(
    records: Sequence[Mapping[str, Any]], manifest: DatasetManifest, sample_size: int, seed: int
) -> Tuple[List[Mapping[str, Any]], Dict[str, Any]]:
    field = str(manifest.sampling.get("stratify_by") or manifest.sampling.get("strata_field"))
    groups: Dict[str, List[int]] = defaultdict(list)
    for index, record in enumerate(records):
        if field not in record:
            raise DatasetInitError(f"stratified sampling field '{field}' is missing from record index {index}")
        groups[str(record.get(field))].append(index)
    if not groups:
        raise DatasetInitError("stratified sampling cannot sample an empty source")

    total = len(records)
    quotas: Dict[str, int] = {}
    remainders: List[Tuple[float, str]] = []
    for stratum, indexes in groups.items():
        exact = sample_size * (len(indexes) / total)
        base = min(len(indexes), int(exact))
        quotas[stratum] = base
        remainders.append((exact - base, stratum))

    remaining = sample_size - sum(quotas.values())
    for _, stratum in sorted(remainders, key=lambda item: (-item[0], item[1])):
        if remaining <= 0:
            break
        if quotas[stratum] < len(groups[stratum]):
            quotas[stratum] += 1
            remaining -= 1

    rng = random.Random(seed)
    selected: List[int] = []
    for stratum in sorted(groups):
        shuffled = list(groups[stratum])
        rng.shuffle(shuffled)
        selected.extend(shuffled[: quotas[stratum]])

    if len(selected) < sample_size:
        selected_set = set(selected)
        leftovers = [index for index in range(len(records)) if index not in selected_set]
        rng.shuffle(leftovers)
        selected.extend(leftovers[: sample_size - len(selected)])

    distribution = Counter(str(records[index].get(field)) for index in selected)
    return [records[index] for index in selected], {
        "strategy": "stratified",
        "stratify_by": field,
        "stratum_distribution": dict(sorted(distribution.items())),
        "indexes": selected,
    }


def _sample_manual(
    records: Sequence[Mapping[str, Any]], manifest: DatasetManifest, sample_size: int
) -> Tuple[List[Mapping[str, Any]], Dict[str, Any]]:
    sampling = manifest.sampling
    indexes = _first_sampling_value(sampling, ("indexes", "record_indexes", "manual_indexes"))
    ids = _first_sampling_value(sampling, ("ids", "record_ids", "manual_ids"))
    if indexes is not None and ids is not None:
        raise DatasetInitError("manual sampling must provide indexes or ids, not both")

    if indexes is not None:
        if not isinstance(indexes, Sequence) or isinstance(indexes, (str, bytes)):
            raise DatasetInitError("manual sampling indexes must be a sequence of zero-based integers")
        selected_indexes = []
        for raw_index in indexes:
            if not isinstance(raw_index, int):
                raise DatasetInitError("manual sampling indexes must be zero-based integers")
            if raw_index < 0 or raw_index >= len(records):
                raise DatasetInitError(f"manual sampling index out of range: {raw_index}")
            selected_indexes.append(raw_index)
        if len(selected_indexes) != sample_size:
            raise DatasetInitError("manual sampling index count must match sampling.sample_size")
        return [records[index] for index in selected_indexes], {"strategy": "manual", "indexes": selected_indexes}

    if ids is None:
        raise DatasetInitError("manual sampling requires indexes or ids")
    if not isinstance(ids, Sequence) or isinstance(ids, (str, bytes)):
        raise DatasetInitError("manual sampling ids must be a sequence")
    id_field = str(sampling.get("id_field", "id"))
    by_id = {str(record.get(id_field)): record for record in records if id_field in record}
    missing = [str(record_id) for record_id in ids if str(record_id) not in by_id]
    if missing:
        raise DatasetInitError(f"manual sampling ids not found in field '{id_field}': {', '.join(missing)}")
    if len(ids) != sample_size:
        raise DatasetInitError("manual sampling id count must match sampling.sample_size")
    return [by_id[str(record_id)] for record_id in ids], {
        "strategy": "manual",
        "id_field": id_field,
        "ids": [str(record_id) for record_id in ids],
    }


def _first_sampling_value(sampling: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in sampling:
            return sampling[key]
    return None


def write_jsonl(records: Sequence[Mapping[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(dict(record), sort_keys=True, default=str) + "\n")


def infer_schema(
    records: Sequence[Mapping[str, Any]],
    source_metadata: Mapping[str, Any],
    manifest: DatasetManifest,
    sample_metadata: Mapping[str, Any],
) -> Mapping[str, Any]:
    field_names = sorted({field for record in records for field in record.keys()})
    types: Dict[str, List[str]] = {}
    nullable_fields: List[str] = []
    for field in field_names:
        normalized_types = sorted({_normalize_type(record.get(field)) for record in records if record.get(field) is not None})
        types[field] = normalized_types or ["null"]
        if any(field not in record or record.get(field) is None for record in records):
            nullable_fields.append(field)

    fingerprint_payload = {
        "fields": field_names,
        "types": types,
        "nullable_fields": sorted(nullable_fields),
        "record_count": len(records),
        "source_metadata": dict(source_metadata),
        "sample_seed": manifest.sampling.get("seed"),
        "sampling_strategy": manifest.sampling.get("strategy"),
        "sample_metadata": dict(sample_metadata),
    }
    return {
        **fingerprint_payload,
        "stable_hash": hashlib.sha256(
            json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest(),
    }


def _normalize_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return "array"
    return type(value).__name__


def print_summary(summary: Mapping[str, Any]) -> None:
    print(f"Initialized dataset sample: {summary['sample_count']} records")
    print(f"Fields: {', '.join(summary['fields'])}")
    print(f"Source type: {summary['source_type']}")
    print(f"Sampling strategy: {summary['sampling_strategy']}")
    if summary.get("stratum_distribution"):
        print(f"Stratum distribution: {json.dumps(summary['stratum_distribution'], sort_keys=True)}")
    print(f"Sample path: {summary['sample_path']}")
    print(f"Lock path: {summary['lock_path']}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _, summary = initialize_dataset(Path(args.dataset))
    except (DatasetInitError, DatasetSourceError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
