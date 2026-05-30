from __future__ import annotations

import argparse
import logging
import unittest

from core.cli_logging import (
    DemoteHttpxHealthcheckFilter,
    install_httpx_healthcheck_filter,
    normalize_log_level,
    to_logging_level,
)


class CliLoggingTests(unittest.TestCase):
    def test_easy__normalizes_case(self) -> None:
        self.assertEqual(normalize_log_level("DEBUG"), "debug")

    def test_easy__normalizes_aliases_and_whitespace(self) -> None:
        self.assertEqual(normalize_log_level(" warn "), "warning")
        self.assertEqual(normalize_log_level("fatal"), "critical")

    def test_medium__rejects_invalid_level(self) -> None:
        with self.assertRaises(argparse.ArgumentTypeError):
            normalize_log_level("verbose")

    def test_easy__maps_to_stdlib_logging_level(self) -> None:
        self.assertEqual(to_logging_level("error"), 40)

    def test_medium__demotes_httpx_local_healthcheck_logs_to_debug(self) -> None:
        record = logging.LogRecord(
            "httpx",
            logging.INFO,
            __file__,
            1,
            'HTTP Request: GET http://localhost:5173/is-alive/ping "HTTP/1.1 200 OK"',
            (),
            None,
        )

        self.assertTrue(DemoteHttpxHealthcheckFilter().filter(record))

        self.assertEqual(record.levelno, logging.DEBUG)
        self.assertEqual(record.levelname, "DEBUG")

    def test_medium__leaves_other_httpx_logs_at_original_level(self) -> None:
        record = logging.LogRecord(
            "httpx",
            logging.INFO,
            __file__,
            1,
            'HTTP Request: GET http://localhost:5173/api/runs "HTTP/1.1 200 OK"',
            (),
            None,
        )

        self.assertTrue(DemoteHttpxHealthcheckFilter().filter(record))

        self.assertEqual(record.levelno, logging.INFO)
        self.assertEqual(record.levelname, "INFO")

    def test_medium__installs_httpx_healthcheck_filter_once(self) -> None:
        httpx_logger = logging.getLogger("httpx")
        original_filters = list(httpx_logger.filters)
        httpx_logger.filters = [
            log_filter
            for log_filter in httpx_logger.filters
            if not isinstance(log_filter, DemoteHttpxHealthcheckFilter)
        ]
        try:
            install_httpx_healthcheck_filter()
            install_httpx_healthcheck_filter()

            matching_filters = [
                log_filter
                for log_filter in httpx_logger.filters
                if isinstance(log_filter, DemoteHttpxHealthcheckFilter)
            ]
            self.assertEqual(len(matching_filters), 1)
        finally:
            httpx_logger.filters = original_filters


if __name__ == "__main__":
    unittest.main()
