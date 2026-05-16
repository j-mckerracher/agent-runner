from __future__ import annotations

import argparse
import logging

VALID_LOG_LEVELS: tuple[str, ...] = ("debug", "info", "warning", "error", "critical")
LOG_LEVEL_ALIASES: dict[str, str] = {
    "warn": "warning",
    "fatal": "critical",
}


def normalize_log_level(value: str) -> str:
    normalized = str(value).strip().lower()
    normalized = LOG_LEVEL_ALIASES.get(normalized, normalized)
    if normalized not in VALID_LOG_LEVELS:
        expected = ", ".join(VALID_LOG_LEVELS)
        raise argparse.ArgumentTypeError(
            f"invalid log level {value!r}; expected one of: {expected}"
        )
    return normalized



def to_logging_level(level: str) -> int:
    return getattr(logging, normalize_log_level(level).upper())

