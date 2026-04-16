"""Gateway — local LLM/HTTP proxy with live, record, and replay modes.

Intercepts LLM traffic for deterministic test replay. Cassettes store
request/response pairs keyed by a canonical hash of the request.

Cassette format:
    <cassette_dir>/
        index.json         — list of entry keys with metadata
        entries/<key>.json — {request: {...}, response: {...}}

Cassette key = sha256 of (provider, method, path, canonicalized_body).
Canonicalization: JSON-parse body, sort keys, drop request_id/nonce/
timestamp/trace_id/user_agent.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web

from agent_runner_shared.events import emit_event_line
from agent_runner_harness.gateway.providers import PROVIDERS

_STRIP_FIELDS = {"request_id", "nonce", "timestamp", "trace_id", "user_agent"}

# Build lookup tables from PROVIDERS
_PROVIDER_PREFIXES: dict[str, str] = {
    spec.path_prefix: spec.upstream_url for spec in PROVIDERS.values()
}


def _canonicalize_body(body: bytes) -> bytes:
    """Return a canonicalized form of a JSON body for cache key computation."""
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return body

    def _strip(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: _strip(v) for k, v in sorted(obj.items()) if k not in _STRIP_FIELDS}
        if isinstance(obj, list):
            return [_strip(item) for item in obj]
        return obj

    return json.dumps(_strip(parsed), sort_keys=True).encode("utf-8")


def _cassette_key(provider: str, method: str, path: str, body: bytes) -> str:
    """Compute a cassette key from request components."""
    canonical_body = _canonicalize_body(body)
    raw = f"{provider}:{method}:{path}:".encode("utf-8") + canonical_body
    return hashlib.sha256(raw).hexdigest()


class Cassette:
    """Manages cassette read/write operations."""

    def __init__(self, cassette_dir: Path) -> None:
        self._dir = Path(cassette_dir)
        self._entries_dir = self._dir / "entries"
        self._index_path = self._dir / "index.json"
        self._index: list[dict[str, str]] = []
        self._lock = threading.Lock()
        self._load_index()

    def _load_index(self) -> None:
        if self._index_path.exists():
            self._index = json.loads(self._index_path.read_text(encoding="utf-8"))

    def _save_index(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index_path.write_text(json.dumps(self._index, indent=2), encoding="utf-8")

    def get(self, key: str) -> dict[str, Any] | None:
        """Return the cassette entry for a key, or None if not found."""
        entry_path = self._entries_dir / f"{key}.json"
        if not entry_path.exists():
            return None
        return json.loads(entry_path.read_text(encoding="utf-8"))

    def put(
        self,
        key: str,
        request: dict[str, Any],
        response: dict[str, Any],
        provider: str | None = None,
    ) -> None:
        """Store a request/response pair in the cassette.

        Args:
            key: Cassette key (sha256 hex string).
            request: Serializable request dict.
            response: Serializable response dict.
            provider: Provider name to stamp on the index entry for replay
                disambiguation.
        """
        with self._lock:
            self._entries_dir.mkdir(parents=True, exist_ok=True)
            entry_path = self._entries_dir / f"{key}.json"
            entry_path.write_text(
                json.dumps({"request": request, "response": response}, indent=2),
                encoding="utf-8",
            )
            # Update index
            self._index = [e for e in self._index if e.get("key") != key]
            index_entry: dict[str, str] = {"key": key}
            if provider:
                index_entry["provider"] = provider
            self._index.append(index_entry)
            self._save_index()


class GatewayApp:
    """aiohttp-based gateway application."""

    def __init__(
        self,
        cassette_path: Path | None,
        mode: str,
    ) -> None:
        self._cassette = Cassette(cassette_path) if cassette_path else None
        self._mode = mode  # live | record | replay

    def _detect_provider(self, path: str) -> tuple[str | None, str | None, str | None]:
        """Detect provider name, prefix, and upstream base URL from request path."""
        for spec in PROVIDERS.values():
            if path.startswith(spec.path_prefix):
                return spec.name, spec.path_prefix, spec.upstream_url
        return None, None, None

    async def _handle(self, request: web.Request) -> web.Response:
        """Handle all proxied requests."""
        path = request.path
        method = request.method
        body = await request.read()

        provider_name, prefix, upstream = self._detect_provider(path)
        if prefix is None or upstream is None or provider_name is None:
            return web.Response(status=404, text="Unknown provider prefix")

        sub_path = path[len(prefix) - 1:]  # keep leading slash

        cassette_key = _cassette_key(provider_name, method, sub_path, body)

        # Replay mode: must find in cassette
        if self._mode == "replay":
            if self._cassette is None:
                print(emit_event_line("cassette.miss", key=cassette_key, path=path))
                return web.Response(
                    status=503,
                    text=f"cassette.miss: {cassette_key}",
                    content_type="application/json",
                )
            entry = self._cassette.get(cassette_key)
            if entry is None:
                print(emit_event_line("cassette.miss", key=cassette_key, path=path))
                return web.Response(
                    status=503,
                    text=json.dumps({"error": "cassette.miss", "key": cassette_key}),
                    content_type="application/json",
                )
            resp_data = entry["response"]
            print(emit_event_line("cassette.replay", key=cassette_key, path=path))
            return web.Response(
                status=resp_data.get("status", 200),
                body=resp_data.get("body", "").encode("utf-8"),
                content_type=resp_data.get("content_type", "application/json"),
                headers=resp_data.get("headers", {}),
            )

        # Live or record: forward to upstream
        upstream_url = upstream + sub_path
        if request.query_string:
            upstream_url += f"?{request.query_string}"

        headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in ("host", "content-length")
        }

        async with aiohttp.ClientSession() as session:
            async with session.request(
                method,
                upstream_url,
                headers=headers,
                data=body,
            ) as resp:
                resp_body = await resp.read()
                resp_headers = dict(resp.headers)
                status = resp.status
                content_type = resp.content_type

        if self._mode == "record" and self._cassette is not None:
            req_record = {
                "method": method,
                "path": sub_path,
                "provider": provider_name,
                "body": body.decode("utf-8", errors="replace"),
                "headers": {k: v for k, v in headers.items() if k.lower() != "authorization"},
            }
            resp_record = {
                "status": status,
                "body": resp_body.decode("utf-8", errors="replace"),
                "content_type": content_type,
                "headers": {k: v for k, v in resp_headers.items()
                            if k.lower() in ("content-type",)},
            }
            self._cassette.put(cassette_key, req_record, resp_record, provider=provider_name)
            print(emit_event_line("cassette.record", key=cassette_key, path=path))

        return web.Response(
            status=status,
            body=resp_body,
            content_type=content_type,
        )

    def build_app(self) -> web.Application:
        """Build and return the aiohttp Application."""
        app = web.Application()
        app.router.add_route("*", "/{path_info:.*}", self._handle)
        return app


@dataclass
class GatewayHandle:
    """Handle for a running gateway server."""

    url: str
    _runner: Any = field(repr=False)
    _site: Any = field(repr=False)
    _loop: Any = field(repr=False)
    _thread: Any = field(repr=False)
    env_overrides: dict[str, str] = field(default_factory=dict)

    def stop(self) -> None:
        """Stop the gateway server."""
        # Cancel all tasks in the loop to wake up the waiting coroutine
        if self._loop and not self._loop.is_closed():
            for task in asyncio.all_tasks(self._loop):
                self._loop.call_soon_threadsafe(task.cancel)
        if self._thread:
            self._thread.join(timeout=5)


def start_gateway(
    cassette_path: Path | None,
    mode: str,
    host: str = "127.0.0.1",
    port: int = 0,
) -> GatewayHandle:
    """Start the gateway server in a background thread.

    Args:
        cassette_path: Path to cassette directory (for record/replay modes).
                       Pass None for live mode.
        mode: One of 'live', 'record', 'replay'.
        host: Host to bind to (default: 127.0.0.1).
        port: Port to bind to. 0 means OS assigns a free port.

    Returns:
        GatewayHandle with .url, .stop(), and .env_overrides dict containing
        all four provider environment variables (ANTHROPIC_BASE_URL,
        OPENAI_BASE_URL, AZURE_DEVOPS_ORG_URL, DISCORD_WEBHOOK_BASE).
    """
    gateway_app = GatewayApp(cassette_path, mode)
    app = gateway_app.build_app()

    loop = asyncio.new_event_loop()
    started_event = threading.Event()
    actual_port_holder: list[int] = []

    # Alternative: use stored runner/site for clean shutdown
    runner_holder: list[Any] = []
    site_holder: list[Any] = []

    async def _run_v2() -> None:
        runner = web.AppRunner(app)
        await runner.setup()
        runner_holder.append(runner)
        site = web.TCPSite(runner, host, port)
        await site.start()
        site_holder.append(site)
        # Extract actual port from the site's server
        actual = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        actual_port_holder.append(actual)
        started_event.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass

    def _thread_main() -> None:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run_v2())

    thread = threading.Thread(target=_thread_main, daemon=True)
    thread.start()
    started_event.wait(timeout=10)

    actual_port = actual_port_holder[0] if actual_port_holder else port
    base_url = f"http://{host}:{actual_port}"

    # Build env_overrides from all providers
    env_overrides: dict[str, str] = {}
    for spec in PROVIDERS.values():
        env_overrides.update(spec.resolve_env_vars(base_url))

    handle = GatewayHandle(
        url=base_url,
        _runner=runner_holder[0] if runner_holder else None,
        _site=site_holder[0] if site_holder else None,
        _loop=loop,
        _thread=thread,
        env_overrides=env_overrides,
    )
    return handle
