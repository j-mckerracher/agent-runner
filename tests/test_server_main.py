from __future__ import annotations

import unittest
from unittest.mock import patch

from server import main as server_main


class ServerMainTests(unittest.TestCase):
    def test_easy__parse_args_normalizes_log_level(self) -> None:
        args = server_main.parse_args(["--log-level", "ERROR"], api_cfg={})

        self.assertEqual(args.log_level, "error")

    def test_easy__main_applies_log_level_to_logging_and_uvicorn(self) -> None:
        with patch.object(server_main, "load_config", return_value={"api": {}}), \
             patch.object(server_main, "configure_logging") as configure_logging_mock, \
             patch.object(server_main.uvicorn, "run") as uvicorn_run_mock:
            server_main.main(["--host", "0.0.0.0", "--port", "9000", "--reload", "--log-level", "warning"])

        configure_logging_mock.assert_called_once_with("warning")
        uvicorn_run_mock.assert_called_once_with(
            "server.app:app",
            host="0.0.0.0",
            port=9000,
            reload=True,
            log_level="warning",
        )

    def test_easy__build_logging_config_uses_requested_level(self) -> None:
        config = server_main.build_logging_config("error")

        self.assertEqual(config["root"]["level"], "ERROR")
        self.assertEqual(config["loggers"]["uvicorn"]["level"], "ERROR")
        self.assertEqual(config["loggers"]["uvicorn.access"]["level"], "ERROR")


if __name__ == "__main__":
    unittest.main()
