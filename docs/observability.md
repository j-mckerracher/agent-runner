# Observability integration (Langfuse)

The runner ships with an optional Langfuse integration in
`packages/runner/agent_runner/integrations/observability.py`.  It is a
*soft* dependency: importing the module is always safe even when the
`langfuse` package is not installed — the module simply returns a
no-op sink in that case.

## Activation

The integration is activated by setting **either** of these environment
variables before launching the runner:

| Variable | Effect |
|---|---|
| `AGENT_RUNNER_OBSERVABILITY=langfuse` | Explicitly enable Langfuse export. |
| `LANGFUSE_PUBLIC_KEY=<key>` | Presence alone enables Langfuse (key is passed to the SDK). |

The SDK also reads `LANGFUSE_SECRET_KEY` and `LANGFUSE_HOST` from the
environment in the normal Langfuse way.

## What gets exported

Every structured event emitted by `_emit_structured_event` in `engine.py`
is forwarded to Langfuse via `record_observability_event`:

| Event kind | Langfuse construct |
|---|---|
| `workflow_start` | Opens a new **trace** |
| `workflow_complete` | Closes the trace and calls `flush()` |
| `stage_start` / `stage_complete` | Opens/closes a **span** per stage |
| `uow_start` / `uow_complete` | Opens/closes a child span per unit-of-work |
| `agent_dispatch` / `agent_result` | Opens/closes a leaf span per agent call |
| `eval_attempt` | Records an instant span |
| `escalation_start` | Records an instant span |
| `workflow_paused` / `workflow_resumed` | Records instant spans |
| All other events | Attached as metadata annotations on the trace |

## Verifying the integration

1. Install langfuse: `pip install langfuse`.
2. Export `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`.
3. Run any workflow — traces appear in the Langfuse UI under the project
   associated with the key.

To confirm the module loads safely without langfuse installed:

```python
from agent_runner.integrations.observability import record_observability_event
# No exception raised; events are silently dropped via the NullObservabilitySink.
```

See `packages/runner/tests/test_observability_import.py` for the
automated import-safety test.
