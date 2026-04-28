"""Agents route — reads agent-sources/ registry."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from server.registry import list_agents

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("")
def get_agents() -> list[dict[str, Any]]:
    return list_agents()
