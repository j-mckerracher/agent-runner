import logging

from fastapi import APIRouter, HTTPException
from .. import registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("")
async def list_agents() -> dict:
    logger.debug("list_agents: enumerating agent registry")
    items = registry.list_agents()
    logger.debug("list_agents: found %d agent(s)", len(items))
    return {"items": items, "count": len(items)}


@router.get("/{name}")
async def get_agent(name: str) -> dict:
    logger.debug("get_agent: name=%s", name)
    item = registry.get_agent(name)
    if item is None:
        logger.warning("get_agent: name=%s not found", name)
        raise HTTPException(404, "agent not found")
    return item
