"""Tests for gateway provider intercepts (pE-intercepts)."""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest
from aiohttp import web

from agent_runner_harness.gateway import Cassette, _cassette_key, start_gateway
from agent_runner_harness.gateway.providers import PROVIDERS, ProviderSpec


class TestProvidersMapping:
    def test_all_four_providers_present(self) -> None:
        assert "anthropic" in PROVIDERS
        assert "openai" in PROVIDERS
        assert "azure-devops" in PROVIDERS
        assert "discord" in PROVIDERS

    def test_provider_spec_types(self) -> None:
        for name, spec in PROVIDERS.items():
            assert isinstance(spec, ProviderSpec), f"{name} is not a ProviderSpec"

    def test_anthropic_upstream(self) -> None:
        assert PROVIDERS["anthropic"].upstream_url == "https://api.anthropic.com"

    def test_openai_upstream(self) -> None:
        assert PROVIDERS["openai"].upstream_url == "https://api.openai.com"

    def test_azure_devops_upstream(self) -> None:
        assert PROVIDERS["azure-devops"].upstream_url == "https://dev.azure.com"

    def test_discord_upstream(self) -> None:
        assert PROVIDERS["discord"].upstream_url == "https://discord.com/api"

    def test_path_prefixes(self) -> None:
        assert PROVIDERS["anthropic"].path_prefix == "/anthropic/"
        assert PROVIDERS["openai"].path_prefix == "/openai/"
        assert PROVIDERS["azure-devops"].path_prefix == "/azure-devops/"
        assert PROVIDERS["discord"].path_prefix == "/discord/"

    def test_resolve_env_vars_anthropic(self) -> None:
        resolved = PROVIDERS["anthropic"].resolve_env_vars("http://127.0.0.1:9000")
        assert "ANTHROPIC_BASE_URL" in resolved
        assert resolved["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:9000/anthropic"

    def test_resolve_env_vars_openai_v1_suffix(self) -> None:
        resolved = PROVIDERS["openai"].resolve_env_vars("http://127.0.0.1:9000")
        assert "OPENAI_BASE_URL" in resolved
        assert resolved["OPENAI_BASE_URL"] == "http://127.0.0.1:9000/openai/v1"

    def test_resolve_env_vars_azure_devops(self) -> None:
        resolved = PROVIDERS["azure-devops"].resolve_env_vars("http://127.0.0.1:9000")
        assert "AZURE_DEVOPS_ORG_URL" in resolved
        assert resolved["AZURE_DEVOPS_ORG_URL"] == "http://127.0.0.1:9000/azure-devops"

    def test_resolve_env_vars_discord(self) -> None:
        resolved = PROVIDERS["discord"].resolve_env_vars("http://127.0.0.1:9000")
        assert "DISCORD_WEBHOOK_BASE" in resolved
        assert resolved["DISCORD_WEBHOOK_BASE"] == "http://127.0.0.1:9000/discord"


class TestEnvOverrides:
    def test_all_four_env_vars_in_gateway_handle(self, tmp_path: Path) -> None:
        handle = start_gateway(tmp_path / "cassette", mode="replay")
        try:
            overrides = handle.env_overrides
            assert "ANTHROPIC_BASE_URL" in overrides
            assert "OPENAI_BASE_URL" in overrides
            assert "AZURE_DEVOPS_ORG_URL" in overrides
            assert "DISCORD_WEBHOOK_BASE" in overrides
        finally:
            handle.stop()

    def test_openai_base_url_has_v1_suffix(self, tmp_path: Path) -> None:
        handle = start_gateway(tmp_path / "cassette", mode="replay")
        try:
            url = handle.env_overrides["OPENAI_BASE_URL"]
            assert url.endswith("/openai/v1"), f"Expected /openai/v1 suffix, got: {url}"
        finally:
            handle.stop()

    def test_env_overrides_contain_gateway_url(self, tmp_path: Path) -> None:
        handle = start_gateway(tmp_path / "cassette", mode="replay")
        try:
            for key, value in handle.env_overrides.items():
                assert handle.url in value, f"{key}={value} does not contain gateway URL {handle.url}"
        finally:
            handle.stop()


def _start_mock_upstream(response_body: dict[str, Any], host: str = "127.0.0.1") -> tuple[str, threading.Event]:
    """Start a tiny aiohttp mock server on an ephemeral port.

    Returns:
        (base_url, stop_event) — set the stop_event to tear down the server.
    """
    stop_event = threading.Event()
    port_holder: list[int] = []
    ready_event = threading.Event()

    async def _handler(request: web.Request) -> web.Response:
        return web.Response(
            text=json.dumps(response_body),
            content_type="application/json",
        )

    async def _run() -> None:
        app = web.Application()
        app.router.add_route("*", "/{path_info:.*}", _handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, 0)
        await site.start()
        port_holder.append(site._server.sockets[0].getsockname()[1])  # type: ignore[union-attr]
        ready_event.set()
        while not stop_event.is_set():
            await asyncio.sleep(0.05)
        await runner.cleanup()

    def _thread_main() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run())

    t = threading.Thread(target=_thread_main, daemon=True)
    t.start()
    ready_event.wait(timeout=5)

    base_url = f"http://{host}:{port_holder[0]}"
    return base_url, stop_event


class TestReplayFromCassette:
    """Test that replay mode returns correct bodies for each provider prefix."""

    def _seed_cassette(
        self,
        cassette_dir: Path,
        provider: str,
        path: str,
        body: bytes,
        response: dict[str, Any],
    ) -> str:
        cassette = Cassette(cassette_dir)
        key = _cassette_key(provider, "POST", path, body)
        cassette.put(
            key,
            {"method": "POST", "path": path, "provider": provider},
            {"status": 200, "body": json.dumps(response), "content_type": "application/json"},
            provider=provider,
        )
        return key

    def test_anthropic_replay(self, tmp_path: Path) -> None:
        cass_dir = tmp_path / "cassette"
        body = b'{"model": "claude-3", "max_tokens": 10}'
        expected = {"id": "msg_ant_1", "provider": "anthropic"}
        self._seed_cassette(cass_dir, "anthropic", "/v1/messages", body, expected)

        handle = start_gateway(cass_dir, mode="replay")
        try:
            resp = httpx.post(
                f"{handle.url}/anthropic/v1/messages",
                content=body,
                headers={"Content-Type": "application/json"},
                timeout=5.0,
            )
            assert resp.status_code == 200
            assert resp.json()["id"] == "msg_ant_1"
        finally:
            handle.stop()

    def test_openai_replay(self, tmp_path: Path) -> None:
        cass_dir = tmp_path / "cassette"
        body = b'{"model": "gpt-5", "messages": []}'
        expected = {"id": "chatcmpl_oai_1"}
        self._seed_cassette(cass_dir, "openai", "/v1/chat/completions", body, expected)

        handle = start_gateway(cass_dir, mode="replay")
        try:
            resp = httpx.post(
                f"{handle.url}/openai/v1/chat/completions",
                content=body,
                headers={"Content-Type": "application/json"},
                timeout=5.0,
            )
            assert resp.status_code == 200
            assert resp.json()["id"] == "chatcmpl_oai_1"
        finally:
            handle.stop()

    def test_azure_devops_replay(self, tmp_path: Path) -> None:
        cass_dir = tmp_path / "cassette"
        body = b'{"query": "project list"}'
        expected = {"value": [{"id": "proj1"}]}
        self._seed_cassette(cass_dir, "azure-devops", "/myorg/_apis/projects", body, expected)

        handle = start_gateway(cass_dir, mode="replay")
        try:
            resp = httpx.post(
                f"{handle.url}/azure-devops/myorg/_apis/projects",
                content=body,
                headers={"Content-Type": "application/json"},
                timeout=5.0,
            )
            assert resp.status_code == 200
            assert resp.json()["value"][0]["id"] == "proj1"
        finally:
            handle.stop()

    def test_discord_replay(self, tmp_path: Path) -> None:
        cass_dir = tmp_path / "cassette"
        body = b'{"content": "hello"}'
        expected = {"id": "discord_msg_1"}
        self._seed_cassette(cass_dir, "discord", "/webhooks/123/token", body, expected)

        handle = start_gateway(cass_dir, mode="replay")
        try:
            resp = httpx.post(
                f"{handle.url}/discord/webhooks/123/token",
                content=body,
                headers={"Content-Type": "application/json"},
                timeout=5.0,
            )
            assert resp.status_code == 200
            assert resp.json()["id"] == "discord_msg_1"
        finally:
            handle.stop()


class TestRecordMode:
    """Test that record mode records cassette entries with provider stamped in index."""

    def test_record_stamps_provider_in_index(self, tmp_path: Path) -> None:
        """In record mode the cassette index entry contains the provider name."""
        mock_response = {"recorded": True}
        mock_url, stop_mock = _start_mock_upstream(mock_response)

        # Temporarily override the anthropic upstream to point at our mock
        from agent_runner_harness.gateway import providers as _p
        original_spec = _p.PROVIDERS["anthropic"]
        _p.PROVIDERS["anthropic"] = ProviderSpec(
            name="anthropic",
            path_prefix="/anthropic/",
            upstream_url=mock_url,
            env_vars=original_spec.env_vars,
        )
        # Also patch the gateway's _PROVIDER_PREFIXES lookup
        from agent_runner_harness import gateway as _gw
        _gw._PROVIDER_PREFIXES["/anthropic/"] = mock_url

        cass_dir = tmp_path / "cassette"
        handle = start_gateway(cass_dir, mode="record")
        try:
            body = b'{"model": "claude-3", "messages": []}'
            httpx.post(
                f"{handle.url}/anthropic/v1/messages",
                content=body,
                headers={"Content-Type": "application/json"},
                timeout=5.0,
            )
        finally:
            handle.stop()
            stop_mock.set()
            # Restore
            _p.PROVIDERS["anthropic"] = original_spec
            _gw._PROVIDER_PREFIXES["/anthropic/"] = original_spec.upstream_url

        # Check that index has the provider stamped
        index_path = cass_dir / "index.json"
        assert index_path.exists(), "index.json was not created"
        index = json.loads(index_path.read_text())
        assert len(index) == 1
        assert index[0].get("provider") == "anthropic"
