"""Corpus route — eval/stories/*.json."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from server.corpus import list_corpus, get_corpus_item

router = APIRouter(prefix="/corpus", tags=["corpus"])


@router.get("")
def get_corpus() -> list[dict[str, Any]]:
    return list_corpus()


@router.get("/{story_id}")
def get_corpus_story(story_id: str) -> dict[str, Any]:
    item = get_corpus_item(story_id)
    if not item:
        raise HTTPException(status_code=404, detail="Story not found")
    return item
