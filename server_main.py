#!/usr/bin/env python3
"""Entrypoint: python server_main.py

Starts the agent-runner FastAPI server on localhost:8742 (configurable).
"""
from __future__ import annotations

import argparse
import sys

import uvicorn

from server.app import create_app
from server.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="agent-runner local API + GUI server")
    parser.add_argument("--host", default=None, help="Bind host (overrides config)")
    parser.add_argument("--port", type=int, default=None, help="Bind port (overrides config)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload (dev mode)")
    args = parser.parse_args()

    cfg = load_config()
    host = args.host or cfg["api"].get("host", "127.0.0.1")
    port = args.port or cfg["api"].get("port", 8742)

    app = create_app()

    print(f"Starting agent-runner server at http://{host}:{port}/")
    print(f"GUI available at:  http://{host}:{port}/")
    print(f"API health:        http://{host}:{port}/health")
    print("Press Ctrl+C to stop.")

    uvicorn.run(
        app,
        host=host,
        port=port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
