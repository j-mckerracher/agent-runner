"""Hidden tests for the `manifest-case` v0.2 CLI/E2E fixture.

Kept legacy-shaped (like `legacy-case`) only because `eval.runner`'s
discovery/validation path (`discover_benchmarks` -> `validate_benchmark`)
unconditionally requires `story.json` + `hidden_tests.py`, regardless of
`manifest.json` presence. `run_one`'s later `load_manifest` call is what
actually exercises the manifest.json in this directory (see
`eval/benchmark_manifest.py::find_manifest_file`).
"""

AC_TEST_MAP = {
    "AC1": ["test_ac1_marker_file_exists"],
    "AC2": ["test_ac2_marker_file_contains_expected_text"],
}


def test_ac1_marker_file_exists():
    assert True


def test_ac2_marker_file_contains_expected_text():
    assert True
