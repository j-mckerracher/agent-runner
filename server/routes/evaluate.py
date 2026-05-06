import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.workflow_inputs import resolve_workflow_input

from .. import evaluate
from .. import corpus
from ..jobs import manager
from core.runner_models import canonical_runner, RUNNER_DEFAULT_MODELS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/evaluate", tags=["evaluate"])


class EvaluationRunSubmit(BaseModel):
    repo: str
    story_id: Optional[str] = None
    change_id: Optional[str] = None
    runner: str = "claude"
    model: Optional[str] = None
    copilot_effort: Optional[str] = None
    mode: str = Field("live", pattern="^(live|hermetic)$")
    extra_context: Optional[str] = None
    skip_materialize: bool = False


@router.get("/summary")
async def get_summary() -> dict:
    logger.debug("get_summary: computing evaluation summary")
    result = evaluate.summary()
    logger.info(
        "get_summary: overall_pass_rate=%s regressions=%s total_runs=%s",
        result.get("overall_pass_rate"), result.get("regressions"), result.get("total_runs"),
    )
    return result


@router.post("/runs")
async def submit_evaluation_run(payload: EvaluationRunSubmit) -> dict:
    story_id = (payload.story_id or payload.change_id or "").strip()
    logger.info("submit_evaluation_run: story_id=%s runner=%s mode=%s", story_id, payload.runner, payload.mode)
    if not story_id:
        raise HTTPException(400, "story_id is required")
    if payload.story_id and payload.change_id and payload.story_id != payload.change_id:
        raise HTTPException(400, "story_id and change_id must match when both are provided")
    if canonical_runner(payload.runner) not in RUNNER_DEFAULT_MODELS:
        logger.warning("submit_evaluation_run: invalid runner=%s", payload.runner)
        raise HTTPException(400, "runner must be claude, copilot, gemini, or a copilot alias (copilot-<name>)")

    story = corpus.get_story(story_id)
    story_path = corpus.story_path_for(story_id)
    if story is None or story_path is None:
        logger.warning("submit_evaluation_run: story_id=%s not found", story_id)
        raise HTTPException(404, "evaluation story not found")

    try:
        resolve_workflow_input(
            repo=payload.repo,
            change_id=story["id"],
            story_file=str(story_path),
        )
        logger.debug("submit_evaluation_run: workflow input resolved successfully for story_id=%s", story_id)
    except (FileNotFoundError, ValueError) as exc:
        logger.warning("submit_evaluation_run: resolve_workflow_input failed: %s", exc)
        raise HTTPException(400, str(exc)) from exc

    job_id = await manager().submit({
        "repo": payload.repo,
        "change_id": story["id"],
        "runner": payload.runner,
        "model": payload.model,
        "copilot_effort": payload.copilot_effort,
        "mode": payload.mode,
        "ado_url": None,
        "story_file": str(story_path),
        "extra_context": payload.extra_context,
        "skip_materialize": payload.skip_materialize,
        "run_kind": "evaluation",
    })
    logger.info("submit_evaluation_run: job submitted job_id=%s story_id=%s", job_id, story_id)
    return {"job_id": job_id, "story_id": story["id"]}
