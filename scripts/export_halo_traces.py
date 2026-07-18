#!/usr/bin/env python3
"""Convert agent-workbench event logs into HALO-ready OpenTelemetry/OpenInference traces.

HALO (https://github.com/context-labs/halo) ingests a JSONL trace file where each
line is one OTLP-shaped span carrying the ``inference.*`` projection keys that the
HALO Engine indexes on. This adapter reads one or more agent-workbench
``events.jsonl`` logs and emits a ``traces.jsonl`` file HALO can analyze directly:

    python3 scripts/export_halo_traces.py \
        --events logs/TEST-AC-001/events.jsonl \
        --out traces.jsonl

    # or scan every run under logs/
    python3 scripts/export_halo_traces.py --events-root logs --out traces.jsonl

Then hand the output to HALO:

    halo traces.jsonl -p "Diagnose recurring agent failures and suggest prompt fixes"

Mapping (agent-workbench event -> HALO span):
    job.start / job.end       -> root ``custom.task``      (CHAIN / observation SPAN)
    stage.start / stage.end   -> ``agent.<stage>``          (AGENT)
    uow.start / uow.end       -> ``custom.uow``             (CHAIN)
    llm.call                  -> ``response``               (LLM)
    cli.invoke / cli.exit     -> ``function.<runner>``      (TOOL)

agent-workbench's ``llm.call`` events carry the raw ``prompt_text`` /
``response_text`` (plus token counts, ``status``, ``error_category`` and
``tool_call_count``). This adapter surfaces the message bodies as HALO's
OpenInference ``llm.input_messages`` / ``llm.output_messages`` (and
``input.value`` / ``output.value``) attributes, so the HALO Engine gets both
structural signal and full-content analysis. When an older event log has only
hashes, content attributes are simply omitted and HALO falls back to the
metadata-level signal.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# ID + timestamp helpers (match the shape of HALO's checked-in sample traces)
# ---------------------------------------------------------------------------

def _trace_id() -> str:
    """32 hex chars (16 bytes), matching HALO's sample export."""
    return uuid.uuid4().hex


def _span_id() -> str:
    """24 hex chars (12 bytes), matching HALO's sample export."""
    return uuid.uuid4().hex[:24]


def _to_otlp_time(ts: str | None) -> str:
    """Convert an agent-workbench ``ts`` to ISO-8601 with nanosecond precision + Z.

    agent-workbench writes ``%Y-%m-%dT%H:%M:%S.%fZ`` (microseconds). HALO's
    verifier requires 9 fractional digits and a trailing ``Z``.
    """
    if not ts:
        dt = datetime.now(timezone.utc)
    else:
        try:
            dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        except ValueError:
            try:
                dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                dt = datetime.now(timezone.utc)
    micros = dt.strftime("%f")  # 6 digits
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + micros + "000Z"


def _shift_time(ts: str | None, delta_ms: float) -> str:
    """Return an OTLP timestamp shifted by ``delta_ms`` (may be negative)."""
    if not ts:
        base = datetime.now(timezone.utc)
    else:
        try:
            base = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        except ValueError:
            base = datetime.now(timezone.utc)
    from datetime import timedelta

    shifted = base + timedelta(milliseconds=delta_ms)
    return shifted.strftime("%Y-%m-%dT%H:%M:%S.") + shifted.strftime("%f") + "000Z"


# ---------------------------------------------------------------------------
# Span construction
# ---------------------------------------------------------------------------

def _make_span(
    *,
    trace_id: str,
    span_id: str,
    parent_span_id: str,
    name: str,
    kind: str,
    start_time: str,
    end_time: str,
    span_kind: str,
    observation_kind: str,
    project_id: str,
    service_name: str,
    ok: bool = True,
    extra_attrs: dict[str, Any] | None = None,
    model: str | None = None,
    provider: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost: float | None = None,
    agent_name: str = "",
) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        "openinference.span.kind": span_kind,
        "inference.export.schema_version": 1,
        "inference.project_id": project_id,
        "inference.observation_kind": observation_kind,
        "inference.llm.provider": provider,
        "inference.llm.model_name": model,
        "inference.llm.input_tokens": input_tokens,
        "inference.llm.output_tokens": output_tokens,
        "inference.llm.cost.total": cost,
        "inference.user_id": None,
        "inference.session_id": None,
        "inference.agent_name": agent_name,
    }
    if extra_attrs:
        attributes.update(extra_attrs)
    return {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "trace_state": "",
        "name": name,
        "kind": kind,
        "start_time": start_time,
        "end_time": end_time,
        "status": {
            "code": "STATUS_CODE_OK" if ok else "STATUS_CODE_ERROR",
            "message": "" if ok else "agent-workbench reported failure",
        },
        "resource": {"attributes": {"service.name": service_name}},
        "scope": {"name": "agent-workbench", "version": "1"},
        "attributes": attributes,
    }


# ---------------------------------------------------------------------------
# Core conversion for a single run (one events.jsonl -> list of spans)
# ---------------------------------------------------------------------------

def convert_run(events: list[dict[str, Any]], project_id: str) -> list[dict[str, Any]]:
    if not events:
        return []

    events = sorted(events, key=lambda e: e.get("seq", 0))
    trace_id = _trace_id()
    service_name = str(
        events[0].get("change_id")
        or events[0].get("job_id")
        or "agent-workbench-run"
    )

    spans: list[dict[str, Any]] = []
    first_ts = events[0].get("ts")
    last_ts = events[-1].get("ts")

    # Root task span spanning the whole run.
    root_id = _span_id()
    job_ok = True
    job_start = first_ts
    job_end = last_ts
    for e in events:
        if e.get("type") == "job.start":
            job_start = e.get("ts", job_start)
        elif e.get("type") == "job.end":
            job_end = e.get("ts", job_end)
            job_ok = str(e.get("status", "")).lower() not in {"error", "failed", "failure"}

    # Parent stack: (span_id, kind_label). Leaf spans attach to the top.
    stack: list[str] = [root_id]
    # Open paired spans keyed by their natural key -> (span_id, start_ts).
    open_stages: dict[str, tuple[str, str | None]] = {}
    open_uows: dict[str, tuple[str, str | None]] = {}
    open_clis: dict[tuple[str, str], tuple[str, str | None]] = {}

    def current_parent() -> str:
        return stack[-1]

    for e in events:
        etype = e.get("type")
        ts = e.get("ts")

        if etype == "stage.start":
            stage = str(e.get("stage") or "stage")
            sid = _span_id()
            open_stages[stage] = (sid, ts)
            stack.append(sid)

        elif etype == "stage.end":
            stage = str(e.get("stage") or "stage")
            sid, start_ts = open_stages.pop(stage, (_span_id(), ts))
            if sid in stack:
                stack.remove(sid)
            ok = str(e.get("kind", "")).lower() not in {"error", "failed", "failure"} and \
                str(e.get("status", "")).lower() not in {"error", "failed", "failure"}
            spans.append(_make_span(
                trace_id=trace_id, span_id=sid, parent_span_id=root_id,
                name=f"agent.{stage}", kind="SPAN_KIND_INTERNAL",
                start_time=_to_otlp_time(start_ts), end_time=_to_otlp_time(ts),
                span_kind="AGENT", observation_kind="AGENT",
                project_id=project_id, service_name=service_name, ok=ok,
                agent_name=stage,
                extra_attrs={"agent.name": stage},
            ))

        elif etype == "uow.start":
            uow = str(e.get("uow_id") or e.get("uow") or "uow")
            sid = _span_id()
            open_uows[uow] = (sid, ts)
            stack.append(sid)

        elif etype == "uow.end":
            uow = str(e.get("uow_id") or e.get("uow") or "uow")
            sid, start_ts = open_uows.pop(uow, (_span_id(), ts))
            parent = stack[-2] if len(stack) >= 2 and stack[-1] == sid else current_parent()
            if sid in stack:
                stack.remove(sid)
            ok = str(e.get("status", "")).lower() not in {"error", "failed", "failure"}
            spans.append(_make_span(
                trace_id=trace_id, span_id=sid, parent_span_id=parent,
                name="custom.uow", kind="SPAN_KIND_INTERNAL",
                start_time=_to_otlp_time(start_ts), end_time=_to_otlp_time(ts),
                span_kind="CHAIN", observation_kind="SPAN",
                project_id=project_id, service_name=service_name, ok=ok,
                extra_attrs={"uow.id": uow},
            ))

        elif etype == "llm.call":
            duration_ms = float(e.get("duration_ms") or 0.0)
            status = str(e.get("status", "ok"))
            ok = status in {"ok", "tool_call"}
            llm_attrs: dict[str, Any] = {
                "llm.provider": e.get("runner"),
                "llm.model_name": e.get("model"),
                "aw.agent": e.get("agent"),
                "aw.status": status,
                "aw.error_category": e.get("error_category"),
                "aw.retryable": e.get("retryable"),
                "aw.attempt": e.get("attempt"),
                "aw.tool_call_count": e.get("tool_call_count"),
                "aw.tool_step_count": e.get("tool_step_count"),
                "aw.prompt_sha256": e.get("prompt_sha256"),
                "aw.response_sha256": e.get("response_sha256"),
            }
            # Raw message bodies are now carried on the event; surface them as
            # HALO's OpenInference content attributes so the Engine can read them.
            prompt_text = e.get("prompt_text")
            response_text = e.get("response_text")
            if prompt_text is not None:
                llm_attrs["input.value"] = prompt_text
                llm_attrs["llm.input_messages"] = json.dumps(
                    [{"message.role": "user", "message.content": prompt_text}],
                    ensure_ascii=False,
                )
            if response_text is not None:
                llm_attrs["output.value"] = response_text
                llm_attrs["llm.output_messages"] = json.dumps(
                    [{"message.role": "assistant", "message.content": response_text}],
                    ensure_ascii=False,
                )
            spans.append(_make_span(
                trace_id=trace_id, span_id=_span_id(), parent_span_id=current_parent(),
                name="response", kind="SPAN_KIND_CLIENT",
                start_time=_shift_time(ts, -duration_ms), end_time=_to_otlp_time(ts),
                span_kind="LLM", observation_kind="LLM",
                project_id=project_id, service_name=service_name, ok=ok,
                model=e.get("model"), provider=e.get("runner"),
                input_tokens=e.get("tokens_in") or e.get("prompt_est_tokens"),
                output_tokens=e.get("tokens_out") or e.get("response_est_tokens"),
                cost=e.get("cost_usd"),
                agent_name=str(e.get("agent") or ""),
                extra_attrs=llm_attrs,
            ))

        elif etype == "cli.invoke":
            key = (str(e.get("runner")), str(e.get("agent")))
            open_clis[key] = (_span_id(), ts)

        elif etype == "cli.exit":
            key = (str(e.get("runner")), str(e.get("agent")))
            sid, start_ts = open_clis.pop(key, (_span_id(), ts))
            exit_code = e.get("exit_code")
            ok = (exit_code == 0) or exit_code is None
            runner = e.get("runner") or "cli"
            spans.append(_make_span(
                trace_id=trace_id, span_id=sid, parent_span_id=current_parent(),
                name=f"function.{runner}", kind="SPAN_KIND_INTERNAL",
                start_time=_to_otlp_time(start_ts), end_time=_to_otlp_time(ts),
                span_kind="TOOL", observation_kind="TOOL",
                project_id=project_id, service_name=service_name, ok=ok,
                agent_name=str(e.get("agent") or ""),
                extra_attrs={
                    "tool.name": f"{runner}:{e.get('agent')}",
                    "aw.exit_code": exit_code,
                    "aw.duration_ms": e.get("duration_ms"),
                },
            ))

    # Root span last so children reference a stable id.
    spans.append(_make_span(
        trace_id=trace_id, span_id=root_id, parent_span_id="",
        name="custom.task", kind="SPAN_KIND_INTERNAL",
        start_time=_to_otlp_time(job_start), end_time=_to_otlp_time(job_end),
        span_kind="CHAIN", observation_kind="SPAN",
        project_id=project_id, service_name=service_name, ok=job_ok,
        extra_attrs={"aw.change_id": service_name},
    ))
    return spans


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def _read_events(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _iter_event_files(events: list[str], events_root: str | None) -> Iterable[Path]:
    for p in events:
        yield Path(p)
    if events_root:
        for p in sorted(Path(events_root).rglob("events.jsonl")):
            yield p


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--events", action="append", default=[],
                        help="Path to an events.jsonl file (repeatable).")
    parser.add_argument("--events-root",
                        help="Directory to scan recursively for events.jsonl files.")
    parser.add_argument("--out", default="traces.jsonl",
                        help="Output HALO trace file (default: traces.jsonl).")
    parser.add_argument("--project-id", default="agent-workbench",
                        help="inference.project_id stamped on every span.")
    args = parser.parse_args(argv)

    files = list(_iter_event_files(args.events, args.events_root))
    if not files:
        parser.error("provide at least one --events file or an --events-root directory")

    total_spans = 0
    total_runs = 0
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as out_fh:
        for path in files:
            if not path.exists():
                print(f"skip (missing): {path}", file=sys.stderr)
                continue
            events = _read_events(path)
            spans = convert_run(events, args.project_id)
            if not spans:
                print(f"skip (no spans): {path}", file=sys.stderr)
                continue
            for span in spans:
                out_fh.write(json.dumps(span, ensure_ascii=False) + "\n")
            total_spans += len(spans)
            total_runs += 1
            print(f"ok: {path} -> {len(spans)} spans", file=sys.stderr)

    print(f"wrote {total_spans} spans from {total_runs} run(s) to {out_path}", file=sys.stderr)
    return 0 if total_runs else 1


if __name__ == "__main__":
    raise SystemExit(main())
