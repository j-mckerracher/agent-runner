# OpenAI-Compatible Provider Setup

Agent Workbench has two OpenAI-compatible paths:

- The built-in `openai-compat` runner shells out to the [`omp`](https://omp.sh/docs) (oh-my-pi) CLI for each workflow step.
- Custom aliases with `"provider": "openai-compat"` still use the local/proxied HTTP `/api/chat` compatibility layer.

Use the built-in runner when you want omp to own provider selection, credentials, model defaults, and tool execution. Use aliases when you want Agent Workbench to call a specific HTTP gateway directly.

## Built-in runner: omp

The built-in runner name is `openai-compat`. It drives omp in headless RPC mode
(a long-running `--mode rpc` subprocess that exchanges newline-delimited JSON
over stdio):

```bash
omp --mode rpc --no-session --approval-mode yolo --no-extensions --cwd /absolute/path/to/repo [--model <id>]
```

The combined agent prompt is sent as a single `prompt` command and Agent
Workbench consumes omp's event stream until `agent_end`, assembling the final
answer from the returned assistant message (falling back to streamed
`text_delta` events). Tool executions (`tool_execution_start`/`_end`) are mapped
to `tool.start`/`tool.end` telemetry events, and interactive `extension_ui_request`
frames (`open_url` and selector/confirm/input prompts) are routed to the
human-escalation channel; status/widget UI pushes are ignored. `--approval-mode
yolo` keeps tool execution fully headless, and `--no-extensions` keeps output
deterministic (omp UI extensions such as status widgets do not alter the run).

Install and configure omp before using this runner. omp reads its own provider
credentials and default model from its normal configuration, including
`~/.omp/agent/agent.db` and config files managed by the omp CLI.

Agent Workbench only passes `--model` to omp when you explicitly provide a model through `--model` or a per-agent model override. When no model is specified, Agent Workbench omits the flag and omp uses its currently configured default model.

```bash
# Uses omp's configured default model.
python3 run.py --runner openai-compat --repo /absolute/path/to/repo

# Overrides the model for this run.
python3 run.py --runner openai-compat --model qwen3:32b --repo /absolute/path/to/repo
```

### Escalation MCP

Before launching omp, Agent Workbench idempotently registers its human-in-the-loop escalation MCP server in `~/.omp/agent/mcp.json`. omp auto-loads that user-scoped MCP config on each run, so escalation is available to the built-in `openai-compat` runner through omp's MCP tool surface. The registration preserves existing MCP servers.

## HTTP alias runtime behavior

The remaining sections apply only to aliases configured with `"provider": "openai-compat"`.

### Runtime endpoint behavior

At runtime, Agent Workbench chooses the API base URL in this order:

1. `OPENAI_COMPAT_HOST`, when set. This overrides every HTTP alias.
2. A runner alias `base_url`, but only when it points at `http://localhost`, `http://127.0.0.1`, or the same hosts over HTTPS.
3. The default local base URL: `http://127.0.0.1:11434`.

Non-local alias `base_url` values are accepted by settings validation for portability, but the current runtime ignores them unless you set `OPENAI_COMPAT_HOST`. For remote providers, use `OPENAI_COMPAT_HOST` or expose the provider through a local gateway/reverse proxy.

### API format

HTTP aliases send `POST <base_url>/api/chat` requests. The endpoint must accept this shape:

```json
{
  "model": "<model name without the openai-compat/ prefix>",
  "stream": false,
  "messages": [
    {"role": "system", "content": "<agent system prompt>"},
    {"role": "user", "content": "<user prompt>"}
  ],
  "tools": ["<function tool specs when tool use is enabled>"]
}
```

Services that expose only `/v1/chat/completions` need a compatibility proxy that rewrites `/api/chat` and maps the request/response shape. Do not point `base_url` directly at a plain `/v1` OpenAI-compatible endpoint unless that service also implements `/api/chat`.

## Runner aliases

Use aliases when you want friendly runner names, per-runner models, API keys, headers, retry settings, or local endpoint overrides. Aliases live in `~/.agent-runner/config.json` under `runner_aliases`.

```json
{
  "runner_aliases": {
    "local-qwen": {
      "provider": "openai-compat",
      "model": "qwen3:32b",
      "base_url": "http://127.0.0.1:11434"
    }
  }
}
```

Then run:

```bash
python3 run.py --runner local-qwen --repo /absolute/path/to/repo
```

## Configuration reference

### Required alias fields

| Field | Type | Description |
|---|---|---|
| `provider` | string | Must be `"openai-compat"`. |
| `model` | string | Model name sent in the request after removing any `openai-compat/` prefix. |

### Optional alias fields

| Field | Type | Default | Description |
|---|---|---|---|
| `base_url` | string | `http://127.0.0.1:11434` | Local `localhost` / `127.0.0.1` API base URL. Non-local values are ignored at runtime unless supplied through `OPENAI_COMPAT_HOST`. |
| `api_key_env` | string | — | Environment variable containing an API key. The runner sends it as `Authorization: Bearer <value>`. |
| `extra_headers` | object | — | Additional string headers sent with every request. |
| `litellm_extra_body` | object | — | Extra JSON fields merged into the request body for compatible gateways. |
| `num_retries` | integer | `8` | Max retry attempts on transient failures. |
| `retry_multiplier` | number | `2.0` | Backoff multiplier between retries. |
| `retry_min_wait` | number | `8` | Minimum wait in seconds before the first retry. |
| `retry_max_wait` | number | `120` | Maximum wait in seconds between retries. |
| `timeout` | number | `420` | Request timeout in seconds. |

## Remote provider pattern

For remote OpenAI-compatible providers such as OpenRouter, Groq, or a hosted LiteLLM proxy, put a small gateway in front of the service that exposes `/api/chat`, then point Agent Workbench at that gateway. The simplest supported override is `OPENAI_COMPAT_HOST`:

```bash
export OPENAI_COMPAT_HOST="https://llm-gateway.example.com"
export LLM_GATEWAY_API_KEY="..."
```

```json
{
  "runner_aliases": {
    "remote-qwen": {
      "provider": "openai-compat",
      "model": "qwen/qwen3-32b",
      "api_key_env": "LLM_GATEWAY_API_KEY"
    }
  }
}
```

The runtime will call `https://llm-gateway.example.com/api/chat`.

## Assigning an alias to a specific agent

Use `agent_model_defaults` to pin an alias/model pair to one agent:

```json
{
  "runner_aliases": {
    "fast-coder": {
      "provider": "openai-compat",
      "model": "deepseek-coder:6.7b",
      "base_url": "http://127.0.0.1:11434"
    }
  },
  "agent_model_defaults": {
    "software-engineer": {
      "fast-coder": "deepseek-coder:6.7b"
    }
  }
}
```

## Verification

### Built-in omp runner

1. Install and configure omp.
2. Run a smoke test:

   ```bash
   python3 run.py --runner openai-compat --change-id TEST-001 --repo /tmp/test-repo
   ```

3. If needed, pass an explicit model:

   ```bash
   python3 run.py --runner openai-compat --model qwen3:32b --change-id TEST-001 --repo /tmp/test-repo
   ```

4. Check `~/.omp/agent/mcp.json` for the `agent-workbench-escalation` MCP server if escalation should be available.

### HTTP alias

1. Start the local or proxied `/api/chat` service.
2. Configure `OPENAI_COMPAT_HOST` or a local alias `base_url`.
3. Run a smoke test through the alias:

   ```bash
    python3 run.py --runner local-qwen --change-id TEST-001 --repo /tmp/test-repo
   ```

4. Check the run log for `[openai-compat] API base URL: http://...`.
