#!/usr/bin/env python3
"""generate-obsidian-archive.py

Transforms workflow artifacts from a CHANGE-ID artifact tree into an
Obsidian-optimized vault with MOC, UoW execution records, agent logs,
QA reports, and lessons reports.

Usage:
    generate-obsidian-archive.py <artifact_root> <change_id> <vault_root>

Inputs:
    artifact_root  – base directory containing the CHANGE-ID folder
    change_id      – the CHANGE-ID (e.g., "STORY-1234")
    vault_root     – destination Obsidian vault directory

Outputs:
    - {vault_root}/{CHANGE-ID}-MOC.md              (Master Map of Content)
    - {vault_root}/{CHANGE-ID}-{UOW-ID}-Execution.md  (per UoW)
    - {vault_root}/{CHANGE-ID}-{AgentName}-Logs.md     (per agent)
    - {vault_root}/{CHANGE-ID}-QA-Report.md            (if qa/ present)
    - {vault_root}/{CHANGE-ID}-Lessons-Optimizer-Report.md (if lessons present)

Exit codes: 0 = success, 1 = partial (warnings), 2 = usage error
Emits JSON summary to stdout.
"""

from __future__ import annotations

import glob
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_file(path: str):
    """Load a JSON or YAML file, returning parsed content or None."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except Exception:
        return None

    suffix = p.suffix.lower()
    if suffix == ".json":
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
    elif suffix in (".yaml", ".yml"):
        if HAS_YAML:
            try:
                return yaml.safe_load(text)
            except yaml.YAMLError:
                return None
        return None
    elif suffix == ".md":
        return text
    return None


def collect_files(base: str, patterns: list[str]) -> list[str]:
    """Collect files matching glob patterns under base directory."""
    results = []
    for pat in patterns:
        full = os.path.join(base, pat)
        results.extend(sorted(glob.glob(full, recursive=True)))
    return results


def yaml_frontmatter(meta: dict) -> str:
    """Render YAML frontmatter block."""
    lines = ["---"]
    for k, v in meta.items():
        if isinstance(v, list):
            lines.append(f"{k}:")
            for item in v:
                lines.append(f"  - {item}")
        else:
            lines.append(f'{k}: "{v}"' if isinstance(v, str) else f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines)


def callout(kind: str, title: str, body: str) -> str:
    """Render an Obsidian callout block."""
    body_lines = body.strip().split("\n")
    rendered = [f"> [!{kind}] {title}"]
    for line in body_lines:
        rendered.append(f"> {line}")
    return "\n".join(rendered)


def safe_str(val, default="N/A") -> str:
    if val is None:
        return default
    return str(val)


# ---------------------------------------------------------------------------
# File generators
# ---------------------------------------------------------------------------

def generate_moc(change_id: str, artifact_dir: str, vault_root: str,
                 uow_ids: list[str], agent_names: list[str],
                 has_qa: bool, has_lessons: bool) -> str:
    """Generate the Master MOC file."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Try to load story/config metadata
    story = {}
    for name in ("story.yaml", "story.yml", "story.json"):
        candidate = os.path.join(artifact_dir, "intake", name)
        data = load_file(candidate)
        if data and isinstance(data, dict):
            story = data
            break
    if not story:
        for name in ("story.yaml", "story.yml", "story.json"):
            candidate = os.path.join(artifact_dir, name)
            data = load_file(candidate)
            if data and isinstance(data, dict):
                story = data
                break

    config = {}
    for name in ("config.yaml", "config.yml", "config.json"):
        candidate = os.path.join(artifact_dir, "intake", name)
        data = load_file(candidate)
        if data and isinstance(data, dict):
            config = data
            break
    if not config:
        for name in ("config.yaml", "config.yml", "config.json"):
            candidate = os.path.join(artifact_dir, name)
            data = load_file(candidate)
            if data and isinstance(data, dict):
                config = data
                break

    title = safe_str(story.get("title"), change_id)
    description = safe_str(story.get("description"), "No description available.")
    status = safe_str(story.get("status") or config.get("status"), "completed")
    project_type = safe_str(config.get("project_type"), "unknown")

    meta = {
        "type": "workflow_moc",
        "change_id": change_id,
        "title": title,
        "date_archived": now,
        "status": status,
        "project_type": project_type,
        "tags": [f"#workflow/complete", f"#ado/{change_id}"],
    }

    sections = [yaml_frontmatter(meta), ""]
    sections.append(f"# {change_id}: {title}\n")
    sections.append(callout("info", "Story Description", description))
    sections.append("")

    sections.append("## Workflow Execution Summary")
    sections.append(f"- **Archived At**: {now}")
    sections.append("")

    # Planning & Assignment
    sections.append("## Workflow Phases & Logs\n")
    sections.append("### 1. Planning & Assignment")
    planning_agents = ["Task-Generator", "Task-Assigner"]
    for a in planning_agents:
        if a.lower().replace("-", "_") in [n.lower().replace("-", "_") for n in agent_names] or True:
            sections.append(f"- [[{change_id}-{a}-Logs]]")
    sections.append("")

    # Execution
    sections.append("### 2. Execution (Units of Work)")
    if uow_ids:
        for uow in sorted(uow_ids):
            sections.append(f"- [[{change_id}-{uow}-Execution]]")
    else:
        sections.append("- No UoW execution records found.")
    sections.append("")

    # QA
    sections.append("### 3. QA & Remediation")
    if has_qa:
        sections.append(f"- [[{change_id}-QA-Report]]")
    else:
        sections.append("- No QA report found.")
    sections.append("")

    # Lessons
    sections.append("### 4. Continuous Improvement")
    if has_lessons:
        sections.append(f"- [[{change_id}-Lessons-Optimizer-Report]]")
    else:
        sections.append("- No lessons optimizer report found.")
    sections.append("")

    # Standing questions
    sections.append("## Standing Questions")
    questions_file = os.path.join(artifact_dir, "summary", "standing-questions.md")
    questions = load_file(questions_file)
    if questions and isinstance(questions, str) and questions.strip():
        sections.append(questions.strip())
    else:
        sections.append("None.")
    sections.append("")

    path = os.path.join(vault_root, f"{change_id}-MOC.md")
    Path(path).write_text("\n".join(sections), encoding="utf-8")
    return path


def generate_uow_record(change_id: str, uow_id: str, artifact_dir: str,
                        vault_root: str) -> str:
    """Generate a per-UoW execution record combining impl + eval data."""
    meta = {
        "type": "uow_execution",
        "change_id": change_id,
        "uow_id": uow_id,
        "parent_moc": f"[[{change_id}-MOC]]",
        "tags": ["#uow", "#agent/software-engineer"],
    }

    sections = [yaml_frontmatter(meta), ""]
    sections.append(f"# {uow_id} Execution Record\n")

    # Load implementation report
    impl_report = None
    for pattern in [
        os.path.join(artifact_dir, "execution", uow_id, "impl_report.*"),
        os.path.join(artifact_dir, "execution", uow_id, "implementation_report.*"),
        os.path.join(artifact_dir, "execution", uow_id.lower(), "impl_report.*"),
    ]:
        matches = glob.glob(pattern)
        for m in matches:
            data = load_file(m)
            if data and isinstance(data, dict):
                impl_report = data
                break
        if impl_report:
            break

    sections.append("## Implementation Report")
    if impl_report:
        sections.append(f"**Status**: {safe_str(impl_report.get('status'))}")
        sections.append(f"**Summary**: {safe_str(impl_report.get('implementation_summary'))}")
        sections.append("")

        # Definition of Done
        dod = impl_report.get("definition_of_done", impl_report.get("dod", []))
        if dod:
            sections.append(callout("success", "Definition of Done Status", ""))
            if isinstance(dod, list):
                for item in dod:
                    if isinstance(item, dict):
                        met = "✅" if item.get("met") else "❌"
                        sections.append(f"> {met} {safe_str(item.get('criterion', item.get('item')))}")
                    else:
                        sections.append(f"> - {item}")
            sections.append("")

        # Files modified
        files = impl_report.get("files_modified", impl_report.get("files_changed", []))
        if files:
            sections.append("### Files Modified & Code Changes")
            for f in files:
                if isinstance(f, dict):
                    sections.append(f"- `{safe_str(f.get('path', f.get('file')))}`"
                                    f" — {safe_str(f.get('action', f.get('change_type', '')))}")
                else:
                    sections.append(f"- `{f}`")
            sections.append("")
    else:
        sections.append("No implementation report found.\n")

    # Evaluator iterations
    sections.append("## Evaluator Iterations")
    eval_files = sorted(glob.glob(
        os.path.join(artifact_dir, "execution", uow_id, "eval_impl*.*")
    ) + glob.glob(
        os.path.join(artifact_dir, "execution", uow_id.lower(), "eval_impl*.*")
    ))

    if eval_files:
        for i, ef in enumerate(eval_files, 1):
            edata = load_file(ef)
            if not edata or not isinstance(edata, dict):
                continue
            result = safe_str(edata.get("overall_result", edata.get("result")))
            score = safe_str(edata.get("score"))
            sections.append(f"\n### Attempt {i}")

            kind = "bug" if result.lower() in ("fail", "revise", "reject") else "success"
            body_lines = [f"**Score**: {score}"]

            fixes = edata.get("actionable_fixes_summary",
                              edata.get("actionable_fixes", []))
            if fixes:
                body_lines.append("**Actionable Fixes**:")
                if isinstance(fixes, list):
                    for fix in fixes:
                        body_lines.append(f"- {fix}")
                else:
                    body_lines.append(str(fixes))

            sections.append(callout(kind, f"Evaluator Result: {result}",
                                    "\n".join(body_lines)))
    else:
        sections.append("No evaluator feedback found.\n")

    sections.append(f"\n---\n← Back to [[{change_id}-MOC]]")

    path = os.path.join(vault_root, f"{change_id}-{uow_id}-Execution.md")
    Path(path).write_text("\n".join(sections), encoding="utf-8")
    return path


def generate_agent_log(change_id: str, agent_name: str, log_files: list[str],
                       vault_root: str) -> str:
    """Generate an agent log summary from JSON/YAML session logs."""
    display_name = agent_name.replace("_", "-").title().replace(" ", "-")

    meta = {
        "type": "agent_log",
        "change_id": change_id,
        "agent": display_name,
        "parent_moc": f"[[{change_id}-MOC]]",
        "tags": [f"#agent/{agent_name.lower().replace('_', '-')}"],
    }

    sections = [yaml_frontmatter(meta), ""]
    sections.append(f"# {change_id} — {display_name} Logs\n")

    for lf in sorted(log_files):
        data = load_file(lf)
        fname = os.path.basename(lf)

        if data and isinstance(data, dict):
            sections.append(f"## Session: {fname}")
            ts = safe_str(data.get("timestamp"))
            sections.append(f"**Timestamp**: {ts}\n")

            summary = data.get("session_summary")
            if summary:
                sections.append(callout("info", "Session Summary", safe_str(summary)))
                sections.append("")

            decisions = data.get("decisions_made", [])
            if decisions:
                sections.append("### Decisions Made")
                for d in decisions:
                    if isinstance(d, dict):
                        sections.append(f"- **{safe_str(d.get('decision'))}**: "
                                        f"{safe_str(d.get('rationale'))}")
                    else:
                        sections.append(f"- {d}")
                sections.append("")

            issues = data.get("issues_encountered", [])
            if issues:
                sections.append("### Issues Encountered")
                for iss in issues:
                    if isinstance(iss, dict):
                        sections.append(callout("error", safe_str(iss.get("issue")),
                                                safe_str(iss.get("resolution", ""))))
                    else:
                        sections.append(f"- {iss}")
                sections.append("")

            notes = data.get("notes")
            if notes:
                sections.append(f"**Notes**: {safe_str(notes)}\n")

        elif data and isinstance(data, str):
            sections.append(f"## {fname}")
            sections.append(data.strip())
            sections.append("")
        else:
            sections.append(f"## {fname}")
            sections.append("*Could not parse log file.*\n")

    sections.append(f"\n---\n← Back to [[{change_id}-MOC]]")

    path = os.path.join(vault_root, f"{change_id}-{display_name}-Logs.md")
    Path(path).write_text("\n".join(sections), encoding="utf-8")
    return path


def generate_qa_report(change_id: str, artifact_dir: str,
                       vault_root: str) -> str | None:
    """Generate QA report from qa/ directory artifacts."""
    qa_dir = os.path.join(artifact_dir, "qa")
    if not os.path.isdir(qa_dir):
        return None

    qa_files = collect_files(qa_dir, ["**/*.json", "**/*.yaml", "**/*.yml"])
    if not qa_files:
        return None

    meta = {
        "type": "qa_report",
        "change_id": change_id,
        "parent_moc": f"[[{change_id}-MOC]]",
        "tags": ["#qa", "#agent/qa-engineer"],
    }

    sections = [yaml_frontmatter(meta), ""]
    sections.append(f"# {change_id} — QA Report\n")

    for qf in qa_files:
        data = load_file(qf)
        fname = os.path.basename(qf)

        if data and isinstance(data, dict):
            sections.append(f"## {fname}")
            result = data.get("overall_result", data.get("result", "N/A"))
            kind = "success" if str(result).lower() in ("pass", "approve") else "bug"
            sections.append(callout(kind, f"Result: {result}",
                                    safe_str(data.get("summary", ""))))
            sections.append("")

            findings = data.get("findings", data.get("issues", []))
            if findings:
                sections.append("### Findings")
                for finding in findings:
                    if isinstance(finding, dict):
                        sev = safe_str(finding.get("severity", "info"))
                        sections.append(f"- **[{sev}]** {safe_str(finding.get('description', finding.get('finding')))}")
                    else:
                        sections.append(f"- {finding}")
                sections.append("")
        else:
            sections.append(f"## {fname}")
            sections.append("*Could not parse QA artifact.*\n")

    sections.append(f"\n---\n← Back to [[{change_id}-MOC]]")

    path = os.path.join(vault_root, f"{change_id}-QA-Report.md")
    Path(path).write_text("\n".join(sections), encoding="utf-8")
    return path


def generate_lessons_report(change_id: str, artifact_dir: str,
                            vault_root: str) -> str | None:
    """Generate lessons optimizer report from summary/ artifacts."""
    summary_dir = os.path.join(artifact_dir, "summary")
    if not os.path.isdir(summary_dir):
        return None

    lessons_files = []
    for pat in ["*lesson*", "*lessons*", "*improvement*"]:
        lessons_files.extend(glob.glob(os.path.join(summary_dir, pat)))
    if not lessons_files:
        return None

    meta = {
        "type": "lessons_report",
        "change_id": change_id,
        "parent_moc": f"[[{change_id}-MOC]]",
        "tags": ["#lessons", "#continuous-improvement"],
    }

    sections = [yaml_frontmatter(meta), ""]
    sections.append(f"# {change_id} — Lessons Optimizer Report\n")

    for lf in sorted(lessons_files):
        data = load_file(lf)
        fname = os.path.basename(lf)

        if data and isinstance(data, dict):
            sections.append(f"## {fname}")
            if "lessons" in data:
                for lesson in data["lessons"]:
                    if isinstance(lesson, dict):
                        sections.append(callout(
                            "question",
                            safe_str(lesson.get("title", "Lesson")),
                            safe_str(lesson.get("description", lesson.get("body", "")))
                        ))
                        sections.append("")
                    else:
                        sections.append(f"- {lesson}")
            else:
                for k, v in data.items():
                    sections.append(f"**{k}**: {safe_str(v)}")
            sections.append("")
        elif data and isinstance(data, str):
            sections.append(f"## {fname}")
            sections.append(data.strip())
            sections.append("")

    sections.append(f"\n---\n← Back to [[{change_id}-MOC]]")

    path = os.path.join(vault_root, f"{change_id}-Lessons-Optimizer-Report.md")
    Path(path).write_text("\n".join(sections), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------

def discover_uow_ids(artifact_dir: str) -> list[str]:
    """Discover UOW-IDs from execution/ subdirectories."""
    exec_dir = os.path.join(artifact_dir, "execution")
    if not os.path.isdir(exec_dir):
        return []
    ids = []
    for entry in os.listdir(exec_dir):
        full = os.path.join(exec_dir, entry)
        if os.path.isdir(full) and entry.upper().startswith("UOW"):
            ids.append(entry)
        elif os.path.isdir(full):
            ids.append(entry)
    return sorted(set(ids))


def discover_agent_logs(artifact_dir: str) -> dict[str, list[str]]:
    """Discover per-agent log files from logs/ directory."""
    logs_dir = os.path.join(artifact_dir, "logs")
    if not os.path.isdir(logs_dir):
        return {}
    agents = {}
    for entry in os.listdir(logs_dir):
        full = os.path.join(logs_dir, entry)
        if os.path.isdir(full):
            files = collect_files(full, ["*.json", "*.yaml", "*.yml"])
            if files:
                agents[entry] = files
    return agents


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) != 4:
        print(json.dumps({
            "status": "error",
            "message": f"Usage: {sys.argv[0]} <artifact_root> <change_id> <vault_root>"
        }))
        sys.exit(2)

    artifact_root = sys.argv[1]
    change_id = sys.argv[2]
    vault_root = sys.argv[3]

    artifact_dir = os.path.join(artifact_root, change_id)
    if not os.path.isdir(artifact_dir):
        # Fallback: maybe artifact_root IS the change-id dir
        if os.path.isdir(os.path.join(artifact_root, "intake")) or \
           os.path.isdir(os.path.join(artifact_root, "logs")):
            artifact_dir = artifact_root
        else:
            print(json.dumps({
                "status": "error",
                "message": f"Artifact directory not found: {artifact_dir}"
            }))
            sys.exit(1)

    os.makedirs(vault_root, exist_ok=True)

    warnings = []
    created_files = []

    # Discover structure
    uow_ids = discover_uow_ids(artifact_dir)
    agent_logs = discover_agent_logs(artifact_dir)
    has_qa = os.path.isdir(os.path.join(artifact_dir, "qa"))
    has_lessons = bool(glob.glob(os.path.join(artifact_dir, "summary", "*lesson*")))

    # 1. Generate Master MOC
    try:
        moc_path = generate_moc(change_id, artifact_dir, vault_root,
                                uow_ids, list(agent_logs.keys()),
                                has_qa, has_lessons)
        created_files.append(moc_path)
    except Exception as e:
        warnings.append(f"MOC generation failed: {e}")

    # 2. Generate UoW execution records
    for uow_id in uow_ids:
        try:
            path = generate_uow_record(change_id, uow_id, artifact_dir, vault_root)
            created_files.append(path)
        except Exception as e:
            warnings.append(f"UoW {uow_id} generation failed: {e}")

    # 3. Generate agent log summaries
    for agent_name, log_files in agent_logs.items():
        try:
            path = generate_agent_log(change_id, agent_name, log_files, vault_root)
            created_files.append(path)
        except Exception as e:
            warnings.append(f"Agent log {agent_name} generation failed: {e}")

    # 4. Generate QA report
    try:
        qa_path = generate_qa_report(change_id, artifact_dir, vault_root)
        if qa_path:
            created_files.append(qa_path)
    except Exception as e:
        warnings.append(f"QA report generation failed: {e}")

    # 5. Generate lessons report
    try:
        lessons_path = generate_lessons_report(change_id, artifact_dir, vault_root)
        if lessons_path:
            created_files.append(lessons_path)
    except Exception as e:
        warnings.append(f"Lessons report generation failed: {e}")

    status = "ok" if not warnings else "partial"
    result = {
        "status": status,
        "change_id": change_id,
        "vault_root": vault_root,
        "files_created": len(created_files),
        "files": [os.path.basename(f) for f in created_files],
    }
    if warnings:
        result["warnings"] = warnings

    print(json.dumps(result, indent=2))
    sys.exit(0 if not warnings else 1)


if __name__ == "__main__":
    main()
