from __future__ import annotations

import argparse
from datetime import datetime, timezone
import logging

VALID_LOG_LEVELS: tuple[str, ...] = ("debug", "info", "warning", "error", "critical")
LOG_LEVEL_ALIASES: dict[str, str] = {
    "warn": "warning",
    "fatal": "critical",
}
DEFAULT_LOG_FORMAT = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
HTTPX_HEALTHCHECK_MESSAGE_FRAGMENT = "GET http://localhost:5173/is-alive/ping"


def normalize_log_level(value: str) -> str:
    normalized = str(value).strip().lower()
    normalized = LOG_LEVEL_ALIASES.get(normalized, normalized)
    if normalized not in VALID_LOG_LEVELS:
        expected = ", ".join(VALID_LOG_LEVELS)
        raise argparse.ArgumentTypeError(
            f"invalid log level {value!r}; expected one of: {expected}"
        )
    return normalized



class LocalTimezoneFormatter(logging.Formatter):
    """Render log timestamps with an explicit local UTC offset."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        local_dt = datetime.fromtimestamp(record.created, tz=timezone.utc).astimezone()
        if datefmt:
            return local_dt.strftime(datefmt)
        return local_dt.isoformat(timespec="seconds")


def to_logging_level(level: str) -> int:
    return getattr(logging, normalize_log_level(level).upper())


class DemoteHttpxHealthcheckFilter(logging.Filter):
    """Demote noisy local GUI health-check request logs to DEBUG."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "httpx" and HTTPX_HEALTHCHECK_MESSAGE_FRAGMENT in record.getMessage():
            record.levelno = logging.DEBUG
            record.levelname = logging.getLevelName(logging.DEBUG)
        return True


def install_httpx_healthcheck_filter() -> None:
    httpx_logger = logging.getLogger("httpx")
    if not any(isinstance(log_filter, DemoteHttpxHealthcheckFilter) for log_filter in httpx_logger.filters):
        httpx_logger.addFilter(DemoteHttpxHealthcheckFilter())
