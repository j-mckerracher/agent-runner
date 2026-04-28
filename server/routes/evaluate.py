"""Evaluate route — aggregate metrics."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from server.evaluate import get_evaluate_summary

router = APIRouter(prefix="/evaluate", tags=["evaluate"])


@router.get("/summary")
def evaluate_summary() -> dict[str, Any]:
    return get_evaluate_summary()
