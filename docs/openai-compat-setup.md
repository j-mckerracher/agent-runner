# OpenAI-Compatible Provider Setup

The `openai-compat` runner sends Agent Workbench prompts to a local or proxied HTTP endpoint that exposes the repository's expected `/api/chat` contract. It is a built-in runner, and you can also define runner aliases that use the same provider with different models or transport settings.

This runner is intentionally different from the `claude`, `codex`, `copilot`, and `gemini` CLI runners: it does not require a CLI, but it does require a reachable HTTP service.

## Runtime endpoint behavior

At runtime, Agent Workbench chooses the API base URL in this order:

1. `OPENAI_COMPAT_HOST`, when set. This overrides every alias and the built-in `openai-compat` runner.
2. A runner alias `base_url`, but only when it points at `http://localhost`, `http://127.0.0.1`, or the same hosts over HTTPS.
3. The default local base URL: `http://127.0.0.1:11434`.

Non-local alias `base_url` values are accepted by settings validation for portability, but the current runtime ignores them unless you set `OPENAI_COMPAT_HOST`. For remote providers, use `OPENAI_COMPAT_HOST` or expose the provider through a local gateway/reverse proxy.

## API format

The runner sends `POST <base_url>/api/chat` requests. The endpoint must accept this shape:

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

## Built-in runner

The built-in runner name is `openai-compat`. It uses the default model from `core/runner_models.py` unless you pass `--model` or configure an agent-specific default. Any model name is accepted for this runner; the listed presets are suggestions, not an allowlist.

```bash
export OPENAI_COMPAT_HOST="http://127.0.0.1:11434"
python3 run.py --runner openai-compat --model qwen3:32b --repo /absolute/path/to/repo
```

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
    "software-engineer-hyperagent": {
      "fast-coder": "deepseek-coder:6.7b"
    }
  }
}
```

## Verification

1. Start the local or proxied `/api/chat` service.
2. Configure `OPENAI_COMPAT_HOST` or a local alias `base_url`.
3. Run a smoke test:

   ```bash
   python3 run.py --runner openai-compat --model qwen3:32b --change-id TEST-001 --repo /tmp/test-repo
   ```

4. Check the run log for `[openai-compat] API base URL: http://...`.
