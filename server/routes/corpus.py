import logging

from fastapi import APIRouter, HTTPException
from .. import corpus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/corpus", tags=["corpus"])


@router.get("")
async def list_corpus() -> dict:
    logger.debug("list_corpus: listing all stories")
    items = corpus.list_stories()
    logger.debug("list_corpus: %d story(ies) found", len(items))
    return {"items": items, "count": len(items)}


@router.get("/{change_id}")
async def get_corpus(change_id: str) -> dict:
    logger.debug("get_corpus: change_id=%s", change_id)
    story = corpus.get_story(change_id)
    if story is None:
        logger.warning("get_corpus: change_id=%s not found", change_id)
        raise HTTPException(404, "story not found")
    logger.debug("get_corpus: change_id=%s found title=%r", change_id, story.get("title"))
    return story
