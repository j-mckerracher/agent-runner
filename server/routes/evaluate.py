import logging
import json
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import evaluate
from ..jobs import manager
from core.runner_models import KNOWN_RUNNERS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/evaluate", tags=["evaluate"])


class BenchmarkRunSubmit(BaseModel):
    repo: str
    sha: str
    runner: str = "claude"
    model: Optional[str] = None
    difficulties: list[str] = Field(default_factory=lambda: ["easy", "medium", "hard"])
    runs: int = Field(1, ge=1)
    project_test_command: Optional[str] = None
    compare_to: Optional[str] = None


@router.get("/summary")
async def get_summary() -> dict:
    logger.debug("get_summary: computing evaluation summary")
    result = evaluate.summary()
    logger.info(
        "get_summary: overall_pass_rate=%s regressions=%s total_runs=%s",
        result.get("overall_pass_rate"), result.get("regressions"), result.get("total_runs"),
    )
    return result


@router.get("/reports")
async def get_eval_reports() -> dict:
    reports = evaluate.list_eval_reports()
    return {"count": len(reports), "items": reports}


@router.post("/benchmark-runs")
async def submit_benchmark_run(payload: BenchmarkRunSubmit) -> dict:
    from ..config import load_config

    cfg = load_config()
    valid_runners = set(KNOWN_RUNNERS) | set((cfg.get("runner_aliases") or {}).keys())
    if payload.runner not in valid_runners:
        logger.warning("submit_benchmark_run: invalid runner=%s", payload.runner)
        raise HTTPException(400, f"runner must be one of: {', '.join(sorted(valid_runners))}")
    difficulties = payload.difficulties or ["easy", "medium", "hard"]
    invalid = [item for item in difficulties if item not in {"easy", "medium", "hard"}]
    if invalid:
        raise HTTPException(400, f"invalid benchmark difficulties: {', '.join(invalid)}")
    args = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    args["difficulties"] = difficulties
    job_id = await manager().submit(
        {
            "repo": payload.repo,
            "change_id": "eval-benchmark",
            "runner": payload.runner,
            "model": payload.model,
            "mode": "live",
            "run_kind": "benchmark_evaluation",
            "eval_runner_args": json.dumps(args),
        }
    )
    logger.info("submit_benchmark_run: job submitted job_id=%s", job_id)
    return {"job_id": job_id, "run_kind": "benchmark_evaluation"}
