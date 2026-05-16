"""Entrypoint: `python server_main.py` → uvicorn."""
from __future__ import annotations

import argparse
from copy import deepcopy
import logging
import logging.config
from typing import Any, cast

import uvicorn

from core.cli_logging import normalize_log_level
from server.config import load_config

# ---------------------------------------------------------------------------
# Logging bootstrap — configure before any other module emits records.
# ---------------------------------------------------------------------------
_LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
            "datefmt": "%Y-%m-%dT%H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "standard",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "DEBUG",
    },
    # Quiet down noisy third-party loggers.
    "loggers": {
        "uvicorn": {"level": "INFO"},
        "uvicorn.access": {"level": "INFO"},
        "httpx": {"level": "WARNING"},
        "httpcore": {"level": "WARNING"},
        "anthropic": {"level": "WARNING"},
    },
}

logger = logging.getLogger(__name__)


def build_logging_config(log_level: str) -> dict[str, Any]:
    config = cast(dict[str, Any], deepcopy(_LOGGING_CONFIG))
    root = cast(dict[str, Any], config["root"])
    loggers = cast(dict[str, dict[str, Any]], config["loggers"])
    root["level"] = log_level.upper()
    loggers["uvicorn"]["level"] = log_level.upper()
    loggers["uvicorn.access"]["level"] = log_level.upper()
    return config


def configure_logging(log_level: str) -> None:
    logging.config.dictConfig(build_logging_config(log_level))


def parse_args(argv: list[str] | None = None, *, api_cfg: dict | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the agent-runner local API server.")
    api_cfg = api_cfg or {}
    parser.add_argument("--host", default=api_cfg.get("host", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(api_cfg.get("port", 8742)))
    parser.add_argument("--reload", action="store_true", help="Hot-reload on code changes (dev).")
    parser.add_argument(
        "--log-level",
        type=normalize_log_level,
        default="debug",
        help="Logging verbosity: debug, info, warning, error, or critical.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    cfg = load_config()
    api_cfg = cfg.get("api", {})
    args = parse_args(argv, api_cfg=api_cfg)
    configure_logging(args.log_level)

    logger.info("agent-runner server_main.py starting up")
    logger.debug("Loaded config: api=%s, concurrency=%s", api_cfg, cfg.get("concurrency"))

    logger.info("Starting uvicorn on %s:%s (reload=%s)", args.host, args.port, args.reload)
    uvicorn.run(
        "server.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )
    logger.info("uvicorn has exited")


if __name__ == "__main__":
    main()
