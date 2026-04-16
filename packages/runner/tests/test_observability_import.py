"""Import-safety test for the observability integration.

Verifies that importing and calling record_observability_event does NOT
raise even when the langfuse package is not installed.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch


def _ensure_langfuse_absent() -> None:
    """Remove langfuse from sys.modules if it happened to be installed."""
    for key in list(sys.modules):
        if key == "langfuse" or key.startswith("langfuse."):
            del sys.modules[key]


class TestObservabilityImportSafety:
    def test_import_succeeds_without_langfuse(self) -> None:
        _ensure_langfuse_absent()
        with patch.dict(sys.modules, {"langfuse": None}):
            # Re-import to exercise the guard path
            import importlib
            import agent_runner.integrations.observability as obs_mod
            importlib.reload(obs_mod)
            assert obs_mod.record_observability_event is not None

    def test_build_sink_returns_none_without_langfuse(self, monkeypatch) -> None:
        _ensure_langfuse_absent()
        monkeypatch.delenv("AGENT_RUNNER_OBSERVABILITY", raising=False)
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)

        from agent_runner.integrations.observability import build_observability_sink_from_env
        sink = build_observability_sink_from_env()
        assert sink is None

    def test_record_event_noop_without_sink(self) -> None:
        """record_observability_event must not raise when sink is None."""
        from unittest.mock import MagicMock
        from agent_runner.integrations.observability import record_observability_event
        from agent_runner.models import WorkflowConfig

        config = MagicMock(spec=WorkflowConfig)
        config.observability_sink = None

        # Must not raise
        record_observability_event(config, "workflow_start", change_id="WI-0")

    def test_record_event_noop_with_null_sink(self) -> None:
        """NullObservabilitySink.record_event must not raise."""
        from agent_runner.integrations.observability import (
            NullObservabilitySink,
            record_observability_event,
        )
        from unittest.mock import MagicMock
        from agent_runner.models import WorkflowConfig

        config = MagicMock(spec=WorkflowConfig)
        config.observability_sink = NullObservabilitySink()

        # Must not raise
        record_observability_event(config, "stage_start", stage="intake")
        record_observability_event(config, "workflow_complete", status="pass")
