"""Minimal purpose-built hidden tests for the hermetic 'hard' benchmark fixture.

A valid 4-AC contract with an AC_TEST_MAP and four real test functions. These
functions are never executed as real pytest by the eval-runner tests that consume
this fixture (``run_hidden_tests`` is stubbed); they exist only to satisfy the
static benchmark validators. Bodies are intentionally trivial.
"""

AC_TEST_MAP = {
    "AC1": ["test_ac1_classifies_new_error_category_at_boundary"],
    "AC2": ["test_ac2_existing_category_regression_preserved"],
    "AC3": ["test_ac3_second_nearby_category_regression_preserved"],
    "AC4": ["test_ac4_unknown_or_empty_input_yields_safe_default"],
}


def test_ac1_classifies_new_error_category_at_boundary():
    assert True


def test_ac2_existing_category_regression_preserved():
    assert True


def test_ac3_second_nearby_category_regression_preserved():
    assert True


def test_ac4_unknown_or_empty_input_yields_safe_default():
    assert True
