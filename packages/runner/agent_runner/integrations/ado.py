from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

from ..commands import run_command
from ..console import log
from ..models import (
    DEFAULT_ADO_ORGANIZATION,
    DEFAULT_ADO_PROJECT,
    WorkItemReference,
    WorkflowError,
)
from ..repo import get_repo_name


def resolve_ado_defaults(repo_root: Path) -> tuple[str, str]:
    """Resolve Azure DevOps defaults without mutating local CLI configuration."""

    organization = DEFAULT_ADO_ORGANIZATION
    project = DEFAULT_ADO_PROJECT
    az_bin = shutil.which("az")
    if az_bin is None:
        return organization, project

    try:
        completed = subprocess.run(
            [az_bin, "devops", "configure", "--list"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return organization, project

    if completed.returncode != 0:
        return organization, project

    organization_match = re.search(
        r"^\s*organization\s*=\s*(.+?)\s*$",
        completed.stdout,
        flags=re.MULTILINE,
    )
    project_match = re.search(
        r"^\s*project\s*=\s*(.+?)\s*$",
        completed.stdout,
        flags=re.MULTILINE,
    )
    if organization_match:
        organization = organization_match.group(1).strip()
    if project_match:
        project = project_match.group(1).strip()
    return organization, project


def parse_work_item_reference(
    raw_value: str,
    *,
    default_organization: str,
    default_project: str,
) -> WorkItemReference:
    """Parse either an Azure DevOps work item URL or a bare work item id."""

    value = raw_value.strip()
    if not value:
        raise WorkflowError("A work item id or URL is required.")

    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        parts = [unquote(part) for part in parsed.path.split("/") if part]
        if (
            parsed.netloc != "dev.azure.com"
            or len(parts) < 5
            or parts[2] != "_workitems"
            or parts[3] != "edit"
            or not parts[4].isdigit()
        ):
            raise WorkflowError(
                "Azure DevOps links must look like "
                "https://dev.azure.com/{organization}/{project}/_workitems/edit/{id}"
            )
        organization_url = f"{parsed.scheme}://{parsed.netloc}/{parts[0]}"
        return WorkItemReference(
            organization_url=organization_url,
            project=parts[1],
            work_item_id=parts[4],
        )

    match = re.fullmatch(r"(?:WI-)?(\d+)", value, flags=re.IGNORECASE)
    if not match:
        raise WorkflowError(
            "Enter either a numeric work item id, WI-12345, or a full "
            "Azure DevOps work item URL."
        )

    return WorkItemReference(
        organization_url=default_organization,
        project=default_project,
        work_item_id=match.group(1),
    )


def build_work_item_url(reference: WorkItemReference) -> str:
    """Construct the canonical Azure DevOps work item URL."""

    project = quote(reference.project, safe="")
    return f"{reference.organization_url}/{project}/_workitems/edit/{reference.work_item_id}"


def strip_html_to_text(raw_html: str | None) -> str:
    """Convert Azure DevOps rich text into readable plain text."""

    if not raw_html:
        return ""

    text = html.unescape(raw_html)
    substitutions = [
        (r"(?i)<br\s*/?>", "\n"),
        (r"(?i)</p\s*>", "\n\n"),
        (r"(?i)<p\s*>", ""),
        (r"(?i)</div\s*>", "\n"),
        (r"(?i)<div\s*>", ""),
        (r"(?i)</li\s*>", "\n"),
        (r"(?i)<li\s*>", "- "),
        (r"(?i)</ul\s*>", "\n"),
        (r"(?i)</ol\s*>", "\n"),
    ]
    for pattern, replacement in substitutions:
        text = re.sub(pattern, replacement, text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_acceptance_criteria_from_text(text: str) -> list[str]:
    """Extract acceptance criteria embedded in free-form text."""

    criteria: list[str] = []
    in_section = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"(?i)^AC\s*[-:]\s*(.+)$", line)
        if match:
            criteria.append(match.group(1).strip())
            continue
        if re.match(r"(?i)^acceptance criteria\s*:?\s*$", line):
            in_section = True
            continue
        if in_section:
            bullet = re.match(r"^(?:[-*]|\d+\.)\s*(.+)$", line)
            if bullet:
                candidate = re.sub(r"(?i)^AC\s*[-:]\s*", "", bullet.group(1).strip())
                criteria.append(candidate)
                continue
            if criteria:
                break
    return criteria


def build_ado_context(
    work_item_payload: dict[str, Any],
    reference: WorkItemReference,
) -> str:
    """Build plain-text workflow context from an Azure DevOps work item."""

    fields = work_item_payload.get("fields", {})
    title = strip_html_to_text(str(fields.get("System.Title", "")))
    description = strip_html_to_text(str(fields.get("System.Description", "")))
    acceptance_text = strip_html_to_text(
        str(
            fields.get("Microsoft.VSTS.Common.AcceptanceCriteria")
            or fields.get("Custom.AcceptanceCriteria")
            or ""
        )
    )
    if acceptance_text:
        acceptance_lines = [
            line.strip() for line in acceptance_text.splitlines() if line.strip()
        ]
        acceptance_block = "\n".join(acceptance_lines)
    else:
        extracted = extract_acceptance_criteria_from_text(description)
        acceptance_block = "\n".join(f"- {item}" for item in extracted)

    lines = [title]
    if description:
        lines.extend(["", "Description:", description])
    if acceptance_block:
        lines.extend(["", "Acceptance Criteria:", acceptance_block])

    for label, field_name in (
        ("Work Item Type", "System.WorkItemType"),
        ("Area Path", "System.AreaPath"),
        ("Iteration", "System.IterationPath"),
        ("Story Points", "Microsoft.VSTS.Scheduling.StoryPoints"),
        ("Effort", "Microsoft.VSTS.Scheduling.Effort"),
        ("State", "System.State"),
        ("Tags", "System.Tags"),
    ):
        value = fields.get(field_name)
        if value not in {None, ""}:
            lines.append(f"{label}: {value}")

    lines.extend(
        [
            "",
            f"Azure DevOps Organization: {reference.organization_url}",
            f"Azure DevOps Project: {reference.project}",
            f"Azure DevOps Work Item ID: {reference.work_item_id}",
            f"Azure DevOps URL: {build_work_item_url(reference)}",
        ]
    )
    return "\n".join(lines).strip()


def fetch_ado_context(reference: WorkItemReference, repo_root: Path) -> str:
    """Fetch workflow context directly from Azure DevOps via the Azure CLI."""

    az_bin = shutil.which("az")
    if az_bin is None:
        raise WorkflowError(
            "Azure CLI is not installed. Install `az` and the azure-devops "
            "extension, or paste context manually."
        )

    command = [
        az_bin,
        "boards",
        "work-item",
        "show",
        "--id",
        reference.work_item_id,
        "--org",
        reference.organization_url,
        "--output",
        "json",
        "--only-show-errors",
    ]
    result = run_command(command, cwd=repo_root, timeout_seconds=60)
    if result.exit_code != 0:
        raise WorkflowError(
            "Azure DevOps fetch failed. Ensure `az` is authenticated for this "
            "organization/project.\n"
            f"stderr:\n{result.stderr.strip() or '(no stderr)'}"
        )

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"Azure DevOps returned invalid JSON: {exc}") from exc
    return build_ado_context(payload, reference)


def create_pull_request(
    repo_root: Path,
    source_branch: str,
    base_ref: str,
    change_id: str,
    org_url: str,
    project: str,
    worktree_path: Path | None = None,
) -> None:
    """Push *source_branch* and create an Azure DevOps pull request."""

    repo_name = get_repo_name(repo_root)
    work_item_id = change_id.removeprefix("WI-")
    push_cwd = str(worktree_path) if worktree_path else str(repo_root)

    log("INFO", f"Pushing branch '{source_branch}' to origin")
    push_result = subprocess.run(
        ["git", "-C", push_cwd, "push", "origin", source_branch],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if push_result.returncode != 0:
        raise WorkflowError(
            f"Failed to push branch '{source_branch}': {push_result.stderr.strip()}"
        )

    target_branch = base_ref.removeprefix("origin/")
    pr_title = f"{change_id}: Automated implementation"
    pr_description = (
        f"## {change_id}\n\n"
        "Automated implementation generated by the agent workflow runner.\n\n"
        f"Work item: #{work_item_id}\n"
    )

    az_bin = shutil.which("az") or "az"
    cmd = [
        az_bin,
        "repos",
        "pr",
        "create",
        "--repository",
        repo_name,
        "--source-branch",
        source_branch,
        "--target-branch",
        target_branch,
        "--title",
        pr_title,
        "--description",
        pr_description,
        "--work-items",
        work_item_id,
        "--org",
        org_url,
        "--project",
        project,
        "--output",
        "json",
    ]
    log("INFO", "Creating PR via az repos pr create")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    if result.returncode != 0:
        raise WorkflowError(
            f"az repos pr create failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
    try:
        pr_data = json.loads(result.stdout)
        pr_id = pr_data.get("pullRequestId", "?")
        pr_url = pr_data.get("url") or pr_data.get("repository", {}).get("remoteUrl", org_url)
        log("OK", f"PR created: #{pr_id}  url={pr_url}")
    except (json.JSONDecodeError, KeyError):
        log("OK", "PR created (could not parse PR URL from az output)")

