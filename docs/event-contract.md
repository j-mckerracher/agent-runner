# Event contract (v1)

Events are the canonical structured record of what happened during a
run. Consumers (harness, Langfuse exporter, Discord bot) rely on this
contract. Bumping `event_version` is a breaking change.

## Emission channels

1. **Stdout line**: `##EVENT## <json>\n`
2. **Event log**: JSONL appended to the `--event-log` path when supplied.

Both channels carry the same payload. Consumers should prefer the event
log when available and fall back to stdout parsing when not.

## Payload shape

```json
{
  "event_version": "1",
  "ts": "2026-04-16T12:34:56Z",
  "kind": "stage.start",
  "run_id": "run-abc123",
  "stage": "task_gen",
  "data": { "attempt": 1 }
}
```

Required fields: `event_version`, `ts`, `kind`.

## Known kinds

| Kind | Meaning |
| --- | --- |
| `run.start` / `run.end` | Run lifecycle boundaries. |
| `stage.start` / `stage.end` | Stage boundaries. |
| `eval.pass` / `eval.fail` | Evaluator stage outcome. |
| `retry` | A retriable failure triggered a retry. |
| `escalate` | Evaluator exceeded max attempts; pausing for human. |
| `artifact.write` | A stage artifact was written. |
| `agent_dispatch` / `agent_result` | Individual agent invocation bracket. |
| `evaluation_result` | Evaluator verdict payload. |
| `cassette.record` / `cassette.replay` / `cassette.miss` | LLM gateway cassette events. |
| `container.start` / `container.end` | Harness container lifecycle. |
| `workflow_error` | Unrecoverable workflow-level failure. |

Adding a new `kind` is **not** a breaking change. Adding required
fields inside `data` IS a breaking change unless nested under a new
key.

## Parser contract

A receiver MUST:

- Accept unknown `kind` values and pass them through unchanged.
- Reject any payload with `event_version != "1"`.
- Not assume `stage` or `run_id` are present on every event.

Reference emitter / parser: `agent_runner_shared.events`
(`emit_event_line`, `parse_event_line`).
