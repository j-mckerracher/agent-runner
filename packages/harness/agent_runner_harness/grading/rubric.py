"""Rubric grader — calls a judge LLM with a rubric prompt.

The judge function is injected so that tests can stub it without
importing any LLM SDK. In production it calls OpenAI or Azure OpenAI.
When AGENT_RUNNER_JUDGE_STUB=1 the built-in stub is used.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agent_runner_shared.models import AcceptanceCriterion
from agent_runner_harness.grading.prompts import render_rubric_prompt


@dataclass
class JudgeConfig:
    """Configuration for the rubric judge."""
    timeout_seconds: float = 60.0
    max_retries: int = 2
    retry_backoff_seconds: float = 2.0


_EXPECTED_SCHEMA_KEYS = {"score", "rationale"}


def _stub_judge(prompt: str, rubric_text: str) -> dict[str, Any]:
    """Stub judge that returns a fixed score without calling any LLM."""
    return {"score": 2, "rationale": "stub", "evidence_refs": []}


def _make_openai_judge(model: str, base_url: str | None = None) -> Callable[[str, str], dict[str, Any]]:
    """Create a judge function backed by OpenAI-compatible API."""
    def _judge(prompt: str, rubric_text: str) -> dict[str, Any]:
        try:
            import openai  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "openai package required for rubric grading; install it or set AGENT_RUNNER_JUDGE_STUB=1"
            ) from exc

        client_kwargs: dict[str, Any] = {}
        if base_url:
            client_kwargs["base_url"] = base_url

        client = openai.OpenAI(**client_kwargs)
        full_prompt = f"{prompt}\n\n## Rubric\n{rubric_text}"
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": full_prompt}],
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content or "{}")

    return _judge


def _validate_judge_response(raw: dict[str, Any]) -> bool:
    """Return True if the judge response has the required schema."""
    return "score" in raw and "rationale" in raw


def _call_judge_with_retry(
    fn: Callable[[str, str], dict[str, Any]],
    prompt: str,
    rubric_text: str,
    config: JudgeConfig,
) -> dict[str, Any]:
    """Call the judge with timeout and retry logic.

    Returns the parsed dict on success.
    Raises one of:
      - TimeoutError: if the call exceeds ``config.timeout_seconds``.
      - ValueError: if JSON parse fails or schema is wrong.
      - Exception: transport/other errors after all retries exhausted.
    """
    import threading

    last_exc: Exception | None = None
    for attempt in range(config.max_retries + 1):
        if attempt > 0:
            time.sleep(config.retry_backoff_seconds * (2 ** (attempt - 1)))

        result_holder: list[Any] = []
        exc_holder: list[Exception] = []

        def _run() -> None:
            try:
                result_holder.append(fn(prompt, rubric_text))
            except Exception as e:
                exc_holder.append(e)

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout=config.timeout_seconds)

        if t.is_alive():
            raise TimeoutError(
                f"Judge call timed out after {config.timeout_seconds}s"
            )

        if exc_holder:
            last_exc = exc_holder[0]
            # Only retry on transport-like errors, not on value/type errors
            continue

        raw = result_holder[0]
        return raw

    raise last_exc or RuntimeError("Judge call failed after all retries")


def grade_rubric(
    criteria: list[AcceptanceCriterion],
    artifact_dir: Path,
    judge_fn: Callable[[str, str], dict[str, Any]] | None = None,
    *,
    stub: bool = False,
    task: Any = None,
    event_log_excerpt: str = "",
    judge_config: JudgeConfig | None = None,
) -> list[dict[str, Any]]:
    """Evaluate rubric acceptance criteria using a judge LLM.

    Args:
        criteria: List of AcceptanceCriterion with kind=='rubric'.
        artifact_dir: Directory containing run artifacts.
        judge_fn: Injectable judge function. Signature: (prompt, rubric_text) -> dict.
                  If None, uses stub or OpenAI depending on environment.
        stub: If True, always use the stub judge (overrides env var).
        task: Task object or description string (used in prompt rendering).
        event_log_excerpt: Excerpt from the run event log for context.
        judge_config: Timeout/retry configuration. Defaults to JudgeConfig().

    Returns:
        List of result dicts. Successful entries have keys:
            id, kind, passed, score, rationale, evidence_refs.
        Error entries have keys:
            id, kind, status="judge_error", error_kind, raw, passed=False, score=None.
    """
    if stub or os.environ.get("AGENT_RUNNER_JUDGE_STUB") == "1":
        fn = _stub_judge
    elif judge_fn is not None:
        fn = judge_fn
    else:
        raise ValueError("No judge_fn provided and AGENT_RUNNER_JUDGE_STUB is not set")

    config = judge_config or JudgeConfig()
    results: list[dict[str, Any]] = []

    for criterion in criteria:
        if criterion.kind != "rubric":
            continue

        # Build a short artifact summary
        artifact_files = list(artifact_dir.glob("**/*")) if artifact_dir.exists() else []
        artifact_summary = ", ".join(
            str(f.relative_to(artifact_dir)) for f in artifact_files if f.is_file()
        ) or "(no artifacts)"

        task_desc = task if task is not None else ""
        prompt = render_rubric_prompt(criterion, task_desc, artifact_summary, event_log_excerpt)
        rubric_text = criterion.judge_prompt or criterion.description

        try:
            raw = _call_judge_with_retry(fn, prompt, rubric_text, config)
        except TimeoutError as exc:
            results.append({
                "id": criterion.id,
                "kind": "rubric",
                "status": "judge_error",
                "error_kind": "timeout",
                "raw": str(exc)[:500],
                "passed": False,
                "score": None,
            })
            continue
        except Exception as exc:
            results.append({
                "id": criterion.id,
                "kind": "rubric",
                "status": "judge_error",
                "error_kind": "transport",
                "raw": str(exc)[:500],
                "passed": False,
                "score": None,
            })
            continue

        # Validate JSON schema
        if not isinstance(raw, dict) or not _validate_judge_response(raw):
            results.append({
                "id": criterion.id,
                "kind": "rubric",
                "status": "judge_error",
                "error_kind": "invalid_json",
                "raw": str(raw)[:500],
                "passed": False,
                "score": None,
            })
            continue

        score = float(raw.get("score", 0))
        threshold = float(criterion.threshold or 0)
        passed = score >= threshold

        results.append({
            "id": criterion.id,
            "kind": "rubric",
            "passed": passed,
            "score": score,
            "rationale": raw.get("rationale", ""),
            "evidence_refs": raw.get("evidence_refs", []),
        })

    return results
