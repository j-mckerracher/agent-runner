from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

import core.run_cmds as run_cmds
from core.runner_failover import (
    RunnerFailoverCandidate,
    RunnerFailoverPolicy,
    discover_prior_runner_candidates,
    is_usage_exhaustion_error_text,
)


class UsageExhaustionClassificationTests(unittest.TestCase):
    def test_medium__recognizes_provider_usage_exhaustion_messages(self) -> None:
        messages = [
            "Anthropic API error: monthly limit reached, upgrade your plan.",
            "OpenAI error insufficient_quota: You exceeded your current quota.",
            "Copilot premium requests monthly limit reached.",
            "Gemini failed: RESOURCE_EXHAUSTED: quota exceeded.",
            "OpenAI-compatible provider: credits exhausted; payment required.",
        ]

        for message in messages:
            with self.subTest(message=message):
                self.assertTrue(is_usage_exhaustion_error_text(message))

    def test_medium__does_not_treat_transient_failures_as_usage_exhaustion(self) -> None:
        messages = [
            "rate limit exceeded, retry after 30 seconds",
            "request timeout while contacting runner",
            "503 service unavailable due to high demand",
            "too many requests",
        ]

        for message in messages:
            with self.subTest(message=message):
                self.assertFalse(is_usage_exhaustion_error_text(message))


class PriorRunnerDiscoveryTests(unittest.TestCase):
    def test_medium__discovers_history_runners_in_recent_order_and_filters_invalid(self) -> None:
        jobs = [
            {
                "runner": "gemini",
                "agent_llm_overrides": {
                    "qa-engineer": {"runner": "codex"},
                    "qa-evaluator": {"runner": "removed-alias"},
                },
            },
            {
                "runner": "claude",
                "agent_llm_overrides": '{"intake":{"runner":"ds4"},"qa-engineer":{"runner":"codex"}}',
            },
            {"runner": "bogus"},
        ]
        config = {
            "runner_aliases": {
                "ds4": {"provider": "openai-compat", "model": "deepseek-v4-pro:cloud"}
            }
        }

        candidates = discover_prior_runner_candidates(jobs, config=config, current_runner="gemini")

        self.assertEqual(
            candidates,
            [
                RunnerFailoverCandidate("codex", "gpt-5.4-nano"),
                RunnerFailoverCandidate("claude", "claude-haiku-4-5-20251001"),
                RunnerFailoverCandidate("ds4", "openai-compat/deepseek-v4-pro:cloud"),
            ],
        )


class RunAgentCmdFailoverTests(unittest.TestCase):
    def tearDown(self) -> None:
        run_cmds.set_runner_failover_policy(None)

    def test_medium__usage_exhaustion_retries_same_prompt_with_fallback_runner(self) -> None:
        policy = RunnerFailoverPolicy([RunnerFailoverCandidate("codex", "gpt-5.4-nano")])
        quota_error = subprocess.CalledProcessError(
            1,
            ["claude"],
            output="",
            stderr="insufficient_quota: monthly limit reached",
        )

        with (
            patch("core.run_cmds.run_claude_cmd", side_effect=quota_error) as run_claude,
            patch("core.run_cmds.run_codex_cmd", return_value="ok") as run_codex,
            patch("core.run_cmds._emit_event") as emit_event,
        ):
            result = run_cmds.run_agent_cmd(
                runner="claude",
                prompt="same prompt",
                agent="qa-evaluator",
                runner_model="claude-opus-4-6",
                runner_failover_policy=policy,
                repo="/tmp/repo",
                change_id="CHANGE-1",
            )

        self.assertEqual(result, "ok")
        run_claude.assert_called_once()
        run_codex.assert_called_once()
        self.assertEqual(run_codex.call_args.kwargs["prompt"], "same prompt")
        self.assertEqual(run_codex.call_args.kwargs["agent"], "qa-evaluator")
        self.assertEqual(run_codex.call_args.kwargs["model"], "gpt-5.4-nano")
        self.assertEqual(run_codex.call_args.kwargs["repo"], "/tmp/repo")
        self.assertEqual(run_codex.call_args.kwargs["change_id"], "CHANGE-1")
        self.assertEqual(emit_event.call_args_list[-1].args[0], "runner.failover")

    def test_medium__already_exhausted_runner_dispatches_directly_to_fallback(self) -> None:
        policy = RunnerFailoverPolicy([RunnerFailoverCandidate("codex", "gpt-5.4-nano")])
        policy.mark_exhausted("claude", model="claude-opus-4-6", reason="insufficient_quota")

        with (
            patch("core.run_cmds.run_claude_cmd") as run_claude,
            patch("core.run_cmds.run_codex_cmd", return_value="ok") as run_codex,
        ):
            result = run_cmds.run_agent_cmd(
                runner="claude",
                prompt="same prompt",
                agent="qa-evaluator",
                runner_model="claude-opus-4-6",
                runner_failover_policy=policy,
            )

        self.assertEqual(result, "ok")
        run_claude.assert_not_called()
        run_codex.assert_called_once()
        self.assertEqual(run_codex.call_args.kwargs["model"], "gpt-5.4-nano")

    def test_medium__transient_rate_limit_does_not_trigger_runner_failover(self) -> None:
        policy = RunnerFailoverPolicy([RunnerFailoverCandidate("codex", "gpt-5.4-nano")])
        rate_limit = subprocess.CalledProcessError(
            1,
            ["claude"],
            output="",
            stderr="rate limit exceeded, retry later",
        )

        with (
            patch("core.run_cmds.run_claude_cmd", side_effect=rate_limit),
            patch("core.run_cmds.run_codex_cmd") as run_codex,
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                run_cmds.run_agent_cmd(
                    runner="claude",
                    prompt="same prompt",
                    agent="qa-evaluator",
                    runner_model="claude-opus-4-6",
                    runner_failover_policy=policy,
                )

        run_codex.assert_not_called()

    def test_medium__usage_exhaustion_fails_clear_when_no_prior_runner_exists(self) -> None:
        policy = RunnerFailoverPolicy([])
        quota_error = subprocess.CalledProcessError(
            1,
            ["claude"],
            output="",
            stderr="quota exceeded",
        )

        with (
            patch("core.run_cmds.run_claude_cmd", side_effect=quota_error),
            patch("core.run_cmds._emit_event") as emit_event,
        ):
            with self.assertRaisesRegex(RuntimeError, "no eligible prior fallback runner"):
                run_cmds.run_agent_cmd(
                    runner="claude",
                    prompt="same prompt",
                    agent="qa-evaluator",
                    runner_model="claude-opus-4-6",
                    runner_failover_policy=policy,
                )

        self.assertEqual(emit_event.call_args.args[0], "runner.failover_unavailable")


if __name__ == "__main__":
    unittest.main()
