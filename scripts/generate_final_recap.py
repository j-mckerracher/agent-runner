#!/usr/bin/env python3
"""Generate concise, observable final recaps from Tolaria/session JSON logs.
Produces a small JSON with keys: outcome, changed_files, commands_tests, decisions_rationale, open_loops.
Redacts probable secrets and truncates very long text/diffs.
"""
import argparse
import json
import os
import re
from typing import Any, Dict, List

REDACT_PATTERNS = [
    re.compile(r"(?i)(password|pwd|secret|token|api[_-]?key|access[_-]?key|secret[_-]?key)[\s:=]*[^\s,\n\"]+"),
]

TRUNCATE_LEN = 400


def redact(s: str) -> str:
    if not isinstance(s, str):
        return s
    # Remove binary-like or diff content
    if "diff --git" in s or s.count('\n') > 6:
        s = s.split('\n')[:6]
        s = "\n".join(s) + "\n... (truncated)"
    # Apply redaction patterns
    for pat in REDACT_PATTERNS:
        s = pat.sub(lambda m: f"{m.group(1)}:<REDACTED>", s)
    # Truncate very long single-line strings
    if len(s) > TRUNCATE_LEN:
        s = s[: TRUNCATE_LEN - 13] + " ... (truncated)"
    return s


def ensure_list(x: Any) -> List[str]:
    if x is None:
        return []
    if isinstance(x, list):
        return [str(redact(i)) for i in x]
    if isinstance(x, str):
        return [str(redact(x))]
    # For other objects, stringify safe
    return [redact(json.dumps(x))]


def pick_first_available(d: Dict, keys: List[str]):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def generate_concise_recap(session: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "outcome": "",
        "changed_files": [],
        "commands_tests": [],
        "decisions_rationale": [],
        "open_loops": [],
    }

    fr = session.get("final_recap") or {}

    # Outcome
    outcome = pick_first_available(fr, ["outcome"]) or pick_first_available(session, ["outcome", "status"]) or ""
    out["outcome"] = redact(str(outcome)) if outcome is not None else ""

    # Changed files
    changed = pick_first_available(fr, ["changed_files", "changed_files_list"]) or pick_first_available(
        session, ["changed_files", "files_modified", "changedFiles"]
    )
    out["changed_files"] = ensure_list(changed)

    # Commands / tests
    cmds = pick_first_available(fr, ["commands_tests", "commands_tests_run"]) or pick_first_available(
        session, ["commands_executed", "commands_tests", "commands"]
    )
    # commands_executed might be a list of dicts
    if isinstance(cmds, list) and len(cmds) > 0 and isinstance(cmds[0], dict):
        formatted = []
        for c in cmds:
            cmd = c.get("command") or c.get("cmd") or str(c)
            res = c.get("result")
            formatted.append(redact(f"{cmd}" + (f" ({res})" if res is not None else "")))
        out["commands_tests"] = formatted
    else:
        out["commands_tests"] = ensure_list(cmds)

    # Decisions / rationale
    decisions = pick_first_available(fr, ["decisions_rationale", "decisions"]) or pick_first_available(
        session, ["decisions_rationale", "notes"]
    )
    out["decisions_rationale"] = ensure_list(decisions)

    # Open loops
    loops = pick_first_available(fr, ["open_loops"]) or pick_first_available(session, ["open_loops", "open_items"])
    out["open_loops"] = ensure_list(loops)

    return out


def main():
    p = argparse.ArgumentParser(description="Generate concise final recap JSON from a session file or stdin")
    p.add_argument("session_json", help="Path to session JSON file")
    p.add_argument("--out", help="Output file path (defaults next to input with _concise_recap.json)")
    p.add_argument("--print", action="store_true", help="Print the concise recap to stdout")
    args = p.parse_args()

    with open(args.session_json, "r", encoding="utf-8") as f:
        session = json.load(f)

    recap = generate_concise_recap(session)

    out_path = args.out
    if not out_path:
        base = os.path.splitext(args.session_json)[0]
        out_path = base + "_concise_recap.json"

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(recap, f, indent=2, ensure_ascii=False)

    if args.print:
        print(json.dumps(recap, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
