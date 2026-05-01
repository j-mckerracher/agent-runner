"""Source readers for eval dataset initialization.

The stdlib-backed readers (CSV, JSONL, SQLite) are fully implemented here.
Connectors that need heavy optional dependencies fail with actionable errors
instead of silently returning incomplete samples.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

PathLike = Union[str, Path]


class DatasetSourceError(RuntimeError):
    """Raised when a dataset source cannot be read."""


@dataclass(frozen=True)
class SourceRecords:
    records: Sequence[Mapping[str, Any]]
    metadata: Mapping[str, Any] = field(default_factory=dict)


def read_source(source: Mapping[str, Any], manifest_path: PathLike) -> SourceRecords:
    source_type = str(source.get("type", "")).strip()
    if source_type == "csv":
        return read_csv_source(source, manifest_path)
    if source_type == "jsonl":
        return read_jsonl_source(source, manifest_path)
    if source_type == "sqlite":
        return read_sqlite_source(source, manifest_path)
    if source_type == "parquet":
        raise DatasetSourceError(
            "parquet sources require optional dependencies that are not installed by default. "
            "Install pyarrow or pandas in your environment, or convert the dataset to csv/jsonl."
        )
    if source_type == "postgres":
        raise DatasetSourceError(
            "postgres sources require an optional database driver such as psycopg. "
            "Install psycopg and rerun, or export the query results to csv/jsonl for deterministic local tests."
        )
    if source_type == "s3_glob":
        raise DatasetSourceError(
            "s3_glob sources require optional S3 dependencies such as boto3/s3fs. "
            "Install the needed connector package, or mirror the files locally and use csv/jsonl."
        )
    raise DatasetSourceError(f"Unsupported dataset source.type: {source_type}")


def resolve_source_path(source: Mapping[str, Any], manifest_path: PathLike) -> Path:
    raw_path = source.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise DatasetSourceError("source.path must be a non-empty string")
    path = Path(raw_path)
    if not path.is_absolute():
        path = Path(manifest_path).resolve().parent / path
    return path


def read_csv_source(source: Mapping[str, Any], manifest_path: PathLike) -> SourceRecords:
    path = resolve_source_path(source, manifest_path)
    try:
        with path.open("r", encoding=str(source.get("encoding", "utf-8")), newline="") as handle:
            reader = csv.DictReader(handle)
            records = [dict(row) for row in reader]
            fields = list(reader.fieldnames or [])
    except FileNotFoundError as exc:
        raise DatasetSourceError(f"CSV source not found: {path}") from exc
    except csv.Error as exc:
        raise DatasetSourceError(f"Invalid CSV source {path}: {exc}") from exc

    return SourceRecords(records=records, metadata=_file_metadata(path, {"fields": fields}))


def read_jsonl_source(source: Mapping[str, Any], manifest_path: PathLike) -> SourceRecords:
    path = resolve_source_path(source, manifest_path)
    records: List[Mapping[str, Any]] = []
    try:
        with path.open("r", encoding=str(source.get("encoding", "utf-8"))) as handle:
            for line_number, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    raise DatasetSourceError(f"Invalid JSON on line {line_number} of {path}: {exc}") from exc
                if not isinstance(record, Mapping):
                    raise DatasetSourceError(f"JSONL line {line_number} of {path} must be a JSON object")
                records.append(dict(record))
    except FileNotFoundError as exc:
        raise DatasetSourceError(f"JSONL source not found: {path}") from exc

    return SourceRecords(records=records, metadata=_file_metadata(path))


def read_sqlite_source(source: Mapping[str, Any], manifest_path: PathLike) -> SourceRecords:
    path = resolve_source_path(source, manifest_path)
    query = source.get("query")
    table = source.get("table")
    if query and table:
        raise DatasetSourceError("SQLite source must provide only one of source.query or source.table")
    if table:
        query = f"SELECT * FROM {_quote_sqlite_identifier(str(table))}"
    if not isinstance(query, str) or not query.strip():
        raise DatasetSourceError("SQLite source requires source.table or source.query")

    try:
        with sqlite3.connect(path) as connection:
            connection.row_factory = sqlite3.Row
            cursor = connection.execute(query)
            rows = cursor.fetchall()
            records = [dict(row) for row in rows]
            fields = [description[0] for description in cursor.description or []]
    except sqlite3.Error as exc:
        raise DatasetSourceError(f"Could not read SQLite source {path}: {exc}") from exc

    metadata = _file_metadata(path, {"query": query, "fields": fields})
    return SourceRecords(records=records, metadata=metadata)


def _quote_sqlite_identifier(identifier: str) -> str:
    if not identifier.strip():
        raise DatasetSourceError("SQLite table name must be non-empty")
    return '"' + identifier.replace('"', '""') + '"'


def _file_metadata(path: Path, extra: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    stat = path.stat()
    metadata: Dict[str, Any] = {
        "path": str(path),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }
    if extra:
        metadata.update(dict(extra))
    return metadata
