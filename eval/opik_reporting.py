"""Optional Opik reporting for single-agent evals.

This module is deliberately defensive: eval runs must still work when Opik is
not installed or the local Opik stack is not configured.
"""

from __future__ import annotations

import contextlib
from contextlib import nullcontext
from typing import Any, Mapping


class AgentEvalOpikReporter:
    """Small wrapper that logs eval metadata and feedback scores when enabled."""

    def __init__(self, *, enabled: bool, change_id: str = "", runner: str = "", model: str | None = None) -> None:
        self.enabled = bool(enabled)
        self.change_id = change_id
        self.runner = runner
        self.model = model
        self.tracer: Any | None = None
        if not enabled:
            return
        try:
            from server.config import load_config
            from core.opik_tracing import build_opik_tracer

            config = load_config()
            settings = config.get("opik") if isinstance(config, Mapping) else {}
            self.tracer = build_opik_tracer(
                settings=settings if isinstance(settings, dict) else {},
                change_id=change_id,
                runner=runner,
                model=model,
            )
            self.enabled = self.tracer is not None
        except Exception:
            self.tracer = None
            self.enabled = False

    @contextlib.contextmanager
    def span(self, name: str, metadata: Mapping[str, Any] | None = None):
        if not self.enabled or self.tracer is None:
            yield None
            return
        with maybe_trace(self.tracer, name, metadata=dict(metadata or {})) as span:
            yield span

    def build_tracer(self, *, change_id: str):
        self.change_id = change_id
        return self.tracer

    def record_case(self, result: Mapping[str, Any]) -> None:
        safe_update_current_trace(result=result)

    def record_result(self, result: Mapping[str, Any], metadata: Mapping[str, Any] | None = None) -> None:
        safe_update_current_trace(result=result, metadata=metadata)

    def flush(self, tracer: Any | None = None) -> None:
        flush_tracer(tracer if tracer is not None else self.tracer)


def build_agent_eval_tracer(*, enabled: bool, change_id: str, runner: str, model: str | None = None) -> AgentEvalOpikReporter:
    return AgentEvalOpikReporter(enabled=enabled, change_id=change_id, runner=runner, model=model)


def maybe_trace(tracer: Any | None, name: str, **kwargs: Any):
    if tracer is None:
        return nullcontext()
    trace = getattr(tracer, "trace", None)
    if callable(trace):
        try:
            return trace(name, **kwargs)
        except TypeError:
            try:
                return trace(name=name, **kwargs)
            except Exception:
                return nullcontext()
        except Exception:
            return nullcontext()
    span = getattr(tracer, "span", None)
    if callable(span):
        try:
            return span(name, metadata=kwargs.get("metadata"))
        except Exception:
            return nullcontext()
    return nullcontext()


def _feedback_scores(result: Mapping[str, Any] | None, score_detail: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    detail = dict(score_detail or {})
    if result is not None and not detail:
        detail = {
            "pass": bool(result.get("pass") or result.get("passed") or result.get("status") == "PASS"),
            "score": result.get("score_weighted") or result.get("weighted_score") or result.get("score") or 0.0,
            "components": result.get("scores") or result.get("components") or {},
            "gates": result.get("gates") or result.get("checks") or {},
        }
    scores = []
    if detail:
        scores.append({"name": "agent_eval_pass", "value": 1.0 if detail.get("pass") else 0.0})
        scores.append({"name": "agent_eval_score", "value": float(detail.get("score") or 0.0)})
        components = detail.get("components") or {}
        if isinstance(components, Mapping):
            for key, value in components.items():
                try:
                    scores.append({"name": f"agent_eval_component_{key}", "value": float(value)})
                except (TypeError, ValueError):
                    continue
        gates = detail.get("gates") or {}
        if isinstance(gates, Mapping):
            for key, value in gates.items():
                scores.append({"name": f"agent_eval_gate_{key}", "value": 1.0 if value else 0.0})
    return scores


def safe_update_current_trace(
    result: Mapping[str, Any] | None = None,
    *,
    metadata: Mapping[str, Any] | None = None,
    score_detail: Mapping[str, Any] | None = None,
    output: Any | None = None,
) -> None:
    try:
        from core.opik_compat import opik_context

        payload: dict[str, Any] = {}
        feedback_scores = _feedback_scores(result, score_detail)
        if feedback_scores:
            payload["feedback_scores"] = feedback_scores
        trace_metadata = dict(metadata or {})
        if result is not None:
            trace_metadata.setdefault("status", result.get("status"))
            trace_metadata.setdefault("case_id", result.get("case_id") or (result.get("metadata") or {}).get("case_id"))
        if trace_metadata:
            payload["metadata"] = trace_metadata
        if output is not None:
            payload["output"] = output
        if payload:
            opik_context.update_current_trace(**payload)
    except Exception:
        return


def flush_tracer(tracer: Any | None) -> None:
    if tracer is None:
        return
    for method_name in ("flush", "end"):
        method = getattr(tracer, method_name, None)
        if callable(method):
            try:
                method()
            except Exception:
                pass
            return
