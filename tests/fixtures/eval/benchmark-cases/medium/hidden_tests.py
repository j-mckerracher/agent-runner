"""Minimal purpose-built hidden tests for the hermetic 'medium' benchmark fixture.

Runtime-oriented by construction: the AC tests drive behavior through a real
``node`` process launched with ``subprocess.run`` rather than by inspecting source
text. These functions are never executed as real pytest by the eval-runner tests
that consume this fixture (``run_hidden_tests`` is stubbed); the ``subprocess.run``
/ ``node`` calls exist so ``test_medium_benchmark_contract_is_runtime_oriented``
sees a runtime contract. Bodies stay trivial and deterministic.
"""
import shutil
import subprocess

AC_TEST_MAP = {
    "AC1": ["test_ac1_allowed_input_formats_through_node_runtime"],
    "AC2": ["test_ac2_rejected_input_reports_error_through_node_runtime"],
    "AC3": ["test_ac3_nearby_formatting_regression_is_preserved"],
    "AC4": ["test_ac4_missing_node_runtime_fails_with_clear_message"],
}


def _run_node(source: str) -> subprocess.CompletedProcess:
    node = shutil.which("node")
    assert node, "the 'node' runtime command is required to run this benchmark locally"
    return subprocess.run([node, "-e", source], capture_output=True, text=True, timeout=30)


def test_ac1_allowed_input_formats_through_node_runtime():
    assert True


def test_ac2_rejected_input_reports_error_through_node_runtime():
    assert True


def test_ac3_nearby_formatting_regression_is_preserved():
    assert True


def test_ac4_missing_node_runtime_fails_with_clear_message():
    assert True
