"""Source readers for eval dataset initialization.

The stdlib-backed readers (CSV, JSONL, SQLite) are fully implemented here.
Connectors that need heavy optional dependencies fail with actionable errors
instead of silently returning incomplete samples.
"""

from __future__ import annotations

import fnmatch
import csv
import json
import sqlite3
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

PathLike = Union[str, Path]
DEFAULT_CODE_EXTENSIONS = (
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".css",
    ".go",
    ".h",
    ".hpp",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".kts",
    ".mjs",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".scala",
    ".swift",
    ".ts",
    ".tsx",
)


class DatasetSourceError(RuntimeError):
    """Raised when a dataset source cannot be read."""


@dataclass(frozen=True)
class SourceRecords:
    records: Sequence[Mapping[str, Any]]
    metadata: Mapping[str, Any] = field(default_factory=dict)


def read_source(source: Mapping[str, Any], manifest_path: PathLike) -> SourceRecords:
    source_type = str(source.get("type", "")).strip()
    if source_type == "code_repository":
        return read_code_repository_source(source, manifest_path)
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
    path = Path(raw_path).expanduser()
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


def read_code_repository_source(source: Mapping[str, Any], manifest_path: PathLike) -> SourceRecords:
    root = resolve_source_path(source, manifest_path)
    if not root.exists():
        raise DatasetSourceError(f"code_repository source not found: {root}")
    if not root.is_dir():
        raise DatasetSourceError(f"code_repository source.path must be a directory: {root}")

    include_extensions = _normalize_code_extensions(source.get("include_extensions"))
    exclude_patterns = _normalize_exclude_patterns(source.get("exclude_patterns"))
    layer_map = {
        str(key).strip(): str(value).strip()
        for key, value in dict(source.get("layer_map") or {}).items()
        if str(key).strip() and str(value).strip()
    }

    records: List[Mapping[str, Any]] = []
    layer_distribution: Counter[str] = Counter()
    total_files = 0

    for file_path in sorted(path for path in root.rglob("*") if path.is_file()):
        rel_path = file_path.relative_to(root)
        rel_posix = rel_path.as_posix()
        if _should_skip_code_file(rel_posix, include_extensions=include_extensions, exclude_patterns=exclude_patterns):
            continue
        total_files += 1
        project, sub_path = _split_project_and_sub_path(rel_path)
        layer = _infer_layer(project, layer_map=layer_map)
        stat = file_path.stat()
        layer_distribution[layer] += 1
        records.append(
            {
                "file": rel_posix,
                "filename": file_path.name,
                "layer": layer,
                "project": project,
                "size_bytes": stat.st_size,
                "sub_path": sub_path,
            }
        )

    metadata: Dict[str, Any] = {
        "path": str(root),
        "total_files": total_files,
        "include_extensions": list(include_extensions),
        "exclude_patterns": list(exclude_patterns),
        "layer_distribution": dict(sorted(layer_distribution.items())),
    }
    return SourceRecords(records=records, metadata=metadata)


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


def _normalize_code_extensions(value: Any) -> tuple[str, ...]:
    if value is None:
        return DEFAULT_CODE_EXTENSIONS
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise DatasetSourceError("code_repository source.include_extensions must be a sequence of file extensions")
    normalized: List[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise DatasetSourceError("code_repository source.include_extensions must contain non-empty strings")
        extension = item.strip()
        if not extension.startswith("."):
            extension = f".{extension}"
        normalized.append(extension)
    return tuple(normalized)


def _normalize_exclude_patterns(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise DatasetSourceError("code_repository source.exclude_patterns must be a sequence of patterns")
    patterns: List[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise DatasetSourceError("code_repository source.exclude_patterns must contain non-empty strings")
        patterns.append(item.strip())
    return tuple(patterns)


def _should_skip_code_file(
    rel_posix: str,
    *,
    include_extensions: Sequence[str],
    exclude_patterns: Sequence[str],
) -> bool:
    if not any(rel_posix.endswith(extension) for extension in include_extensions):
        return True
    candidate = f"/{rel_posix}"
    for pattern in exclude_patterns:
        if _matches_exclude_pattern(candidate, rel_posix, pattern):
            return True
    return False


def _matches_exclude_pattern(candidate: str, rel_posix: str, pattern: str) -> bool:
    if any(char in pattern for char in "*?[]"):
        trimmed = pattern.lstrip("/")
        return fnmatch.fnmatch(rel_posix, trimmed) or fnmatch.fnmatch(candidate, pattern)
    if "/" in pattern:
        return pattern in candidate
    return rel_posix.endswith(pattern)


def _split_project_and_sub_path(rel_path: Path) -> tuple[str, str]:
    parts = rel_path.parts
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], Path(*parts[1:]).as_posix()


def _infer_layer(project: str, *, layer_map: Mapping[str, str]) -> str:
    if project in layer_map:
        return layer_map[project]

    normalized = project.lower()
    if "test" in normalized and "integration" in normalized:
        return "tests_integration"
    if "test" in normalized:
        return "tests"
    if "api" in normalized and "model" in normalized:
        return "api_model"
    if "api" in normalized or "controller" in normalized:
        return "api"
    if any(token in normalized for token in ("model", "entity", "dto")):
        return "model"
    if any(token in normalized for token in ("dal", "data", "repository")):
        return "data_access"
    if any(token in normalized for token in ("bll", "service", "business")):
        return "business_logic"
    if any(token in normalized for token in ("ui", "web", "frontend")):
        return "presentation"
    sanitized = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    return sanitized or "root"


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
