# OpenAI-Compatible Provider Setup

The `openai-compat` provider lets you wire agent-runner agents to any LLM endpoint that speaks the OpenAI chat-completions API format. This works with local tools (LM Studio, llama.cpp server, vLLM, local chat servers) and remote services (OpenRouter, Groq, self-hosted proxies).

## Overview

The provider is not a built-in runner like `claude` or `copilot`. You define it as a **runner alias** in the agent-runner config file. Each alias maps a name to a provider, model, endpoint URL, and optional transport settings.

```yaml
runner_aliases:
  my-local-llm:
    provider: openai-compat
    model: llama3.1:8b
    base_url: http://127.0.0.1:1234/v1
```

After defining the alias, your runner appears in the GUI runner dropdown and can be used anywhere a runner name is expected.

## Configuration Reference

### Required fields

| Field | Type | Description |
|-------|------|-------------|
| `provider` | string | Must be `"openai-compat"` |
| `model` | string | Model name sent in the API payload. The runner prefixes this with `openai-compat/` internally (e.g., `"qwen3:32b"` becomes `"openai-compat/qwen3:32b"`) |
| `base_url` | string | Full HTTP(S) base URL of the chat API endpoint (e.g., `http://127.0.0.1:1234/v1`) |

### Optional transport fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `api_key_env` | string | — | Name of an environment variable holding an API key. Injected as `Authorization: Bearer <value>`. |
| `extra_headers` | object | — | Additional HTTP headers sent with every request. |
| `litellm_extra_body` | object | — | Extra JSON fields merged into the request body (for LiteLLM proxy features like `session`, `metadata`). |
| `num_retries` | integer | `8` | Max retry attempts on transient failures. |
| `retry_multiplier` | float | `2.0` | Backoff multiplier between retries. |
| `retry_min_wait` | float | `8` | Minimum wait in seconds before first retry. |
| `retry_max_wait` | float | `120` | Maximum wait in seconds between retries. |
| `timeout` | float | `420` | Request timeout in seconds. |

### Environment variable

| Variable | Description |
|----------|-------------|
| `OPENAI_COMPAT_HOST` | Overrides `base_url` for all openai-compat aliases. Takes precedence over per-alias `base_url`. Set this when all your openai-compat aliases share the same endpoint. |

## Examples

### Local endpoint (LM Studio, llama.cpp, local server)

```json
{
  "runner_aliases": {
    "local-llama": {
      "provider": "openai-compat",
      "model": "llama3.1:8b",
      "base_url": "http://127.0.0.1:1234/v1"
    }
  }
}
```

### Remote endpoint with API key

```json
{
  "runner_aliases": {
    "openrouter-haiku": {
      "provider": "openai-compat",
      "model": "anthropic/claude-3.5-haiku",
      "base_url": "https://openrouter.ai/api/v1",
      "api_key_env": "OPENROUTER_API_KEY",
      "extra_headers": {
        "HTTP-Referer": "https://agent-runner.local"
      }
    }
  }
}
```

Set the key before starting agent-runner:

```bash
export OPENROUTER_API_KEY="sk-or-v1-..."
```

### LiteLLM proxy with extra body

```json
{
  "runner_aliases": {
    "litellm-proxy": {
      "provider": "openai-compat",
      "model": "gpt-4o",
      "base_url": "https://llm-gateway.internal/v1",
      "api_key_env": "LITELLM_MASTER_KEY",
      "litellm_extra_body": {
        "metadata": {"team": "agent-workbench"}
      }
    }
  }
}
```

### Custom retry tuning

```json
{
  "runner_aliases": {
    "slow-self-hosted": {
      "provider": "openai-compat",
      "model": "mixtral:8x7b",
      "base_url": "http://10.0.0.50:8080/v1",
      "num_retries": 10,
      "retry_min_wait": 12,
      "retry_max_wait": 180,
      "timeout": 600
    }
  }
}
```

## Assigning to a Specific Agent

Use `agent_model_defaults` to pin an alias to a particular agent:

```json
{
  "runner_aliases": {
    "fast-coder": {
      "provider": "openai-compat",
      "model": "deepseek-coder:6.7b",
      "base_url": "http://127.0.0.1:1234/v1"
    }
  },
  "agent_model_defaults": {
    "software-engineer-hyperagent": {
      "fast-coder": "deepseek-coder:6.7b"
    }
  }
}
```

## Using OPENAI_COMPAT_HOST

When all your openai-compat aliases share the same endpoint, set the env var instead of repeating `base_url` in every alias:

```bash
export OPENAI_COMPAT_HOST="http://127.0.0.1:1234/v1"
```

```json
{
  "runner_aliases": {
    "llama-small": {
      "provider": "openai-compat",
      "model": "llama3.2:3b"
    },
    "llama-large": {
      "provider": "openai-compat",
      "model": "llama3.1:70b"
    }
  }
}
```

## Verification

1. Start your OpenAI-compatible server (LM Studio, llama.cpp, vLLM, etc.)
2. Configure the alias in agent-runner settings
3. Run a test with the new alias:
   ```bash
   python run.py --runner fast-coder --change-id TEST-001 --repo /tmp/test-repo
   ```
4. Check the agent runner GUI — your alias appears in the runner dropdown
5. The run log shows: `[openai-compat] API base URL: http://...`

## API Format

The provider sends requests to `<base_url>/api/chat` with this shape:

```json
{
  "model": "<stripped model name>",
  "stream": false,
  "messages": [
    {"role": "system", "content": "<agent system prompt>"},
    {"role": "user", "content": "<user prompt>"}
  ],
  "tools": [<function tool specs>]
}
```

The endpoint must accept standard OpenAI chat-completions format (specifically the `/api/chat` path with `model`, `messages`, `stream`, and optional `tools` fields). If the endpoint uses `/v1/chat/completions` instead of `/api/chat`, wrap it behind a reverse proxy that rewrites the path.
