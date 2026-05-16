from __future__ import annotations

import argparse
import unittest

from core.cli_logging import normalize_log_level, to_logging_level


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


if __name__ == "__main__":
    unittest.main()


