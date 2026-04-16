"""Tests for the workflow-as-data loader."""
from __future__ import annotations

import pytest

from agent_runner.workflow.definition import load_workflow


def test_load_standard_workflow() -> None:
    wf = load_workflow("standard")
    assert wf.id == "standard"
    assert wf.version == 1
    assert len(wf.stages) == 6
    assert wf.stages[0].id == "intake"
    assert wf.stages[0].kind == "single"
    assert wf.stages[0].agent == "intake@v1"


def test_agent_refs_unique_and_ordered() -> None:
    wf = load_workflow("standard")
    refs = wf.agent_refs()
    assert len(refs) == len(set(refs))
    assert refs[0] == "intake@v1"


def test_unknown_workflow_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_workflow("does-not-exist")
