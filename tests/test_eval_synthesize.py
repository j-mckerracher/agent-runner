import json
import shutil
import unittest
from pathlib import Path
from unittest import mock

from eval.dataset_manifest import load_dataset_lock
from eval.init_dataset import initialize_dataset
from eval.models import CheckResult, ScoreSummary
from eval.run_eval import StoryRun
from eval.suite_io import load_eval_story
from eval.synthesize import (
    CALIBRATION_RUNS,
    SynthesisError,
    main,
    synthesize_suites,
    validate_raw_ac,
)
from eval.yaml_io import dump_yaml, load_yaml_mapping
from core.workflow_inputs import load_story_fixture


def _extract_prompt_payload(prompt: str) -> dict:
    marker = "Input payload:\n"
    return json.loads(prompt.split(marker, 1)[1])


def _story_acceptance_criteria(story_id: str, story_tier: str) -> list[dict]:
    if story_tier == "easy":
        return [
            {
                "ac_id": f"{story_id}-E1",
                "tier": "easy",
                "text": "Agent output mentions the repository-wide easy improvement.",
                "check_mechanism": "contains",
                "check_subject": "agent_output",
                "expected": "repository-wide easy",
                "rationale": "Easy checks should be obvious and directly verifiable from agent output.",
            }
        ]
    if story_tier == "medium":
        return [
            {
                "ac_id": f"{story_id}-M1",
                "tier": "medium",
                "text": "Agent output describes implementation work across the repository.",
                "check_mechanism": "matches",
                "check_subject": "agent_output",
                "expected": r"implement|implementation|refactor",
                "rationale": "Medium checks should verify stronger implementation intent or structure.",
            }
        ]
    return [
        {
            "ac_id": f"{story_id}-H1",
            "tier": "hard",
            "text": "Repository compiles.",
            "check_mechanism": "command",
            "check_subject": "repo",
            "command": ["python3", "-m", "compileall", "eval"],
            "rationale": "Hard checks should require an executable repository validation step.",
        }
    ]


def _fake_story_response(prompt: str, *, suffix: str = "") -> str:
    payload = _extract_prompt_payload(prompt)
    stories = []
    for item in payload["requested_stories"]:
        story_id = item["story_id"]
        story_tier = item["story_tier"]
        title_piece = f"Repository-wide {story_tier} workflow improvement"
        if suffix:
            title_piece = f"{title_piece}{suffix}"
        stories.append(
            {
                "story_id": story_id,
                "story_tier": story_tier,
                "title": title_piece,
                "description": f"Implement a {story_tier} repository-wide workflow improvement.",
                "prompt": f"Build a {story_tier} repository-wide workflow improvement.",
                "acceptance_criteria": _story_acceptance_criteria(story_id, story_tier),
            }
        )
    return json.dumps({"stories": stories})


def _fake_ac_response(prompt: str, *, text_prefix: str = "Recalibrated") -> str:
    payload = _extract_prompt_payload(prompt)
    story = payload["story"]
    story_id = story["story_id"]
    story_tier = story["story_tier"]
    if story_tier == "easy":
        acceptance_criteria = [
            {
                "ac_id": f"{story_id}-E2",
                "tier": "easy",
                "text": f"{text_prefix} easy AC",
                "check_mechanism": "contains",
                "check_subject": "agent_output",
                "expected": "repository-wide easy",
                "rationale": "Adjusted easy AC after calibration feedback.",
            }
        ]
    elif story_tier == "medium":
        acceptance_criteria = [
            {
                "ac_id": f"{story_id}-M2",
                "tier": "medium",
                "text": f"{text_prefix} medium AC",
                "check_mechanism": "matches",
                "check_subject": "agent_output",
                "expected": r"implement|implementation|refactor",
                "rationale": "Adjusted medium AC after calibration feedback.",
            }
        ]
    else:
        acceptance_criteria = [
            {
                "ac_id": f"{story_id}-H2",
                "tier": "hard",
                "text": f"{text_prefix} hard AC",
                "check_mechanism": "command",
                "check_subject": "repo",
                "command": ["python3", "-m", "compileall", "eval"],
                "rationale": "Adjusted hard AC after calibration feedback.",
            }
        ]
    return json.dumps({"acceptance_criteria": acceptance_criteria})


def _default_fake_agent_response(prompt: str) -> str:
    payload = _extract_prompt_payload(prompt)
    if "requested_stories" in payload:
        return _fake_story_response(prompt)
    return _fake_ac_response(prompt)


def _build_story_runs(story_path: Path, *, passed_runs: int, total_runs: int = CALIBRATION_RUNS) -> list[StoryRun]:
    story = load_eval_story(story_path)
    story_runs: list[StoryRun] = []
    for run_index in range(1, total_runs + 1):
        run_passed = run_index <= passed_runs
        checks = [
            CheckResult(
                check_id=criterion.ac_id,
                passed=run_passed,
                attempted=True,
                mechanism=criterion.check.mechanism if criterion.check else None,
                subject=criterion.check.subject if criterion.check else None,
                message="",
            )
            for criterion in story.acceptance_criteria
        ]
        total_checks = len(checks)
        story_runs.append(
            StoryRun(
                story=story,
                story_path=story_path,
                fixture_path=story_path,
                change_id=f"{story.change_id or story.story_id}-RUN-{run_index:02d}",
                subprocess_returncode=0,
                artifact_text="",
                checks=checks,
                summary=ScoreSummary(
                    total_checks=total_checks,
                    passed_checks=total_checks if run_passed else 0,
                    attempted_checks=total_checks,
                    weighted_composite=1.0 if run_passed else 0.0,
                    attempted_rate=1.0,
                ),
            )
        )
    return story_runs


def _default_trial_side_effect(*, story_path: Path, **kwargs) -> list[StoryRun]:
    story = load_eval_story(story_path)
    total_runs = kwargs.get("runs", CALIBRATION_RUNS)
    if story.suite_tier == "easy":
        passed_runs = total_runs
    elif story.suite_tier == "medium":
        passed_runs = max(1, (2 * total_runs) // 3)
    else:
        passed_runs = max(1, total_runs // 3)
    selected_run_indices = [run_spec.run_index for run_spec in (kwargs.get("run_specs") or [])]
    story_runs = _build_story_runs(story_path, passed_runs=passed_runs, total_runs=total_runs)
    if not selected_run_indices:
        return story_runs
    return [story_runs[run_index - 1] for run_index in selected_run_indices]


class EvalSynthesizeTests(unittest.TestCase):
    def setUp(self):
        self.workdir = Path(__file__).parent / ".synthesize_workdir"
        if self.workdir.exists():
            shutil.rmtree(self.workdir)
        self.workdir.mkdir()
        self.manifest_path = self._write_initialized_dataset()
        self.suites_dir = self.workdir / "suites"
        self.stories_dir = self.workdir / "stories"

    def tearDown(self):
        if self.workdir.exists():
            shutil.rmtree(self.workdir)

    def _write_initialized_dataset(self):
        records_path = self.workdir / "records.jsonl"
        records = [
            {"id": "R1", "name": "Ada", "tier": "alpha"},
            {"id": "R2", "name": "Grace", "tier": "beta"},
        ]
        with records_path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
        manifest_path = self.workdir / "claims.yaml"
        dump_yaml(
            {
                "dataset_id": "claims",
                "display_name": "Claims",
                "source": {"type": "jsonl", "path": "records.jsonl"},
                "sampling": {"strategy": "head", "sample_size": 2, "seed": 123},
                "domain_context": "Claims workflow improvements.",
            },
            manifest_path,
        )
        initialize_dataset(manifest_path)
        return manifest_path

    def _run_synthesis(self, agent_side_effect=None, trial_side_effect=None, **kwargs):
        agent_side_effect = agent_side_effect or (lambda runner, prompt, agent, **call_kwargs: _default_fake_agent_response(prompt))
        trial_side_effect = trial_side_effect or _default_trial_side_effect
        with mock.patch("eval.synthesize.run_agent_cmd", side_effect=agent_side_effect) as patched_agent, mock.patch(
            "eval.synthesize.run_story_trials", side_effect=trial_side_effect
        ) as patched_trials:
            report = synthesize_suites(
                manifest_path=self.manifest_path,
                output_dir=self.suites_dir,
                stories_output_dir=self.stories_dir,
                runner="copilot",
                model="gpt-5-mini",
                agent="task-generator",
                **kwargs,
            )
        return report, patched_agent, patched_trials

    def test_default_synthesis_generates_predicted_tier_outputs(self):
        report, patched_agent, patched_trials = self._run_synthesis()

        self.assertEqual(patched_agent.call_count, 1)
        patched_trials.assert_not_called()
        self.assertEqual(report["tiering_mode"], "predicted")
        self.assertFalse(report["calibration_enabled"])
        self.assertIsNone(report["calibration_repo"])
        self.assertEqual(report["calibration_runs"], 0)
        self.assertEqual(report["tiering"]["story_001"]["predicted_tier"], "easy")
        self.assertEqual(report["tiering"]["story_002"]["predicted_tier"], "medium")
        self.assertEqual(report["tiering"]["story_003"]["predicted_tier"], "hard")

        raw_stories_path = self.suites_dir / "raw" / "stories.jsonl"
        self.assertTrue(raw_stories_path.exists())
        self.assertFalse((self.suites_dir / "raw" / "story_001_acs.json").exists())
        raw_stories = [json.loads(line) for line in raw_stories_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([story["story_id"] for story in raw_stories], ["story_001", "story_002", "story_003"])
        self.assertIn("tiering", raw_stories[0]["metadata"])
        self.assertNotIn("calibration", raw_stories[0]["metadata"])

        expected_story_ids_by_tier = {
            "easy": "story_001_easy",
            "medium": "story_002_medium",
            "hard": "story_003_hard",
        }
        for tier, story_id in expected_story_ids_by_tier.items():
            tier_yaml = self.suites_dir / tier / f"{story_id}.yaml"
            self.assertTrue(tier_yaml.exists())
            story_payload = load_yaml_mapping(tier_yaml)
            self.assertEqual(story_payload["suite_tier"], tier)
            self.assertEqual(story_payload["metadata"]["tiering"]["requested_tier"], tier)
            self.assertEqual(story_payload["metadata"]["tiering"]["predicted_tier"], tier)
            self.assertNotIn("calibration", story_payload["metadata"])

            manifest = load_yaml_mapping(self.suites_dir / tier / "suite_manifest.yaml")
            self.assertEqual(manifest["dataset_id"], "claims")
            self.assertEqual(manifest["dataset_lock_hash"], load_dataset_lock(self.workdir / "claims.lock").source_fingerprint)
            self.assertEqual(manifest["generated_runner"], "copilot")
            self.assertEqual(manifest["generated_model"], "gpt-5-mini")
            self.assertEqual(manifest["tiering_mode"], "predicted")

            fixture_path = self.stories_dir / f"{story_id}.json"
            fixture = load_story_fixture(str(fixture_path))
            self.assertEqual(fixture["change_id"], story_id)
            self.assertEqual(fixture["metadata"]["tiering"]["predicted_tier"], tier)
            self.assertNotIn("calibration", fixture["metadata"])
            self.assertEqual(fixture["raw_metadata"]["suite_yaml"], str(tier_yaml))

    def test_opt_in_calibration_runs_story_trials_and_emits_metadata(self):
        report, patched_agent, patched_trials = self._run_synthesis(calibrate=True)

        self.assertEqual(patched_agent.call_count, 1)
        self.assertEqual(patched_trials.call_count, CALIBRATION_RUNS * 3)
        self.assertEqual(report["tiering_mode"], "calibrated")
        self.assertTrue(report["calibration_enabled"])
        self.assertEqual(report["calibration"]["story_001"]["selected_iteration"], 1)
        self.assertEqual(report["tiering"]["story_003"]["predicted_tier"], "hard")

        raw_stories = [json.loads(line) for line in (self.suites_dir / "raw" / "stories.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertIn("tiering", raw_stories[0]["metadata"])
        self.assertIn("calibration", raw_stories[0]["metadata"])
        self.assertEqual(raw_stories[0]["metadata"]["calibration"]["selected_iteration"], 1)

    def test_existing_predicted_raw_stories_are_reused_without_hints(self):
        self._run_synthesis()

        with mock.patch("eval.synthesize.run_agent_cmd") as patched_agent, mock.patch(
            "eval.synthesize.run_story_trials"
        ) as patched_trials:
            report = synthesize_suites(
                manifest_path=self.manifest_path,
                output_dir=self.suites_dir,
                stories_output_dir=self.stories_dir,
                runner="copilot",
                model="gpt-5-mini",
                agent="task-generator",
            )

        patched_agent.assert_not_called()
        patched_trials.assert_not_called()
        self.assertEqual(report["synthesized_story_count"], 0)
        self.assertEqual(report["reused_story_count"], 3)
        self.assertTrue(report["tiering"]["story_001"]["reused"])

    def test_missing_lock_fails_clearly(self):
        (self.workdir / "claims.lock").unlink()

        with self.assertRaisesRegex(SynthesisError, "Missing dataset lock"):
            self._run_synthesis()

    def test_missing_sample_fails_clearly(self):
        sample_path = self.workdir / "samples" / "claims_sample.jsonl"
        sample_path.unlink()

        with self.assertRaisesRegex(SynthesisError, "Missing dataset sample"):
            self._run_synthesis()

    def test_llm_story_order_does_not_have_to_match_prompt_order(self):
        def fake_agent(runner, prompt, agent, **kwargs):
            payload = _extract_prompt_payload(prompt)
            if "requested_stories" in payload:
                stories = json.loads(_fake_story_response(prompt))["stories"]
                stories.reverse()
                return json.dumps({"stories": stories})
            return _fake_ac_response(prompt)

        report, patched_agent, patched_trials = self._run_synthesis(agent_side_effect=fake_agent)

        self.assertEqual(patched_agent.call_count, 1)
        patched_trials.assert_not_called()
        self.assertEqual(report["synthesized_story_count"], 3)
        raw_stories = [json.loads(line) for line in (self.suites_dir / "raw" / "stories.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([story["story_id"] for story in raw_stories], ["story_001", "story_002", "story_003"])

    def test_story_tier_and_ac_tiers_must_align(self):
        def fake_agent(runner, prompt, agent, **kwargs):
            response = json.loads(_fake_story_response(prompt))
            response["stories"][0]["acceptance_criteria"][0]["tier"] = "medium"
            return json.dumps(response)

        with self.assertRaisesRegex(SynthesisError, "acceptance criteria tiers must all match story_tier easy"):
            self._run_synthesis(agent_side_effect=fake_agent)

    def test_ac_hints_resynthesizes_only_flagged_existing_story(self):
        self._run_synthesis()
        hints_path = self.workdir / "story_hints.json"
        with hints_path.open("w", encoding="utf-8") as handle:
            json.dump({"flagged_stories": [{"story_id": "story_001", "hint": "Make the easy check more concrete."}]}, handle)

        def fake_agent(runner, prompt, agent, **kwargs):
            payload = _extract_prompt_payload(prompt)
            if "requested_stories" in payload:
                self.assertIn("Make the easy check more concrete.", prompt)
                self.assertIn("story_001", prompt)
                self.assertNotIn('"story_id": "story_002"', prompt)
                self.assertNotIn('"story_id": "story_003"', prompt)
                return _fake_story_response(prompt, suffix=" revised")
            return _fake_ac_response(prompt)

        report, patched_agent, patched_trials = self._run_synthesis(agent_side_effect=fake_agent, ac_hints_path=hints_path)

        self.assertEqual(patched_agent.call_count, 1)
        patched_trials.assert_not_called()
        self.assertEqual(report["synthesized_story_count"], 1)
        self.assertEqual(report["reused_story_count"], 2)
        raw_after = [json.loads(line) for line in (self.suites_dir / "raw" / "stories.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(raw_after[0]["title"], "Repository-wide easy workflow improvement revised")
        self.assertTrue(report["tiering"]["story_002"]["reused"])

    def test_command_checks_fall_back_to_check_subject_for_legacy_llm_output(self):
        def fake_agent(runner, prompt, agent, **kwargs):
            response = json.loads(_fake_story_response(prompt))
            hard_story = next(story for story in response["stories"] if story["story_tier"] == "hard")
            command_ac = hard_story["acceptance_criteria"][0]
            command_ac.pop("command")
            command_ac["check_subject"] = "python3 -m compileall eval"
            return json.dumps(response)

        self._run_synthesis(agent_side_effect=fake_agent)

        hard_story = load_yaml_mapping(self.suites_dir / "hard" / "story_003_hard.yaml")
        hard_check = hard_story["acceptance_criteria"][0]["check"]
        self.assertEqual(hard_check["subject"], "repo")
        self.assertEqual(hard_check["command"], "python3 -m compileall eval")

    def test_command_checks_can_infer_command_from_text_when_subject_is_repo(self):
        def fake_agent(runner, prompt, agent, **kwargs):
            response = json.loads(_fake_story_response(prompt))
            hard_story = next(story for story in response["stories"] if story["story_tier"] == "hard")
            command_ac = hard_story["acceptance_criteria"][0]
            command_ac.pop("command")
            command_ac["text"] = (
                "Run the angular-library generator in dry-run mode: "
                "pnpm nx g @shared/plugins/mcs-plugin:angular-library sample-lib --dry-run "
                "(command should complete without error)."
            )
            return json.dumps(response)

        self._run_synthesis(agent_side_effect=fake_agent)

        hard_story = load_yaml_mapping(self.suites_dir / "hard" / "story_003_hard.yaml")
        hard_check = hard_story["acceptance_criteria"][0]["check"]
        self.assertEqual(
            hard_check["command"],
            "pnpm nx g @shared/plugins/mcs-plugin:angular-library sample-lib --dry-run",
        )

    def test_command_checks_accept_argv_alias_from_llm_output(self):
        def fake_agent(runner, prompt, agent, **kwargs):
            response = json.loads(_fake_story_response(prompt))
            hard_story = next(story for story in response["stories"] if story["story_tier"] == "hard")
            command_ac = hard_story["acceptance_criteria"][0]
            command_ac["argv"] = command_ac.pop("command")
            return json.dumps(response)

        self._run_synthesis(agent_side_effect=fake_agent)

        hard_story = load_yaml_mapping(self.suites_dir / "hard" / "story_003_hard.yaml")
        hard_check = hard_story["acceptance_criteria"][0]["check"]
        self.assertEqual(hard_check["subject"], "repo")
        self.assertEqual(hard_check["command"], ["python3", "-m", "compileall", "eval"])

    def test_missing_command_can_be_inferred_from_symbol_in_file_text(self):
        ac = validate_raw_ac(
            {
                "ac_id": "AC1",
                "tier": "easy",
                "text": "The file pearls/core-home/features/home-feat/src/lib/constants/card-constants.ts contains an exported constant named SYSTEM_STATUS_CARD.",
                "check_mechanism": "command",
                "check_subject": "repo",
                "rationale": "Verify the constant exists.",
            },
            story_id="story_001",
        )

        self.assertEqual(
            ac["command"],
            ["grep", "SYSTEM_STATUS_CARD", "pearls/core-home/features/home-feat/src/lib/constants/card-constants.ts"],
        )

    def test_missing_command_can_be_inferred_from_unit_test_text(self):
        ac = validate_raw_ac(
            {
                "ac_id": "AC4",
                "tier": "hard",
                "text": "The Order Results UI unit tests pass without failures.",
                "check_mechanism": "command",
                "check_subject": "repo",
                "rationale": "Ensure tests pass.",
            },
            story_id="story_003",
        )

        self.assertEqual(ac["command"], ["npx", "nx", "test", "order-results-ui"])

    def test_main_defaults_to_predicted_synthesis_without_calibration(self):
        with mock.patch("eval.synthesize.run_agent_cmd", side_effect=lambda runner, prompt, agent, **kwargs: _default_fake_agent_response(prompt)), mock.patch(
            "eval.synthesize.run_story_trials", side_effect=_default_trial_side_effect
        ) as patched_trials:
            rc = main(
                [
                    "--dataset",
                    str(self.manifest_path),
                    "--output",
                    str(self.suites_dir),
                    "--stories-output",
                    str(self.stories_dir),
                ]
            )
        self.assertEqual(rc, 0)
        patched_trials.assert_not_called()

    def test_main_calibrate_opt_in_uses_default_runner_profile(self):
        with mock.patch("eval.synthesize.run_agent_cmd", side_effect=lambda runner, prompt, agent, **kwargs: _default_fake_agent_response(prompt)), mock.patch(
            "eval.synthesize.run_story_trials", side_effect=_default_trial_side_effect
        ) as patched_trials, mock.patch(
            "eval.synthesize.resolve_runner_model", side_effect=lambda runner, **_: runner
        ):
            rc = main(
                [
                    "--dataset",
                    str(self.manifest_path),
                    "--output",
                    str(self.suites_dir),
                    "--stories-output",
                    str(self.stories_dir),
                    "--calibrate",
                ]
            )
        self.assertEqual(rc, 0)
        first_call = patched_trials.call_args_list[0]
        self.assertEqual(first_call.kwargs["runs"], 6)
        self.assertEqual(
            [run_spec.runner for run_spec in first_call.kwargs["run_specs"]],
            [
                "copilot-gemma4",
                "copilot-gemma4",
                "copilot-deepseek-v4-flash",
                "copilot-deepseek-v4-flash",
                "copilot-minimax-m2.7",
                "copilot-minimax-m2.7",
            ],
        )


def test_smoke_synthesize_outputs_workflow_valid_fixtures():
    workdir = Path(__file__).parent / ".synthesize_smoke_workdir"
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir()
    try:
        records_path = workdir / "records.jsonl"
        with records_path.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps({"id": "S1", "name": "Smoke"}, sort_keys=True) + "\n")
        manifest_path = workdir / "smoke.yaml"
        dump_yaml(
            {
                "dataset_id": "smoke",
                "display_name": "Smoke",
                "source": {"type": "jsonl", "path": "records.jsonl"},
                "sampling": {"strategy": "head", "sample_size": 1, "seed": 1},
                "domain_context": "Smoke testing.",
            },
            manifest_path,
        )
        initialize_dataset(manifest_path)
        with mock.patch("eval.synthesize.run_agent_cmd", side_effect=lambda runner, prompt, agent, **kwargs: _default_fake_agent_response(prompt)), mock.patch(
            "eval.synthesize.run_story_trials", side_effect=_default_trial_side_effect
        ):
            synthesize_suites(
                manifest_path=manifest_path,
                output_dir=workdir / "suites",
                stories_output_dir=workdir / "stories",
            )

        assert (workdir / "suites" / "easy" / "suite_manifest.yaml").exists()
        assert (workdir / "suites" / "medium" / "suite_manifest.yaml").exists()
        assert (workdir / "suites" / "hard" / "suite_manifest.yaml").exists()
        fixture = load_story_fixture(str(workdir / "stories" / "story_001_easy.json"))
        assert fixture["change_id"] == "story_001_easy"
        assert fixture["acceptance_criteria"]
        assert fixture["metadata"]["tiering"]["predicted_tier"] == "easy"
        assert "calibration" not in fixture["metadata"]
    finally:
        if workdir.exists():
            shutil.rmtree(workdir)


if __name__ == "__main__":
    unittest.main()
