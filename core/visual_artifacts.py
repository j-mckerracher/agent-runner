"""
visual_artifacts.py — Self-contained HTML recap and plan generators for the agent-workbench harness.

Two artifacts:
  - recap.html  — Written after PR creation; links the ADO pull request, lists changed files and
                  implementation/QA summaries.  Structure inspired by the BuilderIO visual-recap skill.
  - plan.html   — Written when the task-plan-evaluator approves a plan; shows story/goal, task cards,
                  dependency list, and AC-coverage map.  Structure inspired by the BuilderIO visual-plan skill.

Both files are self-contained (inline CSS, zero external dependencies) so they open correctly as
local file:// URLs in any browser.

Browser-open behaviour:
  - Enabled by default on interactive (tty) runs.
  - Suppressed when the CI env var is set (any value).
  - Suppressed when WORKBENCH_OPEN_BROWSER=0 or WORKBENCH_OPEN_BROWSER=false.
  - Force-enabled when WORKBENCH_OPEN_BROWSER=1 or WORKBENCH_OPEN_BROWSER=true
    (overrides CI / tty check).
"""

import html
import logging
import os
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Browser-open guard
# ---------------------------------------------------------------------------

def _browser_open_enabled() -> bool:
    """Return True when it is appropriate to open a browser window."""
    env_flag = os.environ.get("WORKBENCH_OPEN_BROWSER", "").strip().lower()
    if env_flag in ("1", "true", "yes"):
        return True
    if env_flag in ("0", "false", "no"):
        return False
    # Suppress in CI environments
    if os.environ.get("CI"):
        return False
    # Suppress when stdout is not a tty (headless / pipe)
    return sys.stdout.isatty()


def open_in_browser(path: Path, *, new_tab: bool) -> None:
    """Open *path* as a file:// URL.  new_tab=True requests a new tab (new=2)."""
    logger.info("visual_artifacts: artifact written → %s", path)
    if not _browser_open_enabled():
        logger.debug("visual_artifacts: browser-open suppressed; artifact: %s", path)
        return
    try:
        flag = 2 if new_tab else 0
        webbrowser.open(path.as_uri(), new=flag)
        logger.info("visual_artifacts: opened in browser (new=%d): %s", flag, path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("visual_artifacts: could not open browser: %s", exc)


# ---------------------------------------------------------------------------
# PR URL derivation
# ---------------------------------------------------------------------------

def pr_web_url(pr_payload: dict[str, Any]) -> str:
    """Derive a human-readable PR URL from the ``az repos pr create`` JSON payload."""
    # Field emitted by newer az devops CLI versions
    if isinstance(pr_payload.get("url"), str) and pr_payload["url"].startswith("http"):
        # REST API URL; not ideal but better than nothing
        pass
    # Preferred: repository.webUrl + /pullrequest/{id}
    repo = pr_payload.get("repository") or {}
    web_url = repo.get("webUrl") or repo.get("remoteUrl") or ""
    pr_id = pr_payload.get("pullRequestId") or pr_payload.get("codeReviewId")
    if web_url and pr_id:
        return f"{web_url.rstrip('/')}/pullrequest/{pr_id}"
    # Fallback: some versions expose a direct links dict
    links = pr_payload.get("_links") or pr_payload.get("links") or {}
    web_link = (links.get("web") or {}).get("href") or ""
    if web_link.startswith("http"):
        return web_link
    return ""


# ---------------------------------------------------------------------------
# Inline CSS shared by both artifacts
# ---------------------------------------------------------------------------

_COMMON_CSS = """
*, *::before, *::after { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  margin: 0; background: #0d1117; color: #c9d1d9; line-height: 1.6;
}
.page { max-width: 960px; margin: 0 auto; padding: 2rem 1.5rem 4rem; }
h1 { font-size: 1.75rem; font-weight: 700; color: #e6edf3; margin: 0 0 .25rem; }
h2 { font-size: 1.15rem; font-weight: 600; color: #e6edf3; margin: 1.8rem 0 .6rem; border-bottom: 1px solid #30363d; padding-bottom: .3rem; }
h3 { font-size: 1rem; font-weight: 600; color: #e6edf3; margin: .9rem 0 .4rem; }
.brief { color: #8b949e; font-size: .95rem; margin: 0 0 1.5rem; }
a { color: #58a6ff; text-decoration: none; }
a:hover { text-decoration: underline; }
.tag { display: inline-block; padding: .15rem .55rem; border-radius: 9999px; font-size: .75rem; font-weight: 600; }
.tag-pr   { background: #1a3f6f; color: #79c0ff; }
.tag-pass { background: #1a3a1a; color: #56d364; }
.tag-fail { background: #3d1c1c; color: #f85149; }
.tag-warn { background: #3d2e1a; color: #d29922; }
.pr-banner {
  display: flex; align-items: center; gap: .75rem;
  background: #161b22; border: 1px solid #30363d; border-radius: .5rem;
  padding: .9rem 1.1rem; margin-bottom: 1.5rem;
}
.pr-banner .pr-title { font-weight: 600; color: #e6edf3; }
.pr-banner .pr-link  { font-size: .85rem; }
.card {
  background: #161b22; border: 1px solid #30363d; border-radius: .5rem;
  padding: .85rem 1.1rem; margin-bottom: .6rem;
}
.card-header { display: flex; align-items: center; justify-content: space-between; gap: .5rem; }
.card-title  { font-weight: 600; color: #e6edf3; }
.card-body   { color: #8b949e; font-size: .9rem; margin-top: .35rem; }
.file-list   { list-style: none; padding: 0; margin: 0; }
.file-list li { padding: .2rem 0; font-size: .88rem; border-bottom: 1px solid #21262d; }
.file-list li:last-child { border-bottom: none; }
.file-status { font-family: monospace; margin-right: .5rem; font-size: .8rem; }
.M { color: #d29922; } .A { color: #56d364; } .D { color: #f85149; } .R { color: #79c0ff; }
.dep-list { padding-left: 1.2rem; margin: .3rem 0 0; color: #8b949e; font-size: .88rem; }
.ac-table { width: 100%; border-collapse: collapse; font-size: .88rem; }
.ac-table th { text-align: left; padding: .4rem .6rem; background: #21262d; color: #8b949e; font-weight: 600; }
.ac-table td { padding: .4rem .6rem; border-top: 1px solid #21262d; vertical-align: top; }
.footer { margin-top: 3rem; font-size: .78rem; color: #484f58; }
"""

# ---------------------------------------------------------------------------
# recap.html — PR stage
# ---------------------------------------------------------------------------

def _git_changed_files(repo: str | Path) -> list[tuple[str, str]]:
    """Return [(status_letter, filepath), ...] for files changed on this branch vs develop."""
    try:
        out = subprocess.check_output(
            ["git", "diff", "--name-status", "develop...HEAD"],
            cwd=str(repo),
            text=True,
            stderr=subprocess.DEVNULL,
        )
        result = []
        for line in out.strip().splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2:
                status = parts[0].strip()[:1] or "M"
                result.append((status, parts[1].strip()))
        return result
    except Exception as exc:  # noqa: BLE001
        logger.debug("visual_artifacts: git diff failed: %s", exc)
        return []


def _load_impl_summaries(change_id: str) -> list[dict[str, Any]]:
    """Collect brief summaries from execution/*/impl_report.yaml."""
    from .runtime_paths import agent_context_root  # local import avoids circular
    root = agent_context_root() / change_id / "execution"
    summaries: list[dict[str, Any]] = []
    if not root.is_dir():
        return summaries
    for impl_path in sorted(root.glob("*/impl_report.yaml")):
        try:
            with open(impl_path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            summaries.append({
                "uow": impl_path.parent.name,
                "status": data.get("status") or data.get("overall_result") or "unknown",
                "summary": data.get("summary") or data.get("description") or "",
            })
        except Exception:  # noqa: BLE001
            pass
    return summaries


def _load_qa_summary(change_id: str) -> str:
    """Return a one-line QA summary or empty string."""
    from .runtime_paths import agent_context_root
    qa_path = agent_context_root() / change_id / "qa" / "qa_report.yaml"
    try:
        with open(qa_path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return data.get("summary") or data.get("overall_result") or ""
    except Exception:  # noqa: BLE001
        return ""


def _recap_status_class(status: str) -> str:
    s = str(status).lower()
    if s in ("pass", "passed", "success", "completed"):
        return "tag-pass"
    if s in ("fail", "failed", "error"):
        return "tag-fail"
    return "tag-warn"


def generate_recap_html(change_id: str, repo: str | Path, pr_payload: dict[str, Any]) -> Path:
    """Generate a self-contained recap HTML and write it to ``{pr_dir}/recap.html``.

    Returns the path to the written file.
    """
    from .runtime_paths import agent_context_root

    pr_dir = agent_context_root() / change_id / "pr"
    pr_dir.mkdir(parents=True, exist_ok=True)
    out_path = pr_dir / "recap.html"

    pr_url = pr_web_url(pr_payload)
    pr_id = pr_payload.get("pullRequestId") or pr_payload.get("codeReviewId") or ""
    pr_title = pr_payload.get("title") or f"PR for {change_id}"
    feature_branch = pr_payload.get("sourceRefName", "").replace("refs/heads/", "") or "feature branch"

    changed_files = _git_changed_files(repo)
    impl_summaries = _load_impl_summaries(change_id)
    qa_summary = _load_qa_summary(change_id)

    e = html.escape  # shorthand

    # ---- file map -------------------------------------------------------
    file_rows = ""
    for status, filepath in changed_files:
        css = {"M": "M", "A": "A", "D": "D", "R": "R"}.get(status, "M")
        label = {"M": "modified", "A": "added", "D": "deleted", "R": "renamed"}.get(status, status)
        file_rows += (
            f'<li><span class="file-status {css}">{e(label)}</span>'
            f'<code>{e(filepath)}</code></li>\n'
        )
    if not file_rows:
        file_rows = '<li><span style="color:#484f58">No changed files detected</span></li>\n'

    # ---- implementation summaries ----------------------------------------
    impl_cards = ""
    for s in impl_summaries:
        css = _recap_status_class(s["status"])
        impl_cards += (
            f'<div class="card">'
            f'<div class="card-header">'
            f'<span class="card-title">{e(s["uow"])}</span>'
            f'<span class="tag {css}">{e(str(s["status"]))}</span>'
            f'</div>'
            f'<div class="card-body">{e(str(s["summary"]))}</div>'
            f'</div>\n'
        )
    if not impl_cards:
        impl_cards = '<p style="color:#484f58;font-size:.88rem">No implementation reports found.</p>\n'

    # ---- QA block -------------------------------------------------------
    qa_block = ""
    if qa_summary:
        qa_block = (
            f'<div class="card"><div class="card-body">{e(qa_summary)}</div></div>\n'
        )
    else:
        qa_block = '<p style="color:#484f58;font-size:.88rem">No QA report found.</p>\n'

    # ---- PR banner -------------------------------------------------------
    if pr_url:
        pr_link_html = f'<a href="{e(pr_url)}" class="pr-link" target="_blank">{e(pr_url)}</a>'
    else:
        pr_link_html = '<span style="color:#484f58">PR URL not available</span>'

    pr_id_badge = f'<span class="tag tag-pr">PR #{pr_id}</span>' if pr_id else ""

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Visual Recap — {e(change_id)}</title>
<style>{_COMMON_CSS}</style>
</head>
<body>
<div class="page">
  <h1>Visual Recap</h1>
  <p class="brief">Change <strong>{e(change_id)}</strong> · branch <code>{e(feature_branch)}</code></p>

  <div class="pr-banner">
    {pr_id_badge}
    <div>
      <div class="pr-title">{e(pr_title)}</div>
      {pr_link_html}
    </div>
  </div>

  <h2>Changed Files <span style="color:#484f58;font-weight:400;font-size:.85rem">({len(changed_files)} file(s))</span></h2>
  <ul class="file-list">
{file_rows}  </ul>

  <h2>Implementation Summaries</h2>
{impl_cards}
  <h2>QA Summary</h2>
{qa_block}
  <div class="footer">
    Generated by agent-workbench · structure inspired by the
    <a href="https://github.com/BuilderIO/skills/tree/main/skills/visual-recap" target="_blank">visual-recap skill</a>
  </div>
</div>
</body>
</html>
"""

    out_path.write_text(html_content, encoding="utf-8")
    logger.info("visual_artifacts: recap written → %s", out_path)
    return out_path


# ---------------------------------------------------------------------------
# plan.html — task-plan-evaluator approval stage
# ---------------------------------------------------------------------------

def _load_story(change_id: str) -> dict[str, Any]:
    from .runtime_paths import agent_context_root
    story_path = agent_context_root() / change_id / "intake" / "story.yaml"
    try:
        with open(story_path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:  # noqa: BLE001
        return {}


def _load_tasks(change_id: str) -> list[dict[str, Any]]:
    from .runtime_paths import agent_context_root
    tasks_path = agent_context_root() / change_id / "planning" / "tasks.yaml"
    try:
        with open(tasks_path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        tasks = data.get("tasks") or []
        return tasks if isinstance(tasks, list) else []
    except Exception:  # noqa: BLE001
        return []


def generate_plan_html(change_id: str, *, approved: bool = True) -> Path:
    """Generate a self-contained plan HTML and write it to ``{planning}/plan.html``.

    Returns the path to the written file.
    """
    from .runtime_paths import agent_context_root

    planning_dir = agent_context_root() / change_id / "planning"
    planning_dir.mkdir(parents=True, exist_ok=True)
    out_path = planning_dir / "plan.html"

    story = _load_story(change_id)
    tasks = _load_tasks(change_id)

    e = html.escape

    story_title = e(story.get("title") or f"Plan for {change_id}")
    story_goal = e(story.get("goal") or story.get("description") or "")
    acs: list[Any] = story.get("acceptance_criteria") or story.get("critical_acceptance_criteria") or []

    # ---- task cards -------------------------------------------------------
    task_cards = ""
    for task in tasks:
        tid = e(str(task.get("id") or task.get("task_id") or ""))
        title = e(str(task.get("title") or task.get("name") or "(untitled)"))
        desc = e(str(task.get("description") or ""))
        deps: list[Any] = task.get("depends_on") or task.get("dependencies") or []
        dep_html = ""
        if deps:
            dep_items = "".join(f"<li>{e(str(d))}</li>" for d in deps)
            dep_html = f'<ul class="dep-list">{dep_items}</ul>'
        task_cards += (
            f'<div class="card">'
            f'<div class="card-header">'
            f'<span class="card-title">{tid} — {title}</span>'
            f'</div>'
            f'<div class="card-body">{desc}</div>'
            f'{dep_html}'
            f'</div>\n'
        )
    if not task_cards:
        task_cards = '<p style="color:#484f58;font-size:.88rem">No tasks found in tasks.yaml.</p>\n'

    # ---- AC table --------------------------------------------------------
    ac_rows = ""
    for i, ac in enumerate(acs, start=1):
        if isinstance(ac, dict):
            ac_id = e(str(ac.get("id") or f"AC{i}"))
            ac_text = e(str(ac.get("description") or ac.get("text") or str(ac)))
        else:
            ac_id = e(f"AC{i}")
            ac_text = e(str(ac))
        # find covering tasks
        covering = []
        for task in tasks:
            covers = task.get("covers_acs") or task.get("acceptance_criteria") or []
            raw_id = (ac.get("id") if isinstance(ac, dict) else f"AC{i}")
            if raw_id in covers or str(i) in covers:
                covering.append(str(task.get("id") or task.get("task_id") or ""))
        covered_html = (
            ", ".join(f"<code>{e(t)}</code>" for t in covering)
            if covering else '<span style="color:#484f58">—</span>'
        )
        ac_rows += f'<tr><td>{ac_id}</td><td>{ac_text}</td><td>{covered_html}</td></tr>\n'

    ac_section = ""
    if ac_rows:
        ac_section = f"""
  <h2>Acceptance Criteria Coverage</h2>
  <table class="ac-table">
    <thead><tr><th>ID</th><th>Criterion</th><th>Covered by</th></tr></thead>
    <tbody>{ac_rows}</tbody>
  </table>
"""

    badge_class = "tag-pass" if approved else "tag-fail"
    badge_text = "APPROVED" if approved else "NOT APPROVED"

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Visual Plan — {e(change_id)}</title>
<style>{_COMMON_CSS}</style>
</head>
<body>
<div class="page">
  <h1>Visual Plan</h1>
    <p class="brief">{story_title} · <span class="tag {badge_class}">{badge_text}</span></p>

  {'<h2>Goal</h2><p class="card-body" style="padding:.6rem 0">' + story_goal + '</p>' if story_goal else ''}

  <h2>Tasks <span style="color:#484f58;font-weight:400;font-size:.85rem">({len(tasks)} task(s))</span></h2>
{task_cards}
{ac_section}
  <div class="footer">
    Generated by agent-workbench · structure inspired by the
    <a href="https://github.com/BuilderIO/skills/tree/main/skills/visual-plan" target="_blank">visual-plan skill</a>
  </div>
</div>
</body>
</html>
"""

    out_path.write_text(html_content, encoding="utf-8")
    logger.info("visual_artifacts: plan written → %s", out_path)
    return out_path
