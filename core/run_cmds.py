import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .agent_prompts import load_agent_system_prompt
from .materialized_paths import normalize_runner, runner_skill_dir
from .runner_models import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_COPILOT_MODEL,
    is_copilot_runner,
    _provider_for_runner,
    resolve_runner_transport_config,
)
from .ui_trace_bridge import track_with_ui

logger = logging.getLogger(__name__)

_TRANSIENT_COPILOT_ERROR_MARKERS = (
    "http2: server sent goaway",
    "stream error",
    "internal_error",
    "connection reset",
    "connection refused",
    "temporarily unavailable",
    "timed out",
    "timeout",
    "rate limit",
    "too many requests",
    "429",
    "500",
    "502",
    "503",
    "504",
    "unavailable",
    "eof",
)
_COPILOT_MAX_ATTEMPTS = 5
_COPILOT_REFUSAL_MARKERS = (
    "i'm sorry, but i cannot assist with that request",
    "i cannot assist with that request",
    "i can't assist with that request",
)
_COPILOT_EMBEDDED_AGENT_FALLBACK: dict[str, bool] = {}
_OPENAI_COMPAT_LOCAL_API_DEFAULT = "http://127.0.0.1:11434"
_OPENAI_COMPAT_TRANSIENT_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
_OPENAI_COMPAT_MAX_TOOL_STEPS = 40
_OPENAI_COMPAT_TOOL_RESULT_LIMIT = 12000
_OPENAI_COMPAT_RUNNER_ROOT = Path(__file__).resolve().parent.parent
_OPENAI_COMPAT_AGENT_CONTEXT_ROOT = _OPENAI_COMPAT_RUNNER_ROOT / "agent-context"
_FORBIDDEN_WRITE_PATH_PARTS = frozenset({".git", "node_modules", "dist", "build"})
_FORBIDDEN_WRITE_FILE_NAMES = frozenset({
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "cargo.lock",
    "poetry.lock",
    "composer.lock",
    "go.sum",
})
_FORBIDDEN_SHELL_TOKENS = (
    "curl ",
    "wget ",
    "scp ",
    "ssh ",
    "telnet ",
    "nc ",
    "${var@P}",
    "${!var}",
)


# ─────────────────────────────────────────────────────────────────────────────
# Optional event emission + cassette recording.
# Both are activated only when the corresponding env var is set, so the CLI
# remains side-effect-free for direct users.
# ─────────────────────────────────────────────────────────────────────────────


def _emit_event(type: str, **fields) -> None:
    if not os.environ.get("AGENT_RUNNER_EVENT_LOG"):
        return
    try:
        from server.events import emit
        emit(type, **fields)
    except Exception:
        pass


def _estimate_tokens(text: str) -> int:
    """Rough token estimate when the runner does not provide usage."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def _record_cassette(**fields) -> None:
    if not os.environ.get("AGENT_RUNNER_CASSETTE"):
        return
    try:
        from server.cassette import record
        record(**fields)
    except Exception:
        pass


def _load_runtime_config() -> dict:
    try:
        from server.config import load_config
        return load_config()
    except Exception:
        return {}


def _last_nonempty_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _summarize_cli_failure(result: subprocess.CompletedProcess) -> str:
    detail = _last_nonempty_line(result.stderr or "") or _last_nonempty_line(result.stdout or "")
    if detail:
        return detail[:500]
    return f"Process exited with code {result.returncode}"


def _truncate_output(text: str | None, limit: int = _OPENAI_COMPAT_TOOL_RESULT_LIMIT) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n...[truncated]"


def _strip_openai_compat_model_prefix(model: str) -> str:
    if not model:
        raise ValueError("OpenAI-compatible model must not be empty")
    if model.startswith("openai-compat/"):
        return model.split("/", 1)[1]
    return model


def _normalize_openai_compat_base_url(raw: str | None) -> str:
    value = (raw or "").strip()
    if not value:
        return _OPENAI_COMPAT_LOCAL_API_DEFAULT
    if "://" not in value:
        value = f"http://{value}"
    return value.rstrip("/")


def _resolve_openai_compat_base_url(transport: dict) -> str:
    env_host = os.environ.get("OPENAI_COMPAT_HOST")
    if env_host:
        return _normalize_openai_compat_base_url(env_host)

    configured = transport.get("base_url")
    if isinstance(configured, str) and configured.strip():
        if re.match(r"^https?://(127\.0\.0\.1|localhost)(:\d+)?/?$", configured.strip()):
            return _normalize_openai_compat_base_url(configured)
        else:
            logger.warning(
                "_resolve_openai_compat_base_url: non-local base_url=%r ignored (only localhost/127.0.0.1 supported); "
                "using default %s. Set OPENAI_COMPAT_HOST env var to override.",
                configured.strip(), _OPENAI_COMPAT_LOCAL_API_DEFAULT,
            )

    return _OPENAI_COMPAT_LOCAL_API_DEFAULT


def _openai_compat_headers(transport: dict) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    extra_headers = transport.get("extra_headers")
    if isinstance(extra_headers, dict):
        for key, value in extra_headers.items():
            if isinstance(key, str) and isinstance(value, str):
                headers[key] = value
    api_key = transport.get("api_key")
    if isinstance(api_key, str) and api_key:
        headers.setdefault("Authorization", f"Bearer {api_key}")
    return headers


def _openai_compat_request(path: str, payload: dict, *, transport: dict) -> dict:
    base_url = _resolve_openai_compat_base_url(transport)
    headers = _openai_compat_headers(transport)
    body = json.dumps(payload).encode("utf-8")
    timeout_s = float(transport.get("timeout") or 120)
    retries = int(transport.get("num_retries") or 0)
    retry_multiplier = float(transport.get("retry_multiplier") or 2.0)
    retry_min_wait = float(transport.get("retry_min_wait") or 2.0)
    retry_max_wait = float(transport.get("retry_max_wait") or 30.0)
    url = f"{base_url}{path}"

    logger.info("_openai_compat_request: %s url=%s timeout=%.0fs retries=%d payload_keys=%s",
                path, url, timeout_s, retries, list(payload.keys()))
    logger.debug("_openai_compat_request: payload model=%s stream=%s messages=%d",
                 payload.get("model"), payload.get("stream"), len(payload.get("messages", [])))

    for attempt in range(retries + 1):
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            logger.debug("_openai_compat_request: attempt %d/%d url=%s", attempt + 1, retries + 1, url)
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                response_body = response.read().decode("utf-8")
            parsed = json.loads(response_body)
            logger.debug("_openai_compat_request: response keys=%s", list(parsed.keys()) if parsed else "EMPTY")
            _record_cassette(
                cmd=["openai-compat-api", path],
                stdin=payload,
                stdout=parsed,
                stderr=None,
                exit_code=0,
                duration_ms=0,
                stage="openai-compat-api",
                extra={"url": url},
            )
            return parsed
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            is_transient = exc.code in _OPENAI_COMPAT_TRANSIENT_STATUS_CODES
            logger.warning(
                "_openai_compat_request: HTTP %s for %s attempt %d/%d is_transient=%s detail=%s",
                exc.code, path, attempt + 1, retries + 1, is_transient, detail[:300],
            )
            if is_transient and attempt < retries:
                delay = min(retry_max_wait, retry_min_wait * (retry_multiplier ** attempt))
                logger.warning(
                    "_openai_compat_request: transient HTTP %s for %s attempt %d/%d; retrying in %.1fs",
                    exc.code, path, attempt + 1, retries + 1, delay,
                )
                time.sleep(delay)
                continue
            raise RuntimeError(f"OpenAI-compatible API {path} failed with HTTP {exc.code}: {detail[:500]}") from exc
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "_openai_compat_request: %s error for %s attempt %d/%d: %s",
                type(exc).__name__, path, attempt + 1, retries + 1, exc,
            )
            if attempt < retries:
                delay = min(retry_max_wait, retry_min_wait * (retry_multiplier ** attempt))
                logger.warning(
                    "_openai_compat_request: transient error for %s attempt %d/%d (%s); retrying in %.1fs",
                    path, attempt + 1, retries + 1, exc, delay,
                )
                time.sleep(delay)
                continue
            raise RuntimeError(f"OpenAI-compatible API {path} failed: {exc}") from exc

    raise RuntimeError(f"OpenAI-compatible API {path} exhausted retries")


def _emit_openai_compat_metrics(response: dict, *, model: str) -> None:
    usage = response.get("usage") or {}
    prompt_tokens = int(
        usage.get("prompt_tokens")
        or response.get("prompt_tokens")
        or response.get("prompt_eval_count")
        or 0
    )
    completion_tokens = int(
        usage.get("completion_tokens")
        or response.get("completion_tokens")
        or response.get("eval_count")
        or 0
    )
    if prompt_tokens or completion_tokens:
        _emit_event("metrics", tokens_in=prompt_tokens, tokens_out=completion_tokens, cost_usd=0.0)
        logger.debug(
            "_emit_openai_compat_metrics: model=%s prompt_tokens=%d completion_tokens=%d",
            model,
            prompt_tokens,
            completion_tokens,
        )


def _openai_compat_chat(
    *,
    model: str,
    messages: list[dict],
    transport: dict,
    tools: list[dict] | None = None,
) -> dict:
    stripped = _strip_openai_compat_model_prefix(model)
    payload: dict[str, object] = {
        "model": stripped,
        "stream": False,
        "messages": messages,
    }
    if tools:
        payload["tools"] = tools
    extra_body = transport.get("litellm_extra_body")
    if isinstance(extra_body, dict):
        payload.update(extra_body)

    # Summarize messages for logging (avoid logging full system prompts every call)
    msg_summary = []
    for m in messages:
        role = str(m.get("role", "?"))
        content_len = len(str(m.get("content", "")))
        has_tool_calls = bool(m.get("tool_calls"))
        tool_name = str(m.get("tool_name", "")) if role == "tool" else ""
        msg_summary.append(f"{role}(content={content_len}, tc={has_tool_calls}, tn={tool_name})")

    logger.info("_openai_compat_chat: model=%s messages=%d tools=%d", stripped, len(messages), len(tools or []))
    logger.debug("_openai_compat_chat: message summary: %s", " | ".join(msg_summary))
    logger.debug("_openai_compat_chat: last message role=%s content_preview=%s",
                 msg_summary[-1] if msg_summary else "none",
                 str(messages[-1].get("content", ""))[:200] if messages else "none")

    response = _openai_compat_request("/api/chat", payload, transport=transport)
    logger.debug("_openai_compat_chat: response keys=%s", list(response.keys()) if response else "EMPTY")
    _emit_openai_compat_metrics(response, model=model)
    return response


def _openai_compat_show_capabilities(*, model: str, transport: dict) -> list[str]:
    response = _openai_compat_request(
        "/api/show",
        {"model": _strip_openai_compat_model_prefix(model)},
        transport=transport,
    )
    capabilities = response.get("capabilities") or []
    return [cap for cap in capabilities if isinstance(cap, str)]


def run_openai_compat_text(
    *,
    prompt: str,
    system_prompt: str,
    model: str,
    runner: str,
) -> str:
    if not prompt:
        raise ValueError("prompt must not be empty")
    logger.info("run_openai_compat_text: model=%s runner=%s prompt_len=%d system_prompt_len=%d",
                model, runner, len(prompt), len(system_prompt))
    config = _load_runtime_config()
    transport = resolve_runner_transport_config(runner, config=config) if runner != "openai-compat" else {}
    base_url = _resolve_openai_compat_base_url(transport)
    logger.info("run_openai_compat_text: base_url=%s", base_url)
    logger.debug("run_openai_compat_text: system_prompt first 300 chars: %s", system_prompt[:300])
    logger.debug("run_openai_compat_text: user prompt first 300 chars: %s", prompt[:300])
    try:
        response = _openai_compat_chat(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            transport=transport,
        )
    except Exception as exc:
        logger.error("run_openai_compat_text: _openai_compat_chat FAILED: %s: %s", type(exc).__name__, exc)
        raise
    message = response.get("message") or {}
    text = str(message.get("content") or "")
    logger.info("run_openai_compat_text: response content_len=%d", len(text))
    logger.debug("run_openai_compat_text: response content: %s", text[:500])
    return text


def _forbidden_write_path(path: Path) -> str | None:
    lower_name = path.name.lower()
    if lower_name in _FORBIDDEN_WRITE_FILE_NAMES:
        return f"writing lock files is not allowed: {path.name}"
    if any(part in _FORBIDDEN_WRITE_PATH_PARTS for part in path.parts):
        return f"writing to generated or VCS directories is not allowed: {path}"
    if any(marker in lower_name for marker in (".env", "secret", "credential", "password")):
        return f"writing sensitive files is not allowed: {path.name}"
    return None


class _OpenaiCompatToolRuntime:
    def __init__(self, *, repo: str | None, change_id: str | None) -> None:
        self.repo = Path(repo).expanduser().resolve() if repo else None
        self.change_dir = (_OPENAI_COMPAT_AGENT_CONTEXT_ROOT / change_id).resolve() if change_id else None
        self.read_roots = [root for root in (_OPENAI_COMPAT_RUNNER_ROOT, _OPENAI_COMPAT_AGENT_CONTEXT_ROOT, self.change_dir, self.repo) if root is not None]
        self.write_roots = [root for root in (self.change_dir, self.repo) if root is not None]

    @property
    def tool_specs(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "list_dir",
                    "description": "List files and directories under an allowed path.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "max_depth": {"type": "integer"},
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a text file from an allowed path with optional line bounds.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "start_line": {"type": "integer"},
                            "end_line": {"type": "integer"},
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "Write a UTF-8 text file to an allowed path, creating parent directories when needed.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["path", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "run_shell",
                    "description": "Run a local shell command within an allowed working directory. Use this for git, rg, tests, or build commands.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string"},
                            "cwd": {"type": "string"},
                            "timeout_seconds": {"type": "integer"},
                        },
                        "required": ["command"],
                    },
                },
            },
        ]

    def _resolve_path(self, raw_path: str, *, allow_write: bool) -> Path:
        if not raw_path or not isinstance(raw_path, str):
            raise ValueError("path must be a non-empty string")
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            base = self.repo or self.change_dir or _OPENAI_COMPAT_RUNNER_ROOT
            candidate = base / candidate
        resolved = candidate.resolve()
        allowed_roots = self.write_roots if allow_write else self.read_roots
        if not any(resolved == root or root in resolved.parents for root in allowed_roots):
            raise ValueError(f"path is outside allowed roots: {resolved}")
        if allow_write:
            forbidden_reason = _forbidden_write_path(resolved)
            if forbidden_reason:
                raise ValueError(forbidden_reason)
        return resolved

    def _list_dir(self, args: dict) -> dict:
        path = self._resolve_path(str(args.get("path") or ""), allow_write=False)
        if not path.exists():
            raise FileNotFoundError(f"path does not exist: {path}")
        if not path.is_dir():
            raise ValueError(f"path is not a directory: {path}")
        max_depth = max(0, min(int(args.get("max_depth") or 1), 3))
        entries: list[dict[str, str]] = []

        def walk(current: Path, depth: int) -> None:
            for child in sorted(current.iterdir(), key=lambda item: item.name):
                if child.name.startswith("."):
                    continue
                rel_path = child.relative_to(path)
                entries.append(
                    {
                        "path": str(rel_path),
                        "type": "dir" if child.is_dir() else "file",
                    }
                )
                if child.is_dir() and depth < max_depth:
                    walk(child, depth + 1)

        walk(path, 0)
        return {"path": str(path), "entries": entries}

    def _read_file(self, args: dict) -> dict:
        path = self._resolve_path(str(args.get("path") or ""), allow_write=False)
        if not path.is_file():
            raise FileNotFoundError(f"file does not exist: {path}")
        start_line = max(1, int(args.get("start_line") or 1))
        end_line = int(args.get("end_line") or (start_line + 249))
        if end_line < start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        lines = path.read_text(encoding="utf-8").splitlines()
        selected = lines[start_line - 1:end_line]
        content = "\n".join(f"{index}: {line}" for index, line in enumerate(selected, start=start_line))
        return {
            "path": str(path),
            "start_line": start_line,
            "end_line": min(end_line, len(lines)),
            "content": content,
        }

    def _write_file(self, args: dict) -> dict:
        path = self._resolve_path(str(args.get("path") or ""), allow_write=True)
        content = args.get("content")
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {
            "path": str(path),
            "bytes_written": len(content.encode("utf-8")),
        }

    def _run_shell(self, args: dict) -> dict:
        command = str(args.get("command") or "").strip()
        if not command:
            raise ValueError("command must not be empty")
        lowered = command.lower()
        if any(token in lowered for token in _FORBIDDEN_SHELL_TOKENS) or "http://" in lowered or "https://" in lowered:
            raise ValueError("network or shell-obfuscation commands are not allowed")
        cwd_raw = args.get("cwd")
        cwd = self._resolve_path(str(cwd_raw), allow_write=False) if cwd_raw else (self.repo or self.change_dir or _OPENAI_COMPAT_RUNNER_ROOT)
        if not cwd.is_dir():
            raise ValueError(f"cwd is not a directory: {cwd}")
        timeout_seconds = max(1, min(int(args.get("timeout_seconds") or 90), 300))
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
        combined_output = _truncate_output(f"{result.stdout or ''}{result.stderr or ''}".strip())
        return {
            "cwd": str(cwd),
            "returncode": result.returncode,
            "output": combined_output,
        }

    def execute(self, tool_name: str, arguments: dict) -> str:
        logger.info("_OpenaiCompatToolRuntime.execute: tool=%s", tool_name)
        try:
            if tool_name == "list_dir":
                result = self._list_dir(arguments)
            elif tool_name == "read_file":
                result = self._read_file(arguments)
            elif tool_name == "write_file":
                result = self._write_file(arguments)
            elif tool_name == "run_shell":
                result = self._run_shell(arguments)
            else:
                result = {"error": f"unknown tool: {tool_name}"}
        except Exception as exc:
            logger.warning("_OpenaiCompatToolRuntime.execute: tool=%s failed: %s", tool_name, exc)
            result = {"error": f"{type(exc).__name__}: {exc}"}
        return json.dumps(result, ensure_ascii=False)


def _openai_compat_agent_instructions(*, agent: str, runner: str, extra_skills: list[str] | None, repo: str | None, change_id: str | None) -> str:
    allowed_write_paths = []
    if change_id:
        allowed_write_paths.append(str((_OPENAI_COMPAT_AGENT_CONTEXT_ROOT / change_id).resolve()))
    if repo:
        allowed_write_paths.append(str(Path(repo).expanduser().resolve()))
    write_scope = ", ".join(allowed_write_paths) if allowed_write_paths else "no write roots configured"
    logger.info("_openai_compat_agent_instructions: agent=%s runner=%s change_id=%s repo=%s write_scope=%s",
                agent, runner, change_id, repo, write_scope)
    logger.debug("_openai_compat_agent_instructions: extra_skills=%s", extra_skills)
    base_instructions = build_runner_agent_instructions(agent, runner=runner, extra_skills=extra_skills)
    logger.info("_openai_compat_agent_instructions: base agent instructions length=%d chars", len(base_instructions))
    result = (
        f"{base_instructions}\n\n"
        "## Additional runner instructions\n"
        "- You only have the attached function tools for side effects. Use them instead of describing hypothetical actions.\n"
        "- If the original prompt mentions native CLI features or sub-agents that are unavailable here, perform the work yourself with the provided tools.\n"
        f"- Allowed write roots: {write_scope}\n"
        "- Do not make network requests or access secrets.\n"
        "- Before your final answer, ensure required files are actually written to disk.\n"
    )
    logger.info("_openai_compat_agent_instructions: total instructions length=%d chars", len(result))
    return result


def is_transient_runner_failure_text(text: str, *, runner: str | None = None) -> bool:
    normalized = text.lower()
    if runner is None or is_copilot_runner(runner):
        return any(marker in normalized for marker in _TRANSIENT_COPILOT_ERROR_MARKERS)
    return False


def _run_cli(
    cmd: list[str],
    *,
    runner: str,
    agent: str,
    env: dict | None = None,
    stream_output: bool = False,
) -> subprocess.CompletedProcess:
    """Wrapper around subprocess.run that emits structured events / cassette records.

    Behavior is identical to subprocess.run when AGENT_RUNNER_EVENT_LOG and
    AGENT_RUNNER_CASSETTE are unset.
    """
    logger.info("_run_cli: runner=%s agent=%s cmd=%s", runner, agent, cmd[0])
    logger.debug("_run_cli: full cmd=%s", cmd)
    _emit_event(
        "cli.invoke",
        runner=runner,
        agent=agent,
        cmd=list(cmd[:1]),
        argc=len(cmd) - 1,
    )
    start = time.monotonic()
    result = _run_cli_live(cmd, env=env) if stream_output else _run_cli_captured(cmd, env=env)
    duration_ms = int((time.monotonic() - start) * 1000)
    logger.info(
        "_run_cli: runner=%s agent=%s exit_code=%d duration_ms=%d",
        runner, agent, result.returncode, duration_ms,
    )
    _emit_event(
        "cli.exit",
        runner=runner,
        agent=agent,
        exit_code=result.returncode,
        duration_ms=duration_ms,
    )
    if result.returncode != 0:
        summary = _summarize_cli_failure(result)
        logger.error(
            "_run_cli: FAILED runner=%s agent=%s exit_code=%d: %s",
            runner, agent, result.returncode, summary,
        )
        _emit_event(
            "log",
            level="error",
            kind="command_failed",
            runner=runner,
            agent=agent,
            msg=f"{agent} command failed (exit {result.returncode}): {summary}",
        )
    else:
        logger.debug("_run_cli: SUCCESS runner=%s agent=%s", runner, agent)
    _record_cassette(
        cmd=cmd,
        stdin=None,
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.returncode,
        duration_ms=duration_ms,
        stage=agent,
        extra={"runner": runner},
    )
    return result


def _run_cli_captured(cmd: list[str], *, env: dict | None = None) -> subprocess.CompletedProcess:
    if env is None:
        return subprocess.run(cmd, text=True, capture_output=True)
    return subprocess.run(cmd, text=True, capture_output=True, env=env)


def _run_cli_live(cmd: list[str], *, env: dict | None = None) -> subprocess.CompletedProcess:
    process = subprocess.Popen(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    def _pump(pipe, chunks: list[str], target) -> None:
        if pipe is None:
            return
        try:
            for line in iter(pipe.readline, ""):
                chunks.append(line)
                print(line, end="", file=target, flush=True)
        finally:
            pipe.close()

    stdout_thread = threading.Thread(target=_pump, args=(process.stdout, stdout_chunks, sys.stdout))
    stderr_thread = threading.Thread(target=_pump, args=(process.stderr, stderr_chunks, sys.stderr))
    stdout_thread.start()
    stderr_thread.start()
    returncode = process.wait()
    stdout_thread.join()
    stderr_thread.join()
    return subprocess.CompletedProcess(
        args=cmd,
        returncode=returncode,
        stdout="".join(stdout_chunks),
        stderr="".join(stderr_chunks),
    )

CLAUDE_AUTH_ENV_VARS = frozenset({
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_API_KEY",
})


def _without_claude_auth_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in CLAUDE_AUTH_ENV_VARS:
        env.pop(key, None)
    logger.debug("_without_claude_auth_env: stripped %s from env", CLAUDE_AUTH_ENV_VARS)
    return env


def _load_skill_content(skill_name: str, *, runner: str = "copilot") -> str | None:
    """Load skill content from the selected runner's materialized skills directory."""
    skill_file = runner_skill_dir(runner) / skill_name / "SKILL.md"
    if skill_file.exists():
        content = skill_file.read_text(encoding="utf-8")
        logger.debug(
            "_load_skill_content: loaded skill=%s runner=%s (%d chars)",
            skill_name,
            normalize_runner(runner),
            len(content),
        )
        return content
    logger.debug(
        "_load_skill_content: skill file not found for %s at %s (runner=%s)",
        skill_name,
        skill_file,
        normalize_runner(runner),
    )
    return None


def _extract_required_skills(agent_spec: str) -> list[str]:
    """Extract skill names from the Required Skills table in an agent spec."""
    skills: list[str] = []
    in_skills_section = False
    for line in agent_spec.split("\n"):
        if "## Required Skills" in line:
            in_skills_section = True
            continue
        if in_skills_section:
            if line.startswith("##"):
                break
            match = re.search(r"\|\s*\*\*([a-zA-Z0-9\-]+)\*\*", line)
            if match:
                skills.append(match.group(1))
    logger.debug("_extract_required_skills: found skills=%s", skills)
    return skills


def _merge_skill_names(agent_spec: str, extra_skills: list[str] | None = None) -> list[str]:
    merged: list[str] = []
    for skill_name in [*_extract_required_skills(agent_spec), *(extra_skills or [])]:
        if skill_name not in merged:
            merged.append(skill_name)
    logger.debug("_merge_skill_names: merged skills=%s", merged)
    return merged


def _render_embedded_skills(skill_names: list[str], *, runner: str) -> str:
    if not skill_names:
        return ""

    skill_blocks: list[str] = []
    for skill_name in skill_names:
        content = _load_skill_content(skill_name, runner=runner)
        if content:
            skill_blocks.append(f"### Skill: {skill_name}\n\n{content}")
        else:
            logger.warning(
                "_render_embedded_skills: skill=%s not found for runner=%s; skipping embed",
                skill_name,
                normalize_runner(runner),
            )

    if not skill_blocks:
        return ""

    logger.debug(
        "_render_embedded_skills: embedding %d skill(s) for runner=%s",
        len(skill_blocks),
        normalize_runner(runner),
    )
    return (
        "\n\n## Embedded Skill References\n\n"
        "The following skills are embedded for your use. Follow each skill's "
        "protocol as instructed by the agent specification above.\n\n"
        + "\n\n---\n\n".join(skill_blocks)
    )


def _build_embedded_agent_prompt(
    prompt: str,
    agent: str,
    *,
    runner: str,
    extra_skills: list[str] | None = None,
) -> str:
    agent_instructions = build_runner_agent_instructions(
        agent,
        runner=runner,
        extra_skills=extra_skills,
    )
    return (
        f"You are running as the '{agent}' specialist in the agent-runner workflow.\n"
        f"Treat the following agent specification as your governing instructions for this run.\n\n"
        f"## Agent specification\n{agent_instructions}\n\n"
        f"## Task to execute\n{prompt}"
    )


def build_runner_agent_instructions(
    agent: str,
    *,
    runner: str,
    extra_skills: list[str] | None = None,
) -> str:
    """Return the runner-specific materialized agent prompt plus embedded skills."""
    agent_prompt = load_agent_system_prompt(agent, runner=runner)
    skill_names = _merge_skill_names(agent_prompt, extra_skills)
    return f"{agent_prompt}{_render_embedded_skills(skill_names, runner=runner)}"


def _build_gemini_prompt(prompt: str, agent: str, extra_skills: list[str] | None = None) -> str:
    """
    Build a combined prompt for Gemini CLI headless mode.

    Gemini has no native activate_skill mechanism, so both the runner-specific
    materialized agent prompt and its required skills are embedded directly.
    """
    logger.debug("_build_gemini_prompt: agent=%s extra_skills=%s", agent, extra_skills)
    combined = _build_embedded_agent_prompt(
        prompt=prompt,
        agent=agent,
        runner="gemini",
        extra_skills=extra_skills,
    )
    logger.debug("_build_gemini_prompt: combined prompt length=%d chars for agent=%s", len(combined), agent)
    return combined


def _looks_like_copilot_refusal(text: str | None) -> bool:
    normalized = (text or "").strip().lower()
    return any(marker in normalized for marker in _COPILOT_REFUSAL_MARKERS)


def _build_copilot_command(
    *,
    cli_cmd: str,
    prompt: str,
    agent: str,
    model: str,
    silent: bool,
    stream_output: bool,
    skip_permissions: bool,
    extra_flags: list[str] | None,
    use_custom_agent: bool,
) -> list[str]:
    cmd = [cli_cmd, "-p", prompt]
    if use_custom_agent:
        cmd.append(f"--agent={agent}")
    if cli_cmd == "copilot":
        cmd.extend(["--model", model])
    if silent:
        cmd.append("-s")
    if stream_output and not (extra_flags and "--stream" in extra_flags):
        cmd.extend(["--stream", "on"])
    if skip_permissions:
        cmd.append("--yolo")
    if extra_flags:
        cmd.extend(extra_flags)
    return cmd

def run_claude_cmd(
    prompt: str,
    agent: str,
    model: str = "claude-haiku-4-5-20251001",
    skip_permissions: bool = True,
    stream_output: bool = False,
    extra_flags: list[str] | None = None,
) -> str:
    """
    Trigger Claude Code via the CLI and return stdout.

    Args:
        prompt: The prompt or ADO URL to pass with -p.
        agent: The --agent value.
        model: The --model value.
        skip_permissions: Whether to include --dangerously-skip-permissions.
        extra_flags: Any additional CLI flags to append.

    Returns:
        stdout from the completed process.
    """
    if not prompt:
        raise ValueError(f"prompt must not be empty (agent={agent})")
    logger.info("run_claude_cmd: agent=%s model=%s prompt_len=%d", agent, model, len(prompt))
    if not shutil.which("ztk"):
        logger.warning("run_claude_cmd: ztk not found — Bash output compression disabled (install: brew install codejunkie99/ztk/ztk)")
    print(f"Starting Claude Code via {agent}...")
    print(f"Prompt: {prompt}")
    print(f"Model: {model}")
    cmd = ["claude", "-p", prompt, "--agent", agent, "--model", model, "--output-format", "json"]
    if skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    if extra_flags:
        cmd.extend(extra_flags)
    result = _run_cli(cmd, runner="claude", agent=agent, stream_output=stream_output)
    stdout_raw = result.stdout or ""
    text_out = stdout_raw
    try:
        parsed = json.loads(stdout_raw)
        ti = int(parsed.get("total_input_tokens") or 0)
        to = int(parsed.get("total_output_tokens") or 0)
        cu = float(parsed.get("cost_usd") or 0.0)
        if ti == 0 and to == 0:
            ti = _estimate_tokens(prompt)
            to = _estimate_tokens(str(parsed.get("result") or stdout_raw))
        if cu == 0.0 and (ti > 0 or to > 0):
            cu = round((ti / 1_000_000 * 3.0) + (to / 1_000_000 * 15.0), 6)
        if ti > 0 or to > 0:
            _emit_event("metrics", tokens_in=ti, tokens_out=to, cost_usd=cu)
        text_out = str(parsed.get("result") or stdout_raw)
    except (json.JSONDecodeError, ValueError, TypeError):
        logger.warning("run_claude_cmd: could not parse JSON output for agent=%s", agent)
        ti = _estimate_tokens(prompt)
        to = _estimate_tokens(text_out)
        if ti > 0 or to > 0:
            _emit_event("metrics", tokens_in=ti, tokens_out=to, cost_usd=0.0)
    if text_out:
        logger.debug("run_claude_cmd: stdout length=%d for agent=%s", len(text_out), agent)
        print(text_out)
    if result.stderr:
        logger.debug("run_claude_cmd: stderr length=%d for agent=%s", len(result.stderr), agent)
        print(result.stderr)
    if result.returncode != 0:
        logger.error("run_claude_cmd: agent=%s exited %d", agent, result.returncode)
        raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)
    logger.info("run_claude_cmd: agent=%s completed OK", agent)
    return text_out


def run_claude(
    prompt: str,
    agent: str,
    model: str = "claude-haiku-4-5-20251001",
    skip_permissions: bool = True,
    stream_output: bool = False,
    extra_flags: list[str] | None = None,
) -> str:
    """Wrapper around run_claude_cmd."""
    return run_claude_cmd(prompt=prompt, agent=agent, model=model,
                          stream_output=stream_output,
                          skip_permissions=skip_permissions, extra_flags=extra_flags)


def run_copilot_cmd(
    prompt: str,
    agent: str,
    model: str = "gpt-5-mini",
    skip_permissions: bool = True,
    silent: bool = True,
    stream_output: bool = False,
    extra_flags: list[str] | None = None,
    cli_cmd: str = "copilot",
    extra_skills: list[str] | None = None,
) -> str:
    """
    Trigger GitHub Copilot CLI non-interactively and return stdout.

    Args:
        prompt: The prompt to pass with -p.
        agent: The --agent=<name> value.
        model: The --model value. Only passed for the base 'copilot' command;
               alias binaries (e.g. 'copilot-gemma4') are self-contained and
               do not accept a --model flag.
        silent: When True, passes -s to suppress usage info from stdout.
        cli_cmd: The actual binary to invoke. Defaults to 'copilot'. For alias
                 runners (e.g. runner='copilot-gemma4') pass 'copilot-gemma4'
                 here — the binary IS the model configuration.
        extra_flags: Any additional CLI flags to append.

    Returns:
        stdout from the completed process.
        :param skip_permissions:
    """
    if not prompt:
        raise ValueError(f"prompt must not be empty (agent={agent})")
    logger.info("run_copilot_cmd: cli_cmd=%s agent=%s model=%s prompt_len=%d", cli_cmd, agent, model, len(prompt))
    print(f"Starting Copilot CLI via {agent} (cmd={cli_cmd})...")
    print(f"Prompt: {prompt}")
    if cli_cmd == "copilot":
        print(f"Model: {model}")
    use_custom_agent = not _COPILOT_EMBEDDED_AGENT_FALLBACK.get(cli_cmd, False)
    active_prompt = prompt
    if not use_custom_agent:
        active_prompt = _build_embedded_agent_prompt(
            prompt=prompt,
            agent=agent,
            runner="copilot",
            extra_skills=extra_skills,
        )
        logger.info(
            "run_copilot_cmd: using embedded-agent fallback for cli_cmd=%s agent=%s",
            cli_cmd,
            agent,
        )
    for attempt in range(_COPILOT_MAX_ATTEMPTS):
        logger.debug("run_copilot_cmd: attempt %d/%d agent=%s cli_cmd=%s", attempt + 1, _COPILOT_MAX_ATTEMPTS, agent, cli_cmd)
        cmd = _build_copilot_command(
            cli_cmd=cli_cmd,
            prompt=active_prompt,
            agent=agent,
            model=model,
            silent=silent,
            stream_output=stream_output,
            skip_permissions=skip_permissions,
            extra_flags=extra_flags,
            use_custom_agent=use_custom_agent,
        )
        attempt_result = _run_cli(
            cmd,
            runner=cli_cmd,
            agent=agent,
            env=_without_claude_auth_env(),
            stream_output=stream_output,
        )
        if attempt_result.stdout:
            logger.debug("run_copilot_cmd: stdout length=%d for agent=%s", len(attempt_result.stdout), agent)
            print(attempt_result.stdout)
        if attempt_result.stderr:
            logger.debug("run_copilot_cmd: stderr length=%d for agent=%s", len(attempt_result.stderr), agent)
            print(attempt_result.stderr)
        if attempt_result.returncode == 0:
            if use_custom_agent and _looks_like_copilot_refusal(attempt_result.stdout):
                logger.warning(
                    "run_copilot_cmd: custom agent=%s via %s returned a refusal; switching to embedded-agent fallback",
                    agent,
                    cli_cmd,
                )
                print("[run_copilot_cmd] Custom agent returned a refusal. Retrying with embedded agent instructions...")
                _COPILOT_EMBEDDED_AGENT_FALLBACK[cli_cmd] = True
                use_custom_agent = False
                active_prompt = _build_embedded_agent_prompt(
                    prompt=prompt,
                    agent=agent,
                    runner="copilot",
                    extra_skills=extra_skills,
                )
                continue
            logger.info("run_copilot_cmd: agent=%s completed OK on attempt %d", agent, attempt + 1)
            ti = _estimate_tokens(active_prompt)
            to = _estimate_tokens(attempt_result.stdout or "")
            if ti > 0 or to > 0:
                _emit_event("metrics", tokens_in=ti, tokens_out=to, cost_usd=0.0)
            return attempt_result.stdout
        combined = (attempt_result.stdout or "") + (attempt_result.stderr or "")
        is_transient = is_transient_runner_failure_text(combined, runner=cli_cmd)
        if is_transient and attempt < _COPILOT_MAX_ATTEMPTS - 1:
            delay = 5 * (2 ** attempt)
            logger.warning(
                "run_copilot_cmd: transient error on attempt %d/%d for agent=%s via %s; retrying in %ds",
                attempt + 1,
                _COPILOT_MAX_ATTEMPTS,
                agent,
                cli_cmd,
                delay,
            )
            print(f"[run_copilot_cmd] Transient error (attempt {attempt + 1}/{_COPILOT_MAX_ATTEMPTS}). Retrying in {delay}s...")
            time.sleep(delay)
            continue
        logger.error("run_copilot_cmd: agent=%s exited %d after %d attempt(s)", agent, attempt_result.returncode, attempt + 1)
        raise subprocess.CalledProcessError(
            attempt_result.returncode,
            attempt_result.args,
            attempt_result.stdout,
            attempt_result.stderr,
        )
    return ""  # unreachable


def run_gemini_cmd(
    prompt: str,
    agent: str,
    model: str = DEFAULT_GEMINI_MODEL,
    skip_permissions: bool = True,
    output_format: str = "text",
    stream_output: bool = False,
    extra_flags: list[str] | None = None,
    extra_skills: list[str] | None = None,
) -> str:
    """
    Trigger Gemini CLI non-interactively and return stdout.

    Gemini does not expose a top-level --agent flag in the installed CLI, so
    the materialized agent prompt is injected into the headless prompt payload.
    Required skills are embedded directly since Gemini has no activate_skill mechanism.
    """
    if not prompt:
        raise ValueError(f"prompt must not be empty (agent={agent})")
    logger.info("run_gemini_cmd: agent=%s model=%s prompt_len=%d", agent, model, len(prompt))
    combined_prompt = _build_gemini_prompt(prompt=prompt, agent=agent, extra_skills=extra_skills)
    print(f"Starting Gemini CLI via {agent}...")
    print(f"Prompt: {prompt}")
    print(f"Model: {model}")
    cmd = ["gemini", "-p", combined_prompt, "--model", model, "--output-format", output_format]
    if skip_permissions:
        cmd.append("--yolo")
    if extra_flags:
        cmd.extend(extra_flags)
    for _attempt in range(5):
        logger.debug("run_gemini_cmd: attempt %d/5 agent=%s", _attempt + 1, agent)
        attempt_result = _run_cli(
            cmd,
            runner="gemini",
            agent=agent,
            env=_without_claude_auth_env(),
            stream_output=stream_output,
        )
        if attempt_result.stdout:
            logger.debug("run_gemini_cmd: stdout length=%d for agent=%s", len(attempt_result.stdout), agent)
            print(attempt_result.stdout)
        if attempt_result.stderr:
            logger.debug("run_gemini_cmd: stderr length=%d for agent=%s", len(attempt_result.stderr), agent)
            print(attempt_result.stderr)
        if attempt_result.returncode == 0:
            logger.info("run_gemini_cmd: agent=%s completed OK on attempt %d", agent, _attempt + 1)
            ti = _estimate_tokens(combined_prompt)
            to = _estimate_tokens(attempt_result.stdout or "")
            if ti > 0 or to > 0:
                _emit_event("metrics", tokens_in=ti, tokens_out=to, cost_usd=0.0)
            return attempt_result.stdout
        combined = (attempt_result.stdout or "") + (attempt_result.stderr or "")
        is_transient = any(
            marker in combined
            for marker in ("503", "UNAVAILABLE", "high demand", "rate limit", "429")
        )
        if is_transient and _attempt < 4:
            delay = 60 * (2 ** _attempt)
            logger.warning(
                "run_gemini_cmd: transient error on attempt %d/5 for agent=%s; retrying in %ds",
                _attempt + 1, agent, delay,
            )
            print(f"[run_gemini_cmd] Transient error (attempt {_attempt + 1}/5). Retrying in {delay}s...")
            time.sleep(delay)
        else:
            logger.error(
                "run_gemini_cmd: agent=%s failed after %d attempt(s) exit_code=%d",
                agent, _attempt + 1, attempt_result.returncode,
            )
            raise subprocess.CalledProcessError(
                attempt_result.returncode,
                attempt_result.args,
                attempt_result.stdout,
                attempt_result.stderr,
            )
    return ""  # unreachable


def run_openai_compat_cmd(
    prompt: str,
    agent: str,
    model: str,
    skip_permissions: bool = True,
    output_format: str = "text",
    stream_output: bool = False,
    extra_flags: list[str] | None = None,
    runner: str = "openai-compat",
    extra_skills: list[str] | None = None,
    repo: str | None = None,
    change_id: str | None = None,
) -> str:
    """
    Run an OpenAI-compatible-backed agent via the local OpenAI-compatible chat API and a bounded tool loop.
    """
    if not prompt:
        raise ValueError(f"prompt must not be empty (agent={agent})")
    logger.info("run_openai_compat_cmd: START agent=%s model=%s runner=%s prompt_len=%d repo=%s change_id=%s",
                agent, model, runner, len(prompt), repo, change_id)
    if output_format != "text":
        raise ValueError("run_openai_compat_cmd currently supports only text output")
    if extra_flags:
        logger.debug("run_openai_compat_cmd: ignoring unsupported extra_flags=%s", extra_flags)
    if not skip_permissions:
        logger.debug("run_openai_compat_cmd: skip_permissions=False ignored; local tool allowlists are always enforced")
    print(f"Starting OpenAI-compatible API via {agent}...")
    print(f"Prompt: {prompt}")
    print(f"Model: {model}")
    print(f"Runner: {runner}")

    # Resolve transport config
    logger.info("run_openai_compat_cmd: loading runtime config for transport resolution")
    config = _load_runtime_config()
    logger.debug("run_openai_compat_cmd: runtime config keys=%s", list(config.keys()) if config else "EMPTY")
    transport = resolve_runner_transport_config(runner, config=config) if runner != "openai-compat" else {}
    base_url = _resolve_openai_compat_base_url(transport)
    stripped_model = _strip_openai_compat_model_prefix(model)
    logger.info("run_openai_compat_cmd: resolved base_url=%s stripped_model=%s transport_keys=%s",
                base_url, stripped_model, list(transport.keys()))
    print(f"[openai-compat] API base URL: {base_url}")
    print(f"[openai-compat] Stripped model: {stripped_model}")

    # Check capabilities
    logger.info("run_openai_compat_cmd: checking model capabilities for model=%s", stripped_model)
    try:
        capabilities = _openai_compat_show_capabilities(model=model, transport=transport)
        logger.info("run_openai_compat_cmd: capabilities=%s", capabilities)
        print(f"[openai-compat] Model capabilities: {capabilities}")
    except Exception as exc:
        logger.error("run_openai_compat_cmd: capabilities check FAILED: %s: %s", type(exc).__name__, exc)
        print(f"[openai-compat] ERROR checking capabilities: {type(exc).__name__}: {exc}")
        raise

    if "tools" not in capabilities:
        logger.error("run_openai_compat_cmd: model '%s' does not support tools; capabilities=%s", stripped_model, capabilities)
        raise RuntimeError(
            f"OpenAI-compatible model '{stripped_model}' does not advertise tool support; "
            f"capabilities={capabilities}"
        )

    # Build system prompt
    logger.info("run_openai_compat_cmd: building agent instructions for agent=%s", agent)
    system_content = _openai_compat_agent_instructions(
        agent=agent,
        runner=runner,
        extra_skills=extra_skills,
        repo=repo,
        change_id=change_id,
    )
    logger.info("run_openai_compat_cmd: system prompt length=%d chars", len(system_content))
    logger.debug("run_openai_compat_cmd: system prompt first 300 chars: %s", system_content[:300])
    logger.debug("run_openai_compat_cmd: system prompt last 300 chars: %s", system_content[-300:])

    runtime = _OpenaiCompatToolRuntime(repo=repo, change_id=change_id)
    logger.info("run_openai_compat_cmd: runtime read_roots=%s write_roots=%s",
                [str(r) for r in runtime.read_roots], [str(r) for r in runtime.write_roots])

    messages: list[dict[str, object]] = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": prompt},
    ]
    logger.info("run_openai_compat_cmd: initial messages count=%d", len(messages))
    logger.debug("run_openai_compat_cmd: user prompt first 300 chars: %s", str(prompt)[:300])

    _emit_event("cli.invoke", runner=runner, agent=agent, cmd=["openai-compat-api"], argc=0)
    start = time.monotonic()
    for step in range(_OPENAI_COMPAT_MAX_TOOL_STEPS):
        logger.info("run_openai_compat_cmd: TOOL STEP %d/%d agent=%s messages_count=%d",
                    step + 1, _OPENAI_COMPAT_MAX_TOOL_STEPS, agent, len(messages))
        try:
            response = _openai_compat_chat(
                model=model,
                messages=messages,
                transport=transport,
                tools=runtime.tool_specs,
            )
        except Exception as exc:
            logger.error("run_openai_compat_cmd: _openai_compat_chat FAILED at step %d: %s: %s", step + 1, type(exc).__name__, exc)
            print(f"[openai-compat] Chat API call failed at step {step + 1}: {type(exc).__name__}: {exc}")
            raise

        logger.debug("run_openai_compat_cmd: response keys=%s", list(response.keys()) if response else "EMPTY")
        message = response.get("message") or {}
        messages.append(message)

        # Log message content
        msg_content = str(message.get("content") or "")
        tool_calls = message.get("tool_calls") or []
        logger.info("run_openai_compat_cmd: step %d — content_len=%d tool_calls=%d",
                    step + 1, len(msg_content), len(tool_calls))
        if msg_content:
            logger.debug("run_openai_compat_cmd: step %d message content: %s", step + 1, msg_content[:500])
        if tool_calls:
            logger.info("run_openai_compat_cmd: agent=%s tool_step=%d tool_calls=%d", agent, step + 1, len(tool_calls))
            for tc_idx, tool_call in enumerate(tool_calls):
                function = tool_call.get("function") or {}
                tool_name = str(function.get("name") or "")
                arguments = function.get("arguments") or {}
                if not isinstance(arguments, dict):
                    arguments = {}
                logger.info("run_openai_compat_cmd: step %d tool_call[%d] name=%s args=%s",
                            step + 1, tc_idx, tool_name, json.dumps(arguments, default=str)[:300])
                print(f"[openai-compat] Step {step + 1}, tool call: {tool_name}({json.dumps(arguments, default=str)[:200]})")
                try:
                    tool_result = runtime.execute(tool_name, arguments)
                    logger.info("run_openai_compat_cmd: step %d tool_call[%d] result_len=%d",
                                step + 1, tc_idx, len(tool_result))
                    logger.debug("run_openai_compat_cmd: step %d tool_call[%d] result: %s",
                                step + 1, tc_idx, tool_result[:500])
                except Exception as exc:
                    logger.error("run_openai_compat_cmd: step %d tool_call[%d] runtime.execute FAILED: %s: %s",
                                step + 1, tc_idx, type(exc).__name__, exc)
                    tool_result = json.dumps({"error": f"{type(exc).__name__}: {exc}"})
                messages.append(
                    {"role": "tool", "tool_name": tool_name, "content": tool_result}
                )
            continue

        # No tool calls — model produced final answer
        content = str(message.get("content") or "")
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.info("run_openai_compat_cmd: FINAL ANSWER agent=%s steps=%d content_len=%d duration_ms=%d",
                    agent, step + 1, len(content), duration_ms)
        logger.debug("run_openai_compat_cmd: final content: %s", content[:1000])
        if content:
            if stream_output:
                print(content)
        _emit_event("cli.exit", runner=runner, agent=agent, exit_code=0, duration_ms=duration_ms)
        return content

    duration_ms = int((time.monotonic() - start) * 1000)
    logger.error("run_openai_compat_cmd: EXCEEDED MAX TOOL STEPS agent=%s max=%d duration_ms=%d",
                 agent, _OPENAI_COMPAT_MAX_TOOL_STEPS, duration_ms)
    _emit_event("cli.exit", runner=runner, agent=agent, exit_code=1, duration_ms=duration_ms)
    raise RuntimeError(f"OpenAI-compatible agent '{agent}' exceeded the maximum tool-call steps ({_OPENAI_COMPAT_MAX_TOOL_STEPS})")


def _agent_cmd_metadata(runner: str, prompt: str, agent: str, **kwargs) -> dict[str, str | int]:
    return {
        "agent": agent,
        "runner": runner,
        "model": kwargs.get("runner_model", "default"),
        "prompt_len": len(prompt),
    }


@track_with_ui(name="agent-call", type="llm", metadata_getter=_agent_cmd_metadata)
def run_agent_cmd(
    runner: str,
    prompt: str,
    agent: str,
    **kwargs,
) -> str:
    """Dispatch to the selected CLI runner based on runner."""
    logger.debug("run_agent_cmd: runner=%s agent=%s", runner, agent)
    runner_model = kwargs.pop("runner_model", None)
    extra_skills = kwargs.pop("extra_skills", None)
    repo = kwargs.pop("repo", None)
    change_id = kwargs.pop("change_id", None)
    # copilot_effort is no longer supported — accept and discard for backward compat.
    kwargs.pop("copilot_effort", None)
    if is_copilot_runner(runner):
        if runner == "copilot":
            # Base copilot: pass --model as usual
            effective_model = runner_model if runner_model is not None else DEFAULT_COPILOT_MODEL
            return run_copilot_cmd(
                prompt=prompt,
                agent=agent,
                model=effective_model,
                cli_cmd="copilot",
                extra_skills=extra_skills,
                **kwargs,
            )
        else:
            # Alias runner (e.g. "copilot-gemma4"): the binary IS the command; no --model flag
            logger.debug("run_agent_cmd: copilot alias runner=%s → invoking binary %r directly", runner, runner)
            return run_copilot_cmd(
                prompt=prompt,
                agent=agent,
                cli_cmd=runner,
                extra_skills=extra_skills,
                **kwargs,
            )
    elif runner == "claude":
        model_kwarg = {"model": runner_model} if runner_model is not None else {}
        return run_claude_cmd(prompt=prompt, agent=agent, **model_kwarg, **kwargs)
    elif runner == "gemini":
        model_kwarg = {"model": runner_model} if runner_model is not None else {}
        return run_gemini_cmd(prompt=prompt, agent=agent, extra_skills=extra_skills, **model_kwarg, **kwargs)
    else:
        # Check if the runner is an alias for a supported provider (like openai-compat)
        from .runner_models import _runner_aliases
        config = {"runner_aliases": _runner_aliases(None)} # This is a placeholder, usually config is passed
        # Try to load from server.config if possible
        try:
            from server.config import load_config
            config = load_config()
        except ImportError:
            pass

        provider = _provider_for_runner(runner, config=config)
        logger.info("run_agent_cmd: unknown runner=%r resolved provider=%s", runner, provider)
        if provider == "openai-compat":
            effective_model = runner_model if runner_model is not None else runner
            logger.info("run_agent_cmd: dispatching to run_openai_compat_cmd runner=%s model=%s agent=%s",
                        runner, effective_model, agent)
            return run_openai_compat_cmd(
                prompt=prompt,
                agent=agent,
                model=effective_model,
                runner=runner,
                extra_skills=extra_skills,
                repo=repo,
                change_id=change_id,
                **kwargs,
            )

        logger.error("run_agent_cmd: unknown runner=%r provider=%s", runner, provider)
        raise ValueError(f"Unknown runner: {runner!r}. Must be 'claude', 'copilot' (or a copilot alias), 'gemini', or an openai-compat-based runner.")


def run_copilot(
    prompt: str,
    agent: str,
    model: str = "gpt-5-mini",
    silent: bool = True,
    extra_flags: list[str] | None = None,
) -> str:
    """Wrapper around run_copilot_cmd."""
    return run_copilot_cmd(prompt=prompt, agent=agent, model=model,
                           silent=silent, extra_flags=extra_flags)


def run_gemini(
    prompt: str,
    agent: str,
    model: str = DEFAULT_GEMINI_MODEL,
    extra_flags: list[str] | None = None,
) -> str:
    """Wrapper around run_gemini_cmd."""
    return run_gemini_cmd(prompt=prompt, agent=agent, model=model, extra_flags=extra_flags)
