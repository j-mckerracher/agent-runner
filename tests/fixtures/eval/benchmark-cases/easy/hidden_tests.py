"""Minimal purpose-built hidden tests for the hermetic 'easy' benchmark fixture.

These functions are never executed as real pytest by the eval-runner tests that
consume this fixture (``run_hidden_tests`` is stubbed); they exist only so the
static benchmark validators (``validate_benchmark`` / ``validate_ac_test_map``)
and ``benchmark_ac_test_map`` resolve against a real AC_TEST_MAP whose values name
functions that actually exist. Bodies are intentionally trivial.
"""

AC_TEST_MAP = {
    "AC1": ["test_ac1_renders_final_recap_fields"],
    "AC2": ["test_ac2_omits_verbose_and_sensitive_content_when_final_recap_exists"],
    "AC3": ["test_ac3_empty_final_recap_lists_render_clear_none_placeholder"],
    "AC4": ["test_ac4_preserves_legacy_session_summary_decisions_and_issues"],
}


def test_ac1_renders_final_recap_fields():
    assert True


def test_ac2_omits_verbose_and_sensitive_content_when_final_recap_exists():
    assert True


def test_ac3_empty_final_recap_lists_render_clear_none_placeholder():
    assert True


def test_ac4_preserves_legacy_session_summary_decisions_and_issues():
    assert True
