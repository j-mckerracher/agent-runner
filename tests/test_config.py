from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server.config import load_config, save_config, validate_config


class ConfigRuntimeOverrideTests(unittest.TestCase):
    def test_codex_agent_default_accepts_arbitrary_model(self) -> None:
        errors = validate_config(
            {
                "api": {"host": "127.0.0.1", "port": 8742},
                "defaults": {"runner": "codex", "model": "future-codex-model", "mode": "live"},
                "concurrency": {"max_running_jobs": 1},
                "opik": {},
                "azure_devops": {},
                "repo_paths": {"base_dir": "", "custom_values": []},
                "runner_aliases": {},
                "agent_model_defaults": {
                    "qa-evaluator": {"codex": "future-codex-model"}
                },
            }
        )

        self.assertEqual(errors, [])

    def test_disabled_runner_aliases_are_runtime_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="awb-config-") as tmpdir:
            with patch.dict(os.environ, {"AGENT_RUNNER_DATA_DIR": tmpdir}, clear=False):
                save_config(
                    {
                        "defaults": {
                            "runner": "gemma4",
                            "model": "gemma4:31b-cloud",
                        },
                        "runner_aliases": {
                            "gemma4": {
                                "provider": "copilot",
                                "model": "gemma4:31b-cloud",
                            },
                            "ds4": {
                                "provider": "ollama",
                                "model": "deepseek-v4-pro:cloud",
                            },
                        },
                    }
                )

            with patch.dict(
                os.environ,
                {
                    "AGENT_RUNNER_DATA_DIR": tmpdir,
                    "AGENT_RUNNER_DISABLED_ALIASES": "gemma4,ds4",
                },
                clear=False,
            ):
                cfg = load_config()

            self.assertEqual(cfg["runner_aliases"], {})
            self.assertEqual(cfg["defaults"]["runner"], "copilot")
            self.assertIsNone(cfg["defaults"]["model"])

            on_disk = json.loads((Path(tmpdir) / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(sorted(on_disk["runner_aliases"]), ["ds4", "gemma4"])
            self.assertEqual(on_disk["defaults"]["runner"], "gemma4")


if __name__ == "__main__":
    unittest.main()
