from __future__ import annotations

import json
import logging
import re
import shutil
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class ImplReportValidationError(ValueError):
    """Raised when an implementation report does not align with its UoW spec."""


_COMMON_DOMAIN_TERMS = {
    "acceptance",
    "app",
    "apps",
    "base",
    "common",
    "criteria",
    "definition",
    "description",
    "directive",
    "component",
    "components",
    "confirm",
    "coverage",
    "engineer",
    "facade",
    "feature",
    "guard",
    "harness",
    "helper",
    "implementation",
    "lib",
    "libs",
    "modified",
    "path",
    "paths",
    "project",
    "pipe",
    "requested",
    "resolver",
    "service",
    "shared",
    "src",
    "status",
    "store",
    "test",
    "tests",
    "target",
    "testing",
    "util",
    "utils",
    "verify",
}


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _walk_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        strings: list[str] = []
        for item in value.values():
            strings.extend(_walk_strings(item))
        return strings
    if isinstance(value, list):
        strings = []
        for item in value:
            strings.extend(_walk_strings(item))
        return strings
    return []


def _normalize_domain_term(value: str) -> str | None:
    term = value.strip().strip("`'\".,:;()[]{}").lower()
    term = re.sub(r"\.(ts|tsx|js|jsx|html|scss|css|json|yaml|yml|md)$", "", term)
    if len(term) < 4 or term in _COMMON_DOMAIN_TERMS:
        return None
    if term.isdigit() or re.fullmatch(r"(?:uow|wi|ac|t)-?\d+", term):
        return None
    if not re.search(r"[a-z0-9]", term):
        return None
    return term


def _domain_terms_from_strings(strings: list[str]) -> set[str]:
    terms: set[str] = set()
    for text in strings:
        for raw_path in re.findall(r"[\w@./-]+\.[A-Za-z0-9_-]+", text):
            for segment in re.split(r"[/._]", raw_path):
                normalized = _normalize_domain_term(segment)
                if normalized:
                    terms.add(normalized)
        for raw_name in re.findall(
            r"\b[A-Z][A-Za-z0-9]*(?:Component|Service|Helper|Directive|Pipe|Module|Store|Facade|Resolver|Guard|Harness)\b",
            text,
        ):
            normalized = _normalize_domain_term(raw_name)
            if normalized:
                terms.add(normalized)
        for raw_hyphenated in re.findall(r"\b[A-Za-z0-9]+(?:-[A-Za-z0-9]+){1,}\b", text):
            normalized = _normalize_domain_term(raw_hyphenated)
            if normalized:
                terms.add(normalized)
            for segment in raw_hyphenated.split("-"):
                normalized_segment = _normalize_domain_term(segment)
                if normalized_segment:
                    terms.add(normalized_segment)
    return terms


def _impl_report_dir(agent_context_root: Path, change_id: str, uow_id: str) -> Path:
    return agent_context_root / change_id / "execution" / uow_id


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    from core.yaml_safety import load_yaml_mapping_safe

    return load_yaml_mapping_safe(path)


def _load_yaml_mapping_result(path: Path):
    from core.yaml_safety import safe_load_yaml_file

    return safe_load_yaml_file(path)


def _line_needs_quoted_yaml_scalar(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    if stripped[0] in {'"', "'", "{", "[", "|", ">"}:
        return False
    return bool(re.search(r":\s+", stripped))


def _split_unquoted_yaml_comment(value: str) -> tuple[str, str]:
    in_single = False
    in_double = False
    for index, char in enumerate(value):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip(), value[index:]
    return value.rstrip(), ""


def _quote_yaml_plain_scalar_line(line: str) -> str:
    match = re.match(r"^(\s*(?:-\s*)?[A-Za-z0-9_\"'-][^:\n]*:\s*)(.*)$", line)
    if not match:
        return line

    prefix, value = match.groups()
    value_without_comment, comment = _split_unquoted_yaml_comment(value)
    if not _line_needs_quoted_yaml_scalar(value_without_comment):
        return line

    leading = value_without_comment[: len(value_without_comment) - len(value_without_comment.lstrip())]
    scalar = value_without_comment.strip()
    quoted = json.dumps(scalar)
    suffix = f" {comment}" if comment else ""
    return f"{prefix}{leading}{quoted}{suffix}"


def _repair_impl_report_unquoted_colon_scalars(raw: str) -> str:
    return "\n".join(_quote_yaml_plain_scalar_line(line) for line in raw.split("\n"))


def normalize_impl_report_file(path: Path) -> bool:
    """Repair and canonicalize agent-authored implementation report YAML."""
    if not path.is_file():
        return False

    raw = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as original_exc:
        repaired = _repair_impl_report_unquoted_colon_scalars(raw)
        if repaired == raw:
            raise ValueError(f"impl_report.yaml is not valid YAML: {original_exc}") from original_exc
        try:
            data = yaml.safe_load(repaired)
        except yaml.YAMLError as repaired_exc:
            raise ValueError(
                "impl_report.yaml is not valid YAML after conservative scalar repair: "
                f"{repaired_exc}; original error: {original_exc}"
            ) from repaired_exc

    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError(f"impl_report.yaml must parse to a mapping, got {type(data).__name__}")

    normalized = yaml.safe_dump(data, sort_keys=False)
    if raw == normalized:
        return False
    path.write_text(normalized, encoding="utf-8")
    return True


def snapshot_impl_report_attempt(
    *,
    agent_context_root: Path,
    change_id: str,
    uow_id: str,
    attempt: int,
) -> Path | None:
    """Copy the current impl_report.yaml into an append-only attempt directory."""
    source = _impl_report_dir(agent_context_root, change_id, uow_id) / "impl_report.yaml"
    if not source.is_file():
        logger.warning(
            "snapshot_impl_report_attempt: missing impl_report.yaml change_id=%s uow_id=%s attempt=%d",
            change_id,
            uow_id,
            attempt,
        )
        return None
    destination = (
        _impl_report_dir(agent_context_root, change_id, uow_id)
        / "attempts"
        / f"attempt-{attempt:03d}"
        / "impl_report.yaml"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


def validate_impl_report_alignment(
    *,
    agent_context_root: Path,
    change_id: str,
    uow_id: str,
) -> dict[str, Any]:
    """Validate that impl_report.yaml belongs to the current UoW before evaluator use."""
    uow_dir = _impl_report_dir(agent_context_root, change_id, uow_id)
    spec_path = uow_dir / "uow_spec.yaml"
    report_path = uow_dir / "impl_report.yaml"
    spec_result = _load_yaml_mapping_result(spec_path)
    report_result = _load_yaml_mapping_result(report_path)
    spec = spec_result.data if spec_result.is_valid else {}
    report = report_result.data if report_result.is_valid else {}
    if not spec:
        detail = "; ".join(spec_result.errors or spec_result.warnings)
        raise ImplReportValidationError(f"Missing or invalid UoW spec: {spec_path}. {detail}".rstrip())
    if not report:
        detail = "; ".join(report_result.errors or report_result.warnings)
        raise ImplReportValidationError(f"Missing or invalid implementation report: {report_path}. {detail}".rstrip())

    errors: list[str] = []
    report_uow_id = report.get("uow_id")
    if report_uow_id is not None and str(report_uow_id) != uow_id:
        errors.append(f"impl_report uow_id={report_uow_id!r} does not match expected {uow_id!r}")
    report_change_id = report.get("change_id") or report.get("story_id")
    if report_change_id is not None and str(report_change_id) != change_id:
        errors.append(f"impl_report change/story id={report_change_id!r} does not match expected {change_id!r}")

    spec_terms = _domain_terms_from_strings(_walk_strings(spec))
    report_text = "\n".join(_walk_strings(report)).lower()
    matched_terms = sorted(term for term in spec_terms if term in report_text)
    warnings: list[str] = []
    if spec_terms and not matched_terms:
        sample_terms = ", ".join(sorted(spec_terms)[:12])
        errors.append(
            "impl_report domain does not match uow_spec.yaml; "
            f"none of the extracted spec terms appear in the report ({sample_terms})"
        )
    elif not spec_terms:
        warnings.append("No strong domain terms were extracted from uow_spec.yaml; domain-match check skipped.")

    payload: dict[str, Any] = {
        "change_id": change_id,
        "uow_id": uow_id,
        "validated_at": _utc_timestamp(),
        "status": "fail" if errors else "pass",
        "spec_path": str(spec_path),
        "impl_report_path": str(report_path),
        "domain_terms_checked": sorted(spec_terms)[:50],
        "matched_domain_terms": matched_terms[:50],
        "warnings": warnings,
        "errors": errors,
    }
    validation_path = uow_dir / "impl_report_validation.yaml"
    try:
        validation_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    except OSError as exc:
        logger.warning("validate_impl_report_alignment: could not write %s: %s", validation_path, exc)
    if errors:
        raise ImplReportValidationError("; ".join(errors))
    return payload


def extract_batches_from_duplicate_yaml(raw: str) -> list[dict[str, Any]]:
    """Extract batch blocks from YAML where batches/execution_schedule has duplicate batch keys."""
    match = re.search(r"^(?:batches|execution_schedule):\s*\n((?:[ \t]+.*\n?)*)", raw, re.MULTILINE)
    if not match:
        return []
    block = match.group(1)
    segments = re.split(r"(?=^[ \t]+(?:batch|batch_id):\s*\d)", block, flags=re.MULTILINE)
    batches: list[dict[str, Any]] = []
    for segment in segments:
        if not segment.strip():
            continue
        try:
            parsed = yaml.safe_load(textwrap.dedent(segment))
        except Exception as exc:  # pragma: no cover - defensive logging path
            logger.warning("extract_batches_from_duplicate_yaml: could not parse segment: %s", exc)
            continue
        if isinstance(parsed, dict) and ("batch" in parsed or "batch_id" in parsed):
            batches.append(parsed)
    return batches


def _normalize_batch(batch: dict[str, Any]) -> dict[str, Any]:
    """Normalize a batch dict to use canonical 'batch_id' key."""
    if "batch_id" not in batch and "batch" in batch:
        batch["batch_id"] = batch.pop("batch")
    return batch


def _extract_json_block(raw: str) -> str | None:
    """Try to extract a JSON object from markdown fences or surrounding text."""
    # Strip markdown code fences
    m = re.search(r"```(?:json)?\s*\n(.*?)```", raw, re.DOTALL)
    if m:
        candidate = m.group(1).strip()
        # Remove stray chars (like >) between quoted values and structural tokens
        candidate = re.sub(r'(?<=")\s*>[^"\n{}\[\],]*(?=\s*[},\]])', '', candidate)
        return candidate
    # Find first { … last }
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        candidate = raw[start : end + 1]
        candidate = re.sub(r'(?<=")\s*>[^"\n{}\[\],]*(?=\s*[},\]])', '', candidate)
        return candidate
    return None


def _repair_embedded_quotes(text: str) -> str:
    """Fix unescaped double quotes inside JSON string values.

    Handles the common LLM output pattern where double-quoted terms like
    ``\"PR-001\"`` appear inside JSON string values, breaking both JSON
    and YAML parsers.  Replaces inner ``\"text\"`` with ``'text'`` on
    each line that looks like a JSON key-value pair with a string value.
    """
    lines: list[str] = text.split("\n")
    repaired: list[str] = []
    for line in lines:
        # Match "key": " prefix
        m = re.match(r'^(\s*"[^"]*"\s*:\s*)"', line)
        if not m:
            repaired.append(line)
            continue

        prefix = m.group(0)  # includes opening quote of value
        rest = line[m.end() :]

        # Closing quote is the last " followed by optional whitespace, optional comma
        close_m = re.search(r'"(\s*,?\s*)$', rest)
        if not close_m:
            repaired.append(line)
            continue

        inner = rest[: close_m.start()]
        suffix = rest[close_m.start() :]  # closing " and any trailing chars

        # Replace "text" patterns with 'text' inside the value
        inner = re.sub(r'"([^"]*?)"', r"'\1'", inner)
        repaired.append(prefix + inner + suffix)

    return "\n".join(repaired)


def parse_assignments_text(raw: str) -> dict[str, Any]:
    """Parse assignments content that may be strict JSON or legacy YAML.

    Normalises to the canonical shape: top-level ``batches`` list with
    ``batch_id`` keys on each batch dict, regardless of whether the input
    uses the legacy ``execution_schedule`` / ``batch`` shape.
    """
    # Try strict JSON first (no repair – valid content shouldn't need it)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Try repairing common agent quoting errors, then retry JSON
        repaired = _repair_embedded_quotes(raw)
        try:
            data = json.loads(repaired)
        except json.JSONDecodeError:
            # Try extracting a JSON block from surrounding prose/fences
            extracted = _extract_json_block(raw)
            if extracted:
                try:
                    data = json.loads(extracted)
                except json.JSONDecodeError:
                    repaired_extracted = _repair_embedded_quotes(extracted)
                    try:
                        data = json.loads(repaired_extracted)
                    except json.JSONDecodeError:
                        data = None
            else:
                data = None

            # Fall back to YAML only if JSON extraction failed
            if data is None:
                try:
                    data = yaml.safe_load(repaired)
                except yaml.YAMLError as exc:
                    raise ValueError(
                        f"assignments artifact is neither valid JSON nor valid YAML: {exc}"
                    ) from exc
    if not isinstance(data, dict):
        raise ValueError("assignments artifact must parse to a mapping")

    # Normalise top-level key: legacy 'execution_schedule' → canonical 'batches'
    raw_batches = data.get("batches") or data.get("execution_schedule", [])
    if isinstance(raw_batches, dict):
        extracted = extract_batches_from_duplicate_yaml(raw)
        if extracted:
            raw_batches = extracted
        else:
            raw_batches = [raw_batches]
            index = 2
            while f"batch_{index}" in data:
                raw_batches.append(data[f"batch_{index}"])
                index += 1

    data["batches"] = [_normalize_batch(b) for b in raw_batches]
    data.pop("execution_schedule", None)
    return data


def load_assignments_file(path: Path) -> dict[str, Any]:
    """Load and parse assignments, sanitizing malformed content in place first."""
    raw = path.read_text(encoding="utf-8")
    try:
        return parse_assignments_text(raw)
    except (ValueError, yaml.YAMLError):
        # Attempt in-place sanitization before giving up
        sanitized = _sanitize_json_content(raw)
        if sanitized and sanitized != raw:
            logger.warning("load_assignments_file: sanitized malformed content in %s", path)
            path.write_text(sanitized, encoding="utf-8")
            return parse_assignments_text(sanitized)
        raise


def _sanitize_json_content(raw: str) -> str | None:
    """Try to extract and re-serialize valid JSON from potentially malformed content."""
    # Strip markdown fences
    stripped = raw
    m = re.search(r"```(?:json|yaml|yml)?\s*\n(.*?)```", raw, re.DOTALL)
    if m:
        stripped = m.group(1).strip()

    # Remove stray characters between quoted values and structural JSON chars
    # e.g. "value"> }  →  "value" }
    cleaned = re.sub(r'(?<=")\s*>[^"\n{}\[\],]*(?=\s*[},\]])', '', stripped)

    # Try trailing comma fix
    for candidate in [cleaned, stripped]:
        fixed = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            data = json.loads(fixed)
            return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        except json.JSONDecodeError:
            pass

    # Extract outermost braces
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        candidate = re.sub(r",\s*([}\]])", r"\1", cleaned[start : end + 1])
        try:
            data = json.loads(candidate)
            return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        except json.JSONDecodeError:
            pass

    return None


def normalize_assignments_file(path: Path) -> bool:
    """Rewrite assignments artifacts to canonical JSON when parsing succeeds."""
    if not path.is_file():
        return False
    raw = path.read_text(encoding="utf-8")
    data = parse_assignments_text(raw)
    normalized = json.dumps(data, indent=2, sort_keys=False) + "\n"
    if raw == normalized:
        return False
    path.write_text(normalized, encoding="utf-8")
    return True
