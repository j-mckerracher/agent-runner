"""Settings route — read/write config.json."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from server.config import load_config, save_config

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
def get_settings() -> dict[str, Any]:
    return load_config()


@router.put("")
def put_settings(body: dict) -> dict[str, Any]:
    try:
        save_config(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return load_config()
