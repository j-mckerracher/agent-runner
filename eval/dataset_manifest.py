"""Dataset manifest parsing and validation helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Union

from .models import DatasetLock, DatasetManifest
from .yaml_io import dump_yaml, load_yaml_mapping

PathLike = Union[str, Path]


class ManifestValidationError(ValueError):
    """Raised when an eval dataset manifest is incomplete or invalid."""


def parse_dataset_manifest(data: Mapping[str, Any]) -> DatasetManifest:
    try:
        manifest = DatasetManifest.from_dict(data)
    except (KeyError, TypeError, ValueError) as exc:
        raise ManifestValidationError(f"Invalid dataset manifest: {exc}") from exc

    source_type = manifest.source.get("type")
    if not isinstance(source_type, str) or not source_type.strip():
        raise ManifestValidationError("dataset manifest source.type must be a non-empty string")
    source_type = source_type.strip()
    if source_type not in {"csv", "jsonl", "sqlite", "parquet", "postgres", "s3_glob"}:
        raise ManifestValidationError(
            "dataset manifest source.type must be one of: csv, jsonl, parquet, postgres, s3_glob, sqlite"
        )
    _validate_source_settings(source_type, manifest.source)

    strategy = manifest.sampling.get("strategy")
    if not isinstance(strategy, str) or not strategy.strip():
        raise ManifestValidationError("dataset manifest sampling.strategy must be a non-empty string")
    strategy = strategy.strip()
    if strategy not in {"head", "manual", "random", "stratified"}:
        raise ManifestValidationError(
            "dataset manifest sampling.strategy must be one of: head, manual, random, stratified"
        )

    sample_size = manifest.sampling.get("sample_size")
    if not isinstance(sample_size, int) or sample_size <= 0:
        raise ManifestValidationError("dataset manifest sampling.sample_size must be a positive integer")

    seed = manifest.sampling.get("seed")
    if not isinstance(seed, int):
        raise ManifestValidationError("dataset manifest sampling.seed must be an integer")

    if strategy == "stratified":
        field = manifest.sampling.get("stratify_by") or manifest.sampling.get("strata_field")
        if not isinstance(field, str) or not field.strip():
            raise ManifestValidationError(
                "dataset manifest sampling.stratify_by must be a non-empty string for stratified sampling"
            )

    if strategy == "manual" and not _first_present(
        manifest.sampling,
        ("indexes", "record_indexes", "manual_indexes", "ids", "record_ids", "manual_ids"),
    ):
        raise ManifestValidationError(
            "dataset manifest manual sampling requires explicit indexes/record_indexes or ids/record_ids"
        )

    return manifest


def _first_present(data: Mapping[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in data:
            return data[key]
    return None


def _validate_source_settings(source_type: str, source: Mapping[str, Any]) -> None:
    if source_type in {"csv", "jsonl", "parquet"}:
        path = source.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ManifestValidationError(f"{source_type} source requires a non-empty source.path")
        return

    if source_type == "sqlite":
        path = source.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ManifestValidationError("sqlite source requires a non-empty source.path")
        has_table = isinstance(source.get("table"), str) and bool(source.get("table", "").strip())
        has_query = isinstance(source.get("query"), str) and bool(source.get("query", "").strip())
        if has_table == has_query:
            raise ManifestValidationError("sqlite source requires exactly one of source.table or source.query")
        return

    if source_type == "postgres":
        has_connection = any(
            isinstance(source.get(key), str) and bool(source.get(key, "").strip())
            for key in ("connection_string", "dsn", "url")
        )
        has_query = isinstance(source.get("query"), str) and bool(source.get("query", "").strip())
        if not has_connection or not has_query:
            raise ManifestValidationError(
                "postgres source requires source.connection_string (or dsn/url) and source.query"
            )
        return

    if source_type == "s3_glob":
        has_uri = isinstance(source.get("uri"), str) and bool(source.get("uri", "").strip())
        has_pattern = isinstance(source.get("pattern"), str) and bool(source.get("pattern", "").strip())
        if not (has_uri or has_pattern):
            raise ManifestValidationError("s3_glob source requires source.uri or source.pattern")


def load_dataset_manifest(path: PathLike) -> DatasetManifest:
    return parse_dataset_manifest(load_yaml_mapping(path))


def load_dataset_lock(path: PathLike) -> DatasetLock:
    try:
        return DatasetLock.from_dict(load_yaml_mapping(path))
    except (KeyError, TypeError, ValueError) as exc:
        raise ManifestValidationError(f"Invalid dataset lock: {exc}") from exc


def dump_dataset_manifest(manifest: DatasetManifest, path: PathLike) -> None:
    dump_yaml(manifest.to_dict(), path)


def dump_dataset_lock(lock: DatasetLock, path: PathLike) -> None:
    dump_yaml(lock.to_dict(), path)


def stable_manifest_hash(data: Mapping[str, Any]) -> str:
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
