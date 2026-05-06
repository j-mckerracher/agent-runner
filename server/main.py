"""Entrypoint: `python server_main.py` → uvicorn."""
from __future__ import annotations

import argparse
import logging
import logging.config

import uvicorn

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

logging.config.dictConfig(_LOGGING_CONFIG)

logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("agent-runner server_main.py starting up")
    cfg = load_config()
    api_cfg = cfg.get("api", {})
    logger.debug("Loaded config: api=%s, concurrency=%s", api_cfg, cfg.get("concurrency"))

    parser = argparse.ArgumentParser(description="Run the agent-runner local API server.")
    parser.add_argument("--host", default=api_cfg.get("host", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(api_cfg.get("port", 8742)))
    parser.add_argument("--reload", action="store_true", help="Hot-reload on code changes (dev).")
    args = parser.parse_args()

    logger.info("Starting uvicorn on %s:%s (reload=%s)", args.host, args.port, args.reload)
    uvicorn.run(
        "server.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
    logger.info("uvicorn has exited")


if __name__ == "__main__":
    main()
