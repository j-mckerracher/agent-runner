#!/usr/bin/env python3
"""Sanitize an artifact file in place.

Forces agent-written artifacts into valid JSON or YAML. Handles common
problems seen in practice:

- Markdown code fences wrapping the JSON/YAML content
- Prose/commentary before or after the data block
- HTML entities or stray characters (>, <, &) inside string values
- Trailing commas in JSON
- Mixed content that's *almost* valid

Usage:
    sanitize-artifact.py <file_path>

Exit codes:
    0 = file was already valid or was successfully sanitized
    1 = file could not be sanitized (left unchanged)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences, returning inner content."""
    # ```json ... ``` or ```yaml ... ``` or bare ```
    m = re.search(r"```(?:json|yaml|yml)?\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


def extract_json_object(text: str) -> str | None:
    """Find the outermost { ... } block in text."""
    start = text.find("{")
    if start == -1:
        return None
    # Walk forward tracking brace depth, ignoring braces inside strings
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    # Unbalanced — return from start to last }
    end = text.rfind("}")
    if end > start:
        return text[start : end + 1]
    return None


def fix_trailing_commas(text: str) -> str:
    """Remove trailing commas before } or ]."""
    return re.sub(r",\s*([}\]])", r"\1", text)


def fix_stray_chars(text: str) -> str:
    """Remove stray characters (like >) between quoted values and structural JSON tokens."""
    return re.sub(r'(?<=")\s*>[^"\n{}\[\],]*(?=\s*[},\]])', '', text)


def try_parse_json(text: str) -> dict | list | None:
    """Attempt JSON parsing with progressive fixups."""
    for candidate in [text, fix_stray_chars(text), fix_trailing_commas(fix_stray_chars(text))]:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def try_parse_yaml(text: str):
    """Attempt YAML parsing."""
    if yaml is None:
        return None
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return None


# ---------------------------------------------------------------------------
# Main sanitization logic
# ---------------------------------------------------------------------------

def sanitize(path: Path) -> bool:
    """Sanitize a file in place. Returns True if the file was modified."""
    raw = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()

    # Step 1: Try parsing as-is
    if suffix == ".json":
        data = try_parse_json(raw)
        if data is not None:
            return False  # Already valid
    elif suffix in (".yaml", ".yml"):
        data = try_parse_yaml(raw)
        if data is not None and isinstance(data, (dict, list)):
            return False  # Already valid
        data = None  # will attempt fixups below
    else:
        data = try_parse_json(raw)
        if data is not None:
            return False

    # Step 2: Strip markdown fences
    stripped = strip_markdown_fences(raw)
    if stripped != raw:
        if suffix == ".json":
            data = try_parse_json(stripped)
        elif suffix in (".yaml", ".yml"):
            data = try_parse_yaml(stripped)
        else:
            data = try_parse_json(stripped) or try_parse_yaml(stripped)

    # Step 3: Extract JSON object from surrounding prose
    if data is None and suffix == ".json":
        extracted = extract_json_object(stripped or raw)
        if extracted:
            data = try_parse_json(extracted)

    # Step 4: For YAML files, try stripping to just the YAML document
    if data is None and suffix in (".yaml", ".yml"):
        # Try removing leading prose lines (anything before the first key: or ---)
        lines = (stripped or raw).splitlines()
        for i, line in enumerate(lines):
            if line.strip() == "---" or re.match(r"^[a-zA-Z_][\w]*:", line):
                candidate = "\n".join(lines[i:])
                data = try_parse_yaml(candidate)
                if data is not None and isinstance(data, (dict, list)):
                    break
                data = None

    if data is None:
        # Cannot salvage — leave file untouched
        sys.stderr.write(f"sanitize-artifact: UNABLE to fix {path}\n")
        return False

    # Write back in canonical format
    if suffix == ".json":
        canonical = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    elif suffix in (".yaml", ".yml"):
        if yaml is None:
            # No yaml module — write as JSON since it's a superset
            canonical = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        else:
            canonical = yaml.dump(
                data,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
                width=120,
            )
    else:
        canonical = json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    if canonical == raw:
        return False

    path.write_text(canonical, encoding="utf-8")
    sys.stderr.write(f"sanitize-artifact: FIXED {path}\n")
    return True


def main():
    if len(sys.argv) < 2:
        sys.stderr.write("Usage: sanitize-artifact.py <file_path>\n")
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.is_file():
        sys.stderr.write(f"sanitize-artifact: file not found: {path}\n")
        sys.exit(1)

    try:
        sanitize(path)
    except Exception as exc:
        sys.stderr.write(f"sanitize-artifact: error processing {path}: {exc}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()


