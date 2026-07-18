"""Hidden tests for the `legacy-case` v0.2 CLI/E2E fixture.

Self-contained and deterministic -- no network, no external target-repo
dependency. In `tests/test_v02_end_to_end.py` the runner test seams
(`prepare_workspace`, `invoke_workflow`, `run_hidden_tests`) are patched, so
these test bodies never actually execute; their only job is to satisfy the
static validators in `eval/seed_benchmarks.py` (`validate_hidden_tests`,
`validate_ac_test_map`) at discovery time.
"""

AC_TEST_MAP = {
    "AC1": ["test_ac1_marker_file_exists"],
    "AC2": ["test_ac2_marker_file_contains_expected_text"],
}


def test_ac1_marker_file_exists():
    assert True


def test_ac2_marker_file_contains_expected_text():
    assert True
