import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from eval import seed_benchmarks


VALID_STORY = """
{
  "change_id": "EVAL-EASY-001",
  "title": "Improve formatter behavior",
  "description": "As a maintainer, I want formatter behavior improved.",
  "acceptance_criteria": [
    "AC1: New formatter behavior is observable.",
    "AC2: Existing short values are unchanged.",
    "AC3: Empty input is handled explicitly."
  ],
  "metadata": {
    "difficulty": "easy",
    "target_sha": "abc123",
    "generated": true
  }
}
""".strip()


VALID_HIDDEN_TESTS = """
AC_TEST_MAP = {
    "AC1": ["test_ac1_new_formatter_behavior"],
    "AC2": ["test_ac2_preserves_short_values"],
    "AC3": ["test_ac3_handles_empty_input"],
}


def test_ac1_new_formatter_behavior():
    # User-visible behavior: long values are transformed.
    assert "abc".upper() == "ABC"


def test_ac2_preserves_short_values():
    # Regression behavior: nearby short values remain unchanged.
    assert "ok" == "ok"


def test_ac3_handles_empty_input():
    # Edge behavior: empty values remain explicit.
    assert "" == ""
""".strip()


def benchmark_response(story: str = VALID_STORY, hidden_tests: str = VALID_HIDDEN_TESTS) -> str:
    return f"""
<story.json>
{story}
</story.json>

<hidden_tests.py>
{hidden_tests}
</hidden_tests.py>
""".strip()


class EvalSeedBenchmarkPromptTests(unittest.TestCase):
    def test_build_prompt_requires_behavioral_ac_mapped_non_skipping_tests(self):
        prompt = seed_benchmarks.build_prompt(difficulty="easy", target_sha="abc123")

        self.assertIn("AC_TEST_MAP", prompt)
        self.assertIn("Hidden tests must not call pytest.skip", prompt)
        self.assertIn("accepts_semantically_equivalent_implementations", prompt)
        self.assertIn("Avoid the known weak benchmark patterns", prompt)
        self.assertIn("Include at least 3 acceptance criteria", prompt)

    def test_build_repair_prompt_includes_prior_benchmark_and_failure_reason(self):
        prompt = seed_benchmarks.build_repair_prompt(
            difficulty="medium",
            target_sha="abc123",
            story_json=VALID_STORY,
            hidden_tests_py=VALID_HIDDEN_TESTS,
            failure_reason="AC_TEST_MAP is missing acceptance criteria: AC3",
        )

        self.assertIn("AC_TEST_MAP is missing acceptance criteria: AC3", prompt)
        self.assertIn(VALID_STORY, prompt)
        self.assertIn(VALID_HIDDEN_TESTS, prompt)
        self.assertIn('"repaired": true', prompt)


class EvalSeedBenchmarkValidationTests(unittest.TestCase):
    def test_parse_benchmark_response_accepts_valid_ac_mapped_benchmark(self):
        story, hidden_tests = seed_benchmarks.parse_benchmark_response(
            benchmark_response(),
            difficulty="easy",
            target_sha="abc123",
        )

        self.assertEqual(story["metadata"]["difficulty"], "easy")
        self.assertIn("AC_TEST_MAP", hidden_tests)

    def test_parse_benchmark_response_rejects_missing_ac_test_map_entry(self):
        hidden_tests = VALID_HIDDEN_TESTS.replace('"AC3": ["test_ac3_handles_empty_input"],\n', "")

        with self.assertRaisesRegex(seed_benchmarks.BenchmarkGenerationError, "missing acceptance criteria: AC3"):
            seed_benchmarks.parse_benchmark_response(
                benchmark_response(hidden_tests=hidden_tests),
                difficulty="easy",
                target_sha="abc123",
            )

    def test_validate_hidden_tests_rejects_skip_and_xfail_behavior(self):
        cases = {
            "pytest_skip_call": VALID_HIDDEN_TESTS + "\n\nimport pytest\n\ndef test_ac1_skip_runtime():\n    pytest.skip('missing')\n",
            "pytest_skip_decorator": VALID_HIDDEN_TESTS
            + "\n\nimport pytest\n\n@pytest.mark.skip(reason='missing')\ndef test_ac1_skip_decorator():\n    assert True\n",
            "pytest_xfail_decorator": VALID_HIDDEN_TESTS
            + "\n\nimport pytest\n\n@pytest.mark.xfail(reason='bug')\ndef test_ac1_xfail_decorator():\n    assert True\n",
            "unittest_skiptest": VALID_HIDDEN_TESTS + "\n\nimport unittest\n\ndef test_ac1_skiptest():\n    raise unittest.SkipTest('missing')\n",
        }

        for name, hidden_tests in cases.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(seed_benchmarks.BenchmarkGenerationError, "skip|SkipTest|xfail"):
                    seed_benchmarks.validate_hidden_tests(hidden_tests)

    def test_validate_hidden_tests_rejects_blocked_network_imports(self):
        hidden_tests = VALID_HIDDEN_TESTS + "\n\nimport requests\n"

        with self.assertRaisesRegex(seed_benchmarks.BenchmarkGenerationError, "blocked network"):
            seed_benchmarks.validate_hidden_tests(hidden_tests)


class EvalSeedBenchmarkGenerationTests(unittest.TestCase):
    def test_parser_verifies_gold_master_failure_by_default(self):
        parser = seed_benchmarks.build_parser()

        args = parser.parse_args(["--repo", ".", "--sha", "abc123"])
        self.assertFalse(args.no_verify_gold_fails)

        args = parser.parse_args(["--repo", ".", "--sha", "abc123", "--no-verify-gold-fails"])
        self.assertTrue(args.no_verify_gold_fails)

    def test_generate_one_runs_gold_master_verification_by_default(self):
        with tempfile.TemporaryDirectory(prefix="seed-benchmark-test-") as tmpdir:
            args = SimpleNamespace(
                runner="claude",
                model=None,
                timeout=30,
                sha="abc123",
                attempts=1,
                no_verify_gold_fails=False,
                output_dir=Path(tmpdir) / "benchmarks",
                force=True,
                test_timeout=10,
            )

            with (
                patch.object(seed_benchmarks, "invoke_llm", return_value=benchmark_response()),
                patch.object(seed_benchmarks, "verify_gold_fails") as verify_gold_fails,
            ):
                seed_benchmarks.generate_one("easy", Path(tmpdir), args)

            verify_gold_fails.assert_called_once()

    def test_generate_one_uses_repair_prompt_after_validation_failure(self):
        invalid_hidden_tests = VALID_HIDDEN_TESTS.replace('"AC3": ["test_ac3_handles_empty_input"],\n', "")
        with tempfile.TemporaryDirectory(prefix="seed-benchmark-test-") as tmpdir:
            args = SimpleNamespace(
                runner="claude",
                model=None,
                timeout=30,
                sha="abc123",
                attempts=2,
                no_verify_gold_fails=True,
                output_dir=Path(tmpdir) / "benchmarks",
                force=True,
                test_timeout=10,
            )

            with patch.object(
                seed_benchmarks,
                "invoke_llm",
                side_effect=[benchmark_response(hidden_tests=invalid_hidden_tests), benchmark_response()],
            ) as invoke_llm:
                seed_benchmarks.generate_one("easy", Path(tmpdir), args)

            second_prompt = invoke_llm.call_args_list[1].kwargs["prompt"]
            self.assertIn("AC_TEST_MAP is missing acceptance criteria: AC3", second_prompt)
            self.assertIn("Current hidden_tests.py", second_prompt)


if __name__ == "__main__":
    unittest.main()
