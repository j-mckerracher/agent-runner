from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


def clean_change_workspace(
    change_id: str,
    *,
    agent_context_root: Path,
    logs_root: Path,
    announce: Callable[[str], None] | None = None,
) -> None:
    """Remove stale artifact and log directories before a workflow starts."""
    logger.info("clean_change_workspace: change_id=%s", change_id)
    base = re.sub(r"-RUN-\d+$", "", change_id)
    is_isolated_run = base != change_id

    for root, label in ((agent_context_root, "workspace"), (logs_root, "log directory")):
        target = root / change_id
        if target.is_dir():
            logger.info("clean_change_workspace: removing stale %s %s", label, target)
            if announce is not None:
                announce(f"[cleanup] Removing stale {label}: {target.name}")
            shutil.rmtree(target)

    if is_isolated_run:
        return

    pattern = re.compile(rf"^{re.escape(base)}-RUN-\d+$")
    for root, label in ((agent_context_root, "workspace"), (logs_root, "log directory")):
        if not root.is_dir():
            continue
        for entry in root.iterdir():
            if entry.is_dir() and pattern.match(entry.name):
                logger.info("clean_change_workspace: removing stale multi-run %s %s", label, entry)
                if announce is not None:
                    announce(f"[cleanup] Removing stale multi-run {label}: {entry.name}")
                shutil.rmtree(entry)
