"""Tests for the gateway in record and replay modes."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
import pytest_asyncio

from agent_runner_harness.gateway import (
    Cassette,
    _cassette_key,
    _canonicalize_body,
    start_gateway,
)


class TestCassette:
    def test_put_and_get(self, tmp_path: Path) -> None:
        """Storing an entry and retrieving it returns the same data."""
        cassette = Cassette(tmp_path / "cassette")
        key = "abc123"
        req = {"method": "POST", "path": "/v1/messages"}
        resp = {"status": 200, "body": '{"id": "msg_1"}'}
        cassette.put(key, req, resp)

        result = cassette.get(key)
        assert result is not None
        assert result["request"] == req
        assert result["response"] == resp

    def test_get_missing_returns_none(self, tmp_path: Path) -> None:
        """get() returns None for a key that was never stored."""
        cassette = Cassette(tmp_path / "cassette")
        assert cassette.get("no-such-key") is None

    def test_index_updated_on_put(self, tmp_path: Path) -> None:
        """Index file is updated when entries are stored."""
        cass_dir = tmp_path / "cassette"
        cassette = Cassette(cass_dir)
        cassette.put("k1", {}, {})
        cassette.put("k2", {}, {})

        index = json.loads((cass_dir / "index.json").read_text())
        keys = {e["key"] for e in index}
        assert "k1" in keys
        assert "k2" in keys

    def test_put_overwrites_same_key(self, tmp_path: Path) -> None:
        """Storing the same key twice updates the entry."""
        cassette = Cassette(tmp_path / "cassette")
        cassette.put("k", {}, {"status": 200})
        cassette.put("k", {}, {"status": 500})
        result = cassette.get("k")
        assert result["response"]["status"] == 500


class TestCassetteKey:
    def test_same_inputs_produce_same_key(self) -> None:
        """Same inputs always produce the same key."""
        body = b'{"model": "claude-3", "max_tokens": 100}'
        k1 = _cassette_key("anthropic", "POST", "/v1/messages", body)
        k2 = _cassette_key("anthropic", "POST", "/v1/messages", body)
        assert k1 == k2

    def test_different_method_produces_different_key(self) -> None:
        """Different HTTP methods produce different keys."""
        body = b"{}"
        k1 = _cassette_key("openai", "POST", "/v1/chat", body)
        k2 = _cassette_key("openai", "GET", "/v1/chat", body)
        assert k1 != k2

    def test_nonce_field_stripped(self) -> None:
        """nonce field is stripped before computing key."""
        body1 = b'{"model": "gpt-5", "nonce": "abc"}'
        body2 = b'{"model": "gpt-5", "nonce": "xyz"}'
        k1 = _cassette_key("openai", "POST", "/v1/chat", body1)
        k2 = _cassette_key("openai", "POST", "/v1/chat", body2)
        assert k1 == k2

    def test_request_id_stripped(self) -> None:
        """request_id field is stripped."""
        body1 = b'{"prompt": "hello", "request_id": "r1"}'
        body2 = b'{"prompt": "hello", "request_id": "r2"}'
        k1 = _cassette_key("openai", "POST", "/v1", body1)
        k2 = _cassette_key("openai", "POST", "/v1", body2)
        assert k1 == k2

    def test_key_is_hex_string(self) -> None:
        """Key is a hex string (sha256)."""
        key = _cassette_key("p", "POST", "/path", b"body")
        assert all(c in "0123456789abcdef" for c in key)
        assert len(key) == 64


class TestCanonicalize:
    def test_sorts_keys(self) -> None:
        """Canonicalization sorts JSON keys."""
        body = b'{"z": 1, "a": 2}'
        result = _canonicalize_body(body)
        parsed = json.loads(result)
        assert list(parsed.keys()) == ["a", "z"]

    def test_strips_nonce(self) -> None:
        body = b'{"model": "m", "nonce": "n123"}'
        result = json.loads(_canonicalize_body(body))
        assert "nonce" not in result

    def test_non_json_body_returned_as_is(self) -> None:
        """Non-JSON bodies are returned unchanged."""
        body = b"plain text body"
        result = _canonicalize_body(body)
        assert result == body


class TestGatewayReplay:
    def test_replay_miss_returns_503(self, tmp_path: Path) -> None:
        """In replay mode, a cache miss returns HTTP 503."""
        import httpx

        handle = start_gateway(tmp_path / "empty-cassette", mode="replay")
        try:
            resp = httpx.post(
                f"{handle.url}/openai/v1/chat/completions",
                json={"model": "gpt-5"},
                timeout=5.0,
            )
            assert resp.status_code == 503
        finally:
            handle.stop()

    def test_replay_from_cassette(self, tmp_path: Path) -> None:
        """In replay mode, a cassette hit returns the stored response."""
        import httpx

        cass_dir = tmp_path / "cassette"
        cassette = Cassette(cass_dir)

        # Pre-populate cassette
        body = b'{"model": "gpt-5", "messages": []}'
        key = _cassette_key("openai", "POST", "/v1/chat/completions", body)
        stored_resp = {"status": 200, "body": '{"id": "chatcmpl-1"}', "content_type": "application/json"}
        cassette.put(key, {"method": "POST", "path": "/v1/chat/completions"}, stored_resp)

        handle = start_gateway(cass_dir, mode="replay")
        try:
            resp = httpx.post(
                f"{handle.url}/openai/v1/chat/completions",
                content=body,
                headers={"Content-Type": "application/json"},
                timeout=5.0,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data.get("id") == "chatcmpl-1"
        finally:
            handle.stop()

    def test_env_overrides_populated(self, tmp_path: Path) -> None:
        """GatewayHandle.env_overrides contains expected keys."""
        handle = start_gateway(tmp_path / "c", mode="replay")
        try:
            assert "OPENAI_BASE_URL" in handle.env_overrides
            assert "ANTHROPIC_BASE_URL" in handle.env_overrides
            assert handle.url in handle.env_overrides["OPENAI_BASE_URL"]
        finally:
            handle.stop()
