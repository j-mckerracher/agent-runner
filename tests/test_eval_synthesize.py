import json
import shutil
import unittest
from pathlib import Path
from unittest import mock

from eval.dataset_manifest import load_dataset_lock
from eval.init_dataset import initialize_dataset
from eval.synthesize import SynthesisError, main, synthesize_suites
from eval.yaml_io import load_yaml_mapping, dump_yaml
from workflow_inputs import load_story_fixture


REPO_ROOT = Path(__file__).resolve().parent.parent


def _fake_llm_response(prompt, *, suffix=""):
    marker = "Input payload:\n"
    payload = json.loads(prompt.split(marker, 1)[1])
    stories = []
    for item in payload["records"]:
        story_id = item["story_id"]
        record = item["record"]
        title_piece = record.get("name") or record.get("id") or story_id
        stories.append(
            {
                "story_id": story_id,
                "title": f"Improve record {title_piece}{suffix}",
                "description": f"Implement workflow support for sample record {title_piece}.",
                "prompt": f"Build a workflow improvement for {title_piece}.",
                "acceptance_criteria": [
                    {
                        "ac_id": f"{story_id}-E",
                        "tier": "easy",
                        "text": f"Output contains {title_piece}",
                        "check_mechanism": "contains",
                        "check_subject": "agent_output",
                        "expected": str(title_piece),
                        "rationale": "Easy check validates the visible record identifier.",
                    },
                    {
                        "ac_id": f"{story_id}-M",
                        "tier": "medium",
                        "text": "Output mentions implementation",
                        "check_mechanism": "matches",
                        "check_subject": "agent_output",
                        "expected": r"implement|implementation",
                        "rationale": "Medium check validates implementation detail.",
                    },
                    {
                        "ac_id": f"{story_id}-H",
                        "tier": "hard",
                        "text": "Repository compiles",
                        "check_mechanism": "command",
                        "check_subject": "repo",
                        "command": ["python3", "-m", "compileall", "eval"],
                        "rationale": "Hard check validates an executable command.",
                    },
                ],
            }
        )
    return json.dumps({"stories": stories})


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

    def _run_synthesis(self, fake=None, **kwargs):
        fake = fake or (lambda runner, prompt, agent, **call_kwargs: _fake_llm_response(prompt))
        batch_size = kwargs.pop("batch_size", 1)
        with mock.patch("eval.synthesize.run_cmds.run_agent_cmd", side_effect=fake) as patched:
            report = synthesize_suites(
                manifest_path=self.manifest_path,
                output_dir=self.suites_dir,
                stories_output_dir=self.stories_dir,
                runner="copilot",
                model="gpt-5-mini",
                agent="task-generator",
                batch_size=batch_size,
                **kwargs,
            )
        return report, patched

    def test_deterministic_fake_llm_generates_raw_tier_manifests_and_workflow_json(self):
        report, patched = self._run_synthesis()

        self.assertEqual(patched.call_count, 2)
        self.assertEqual(report["dataset_id"], "claims")
        raw_stories_path = self.suites_dir / "raw" / "stories.jsonl"
        self.assertTrue(raw_stories_path.exists())
        raw_stories = [json.loads(line) for line in raw_stories_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([story["story_id"] for story in raw_stories], ["story_001", "story_002"])
        self.assertTrue((self.suites_dir / "raw" / "story_001_acs.json").exists())

        for tier in ("easy", "medium", "hard"):
            tier_yaml = self.suites_dir / tier / f"story_001_{tier}.yaml"
            self.assertTrue(tier_yaml.exists())
            story_payload = load_yaml_mapping(tier_yaml)
            self.assertEqual(story_payload["title"], "Improve record Ada")
            self.assertEqual(story_payload["suite_tier"], tier)
            self.assertEqual(len(story_payload["acceptance_criteria"]), 1)
            self.assertEqual(story_payload["acceptance_criteria"][0]["tier"], tier)

            manifest = load_yaml_mapping(self.suites_dir / tier / "suite_manifest.yaml")
            self.assertEqual(manifest["dataset_id"], "claims")
            self.assertEqual(manifest["dataset_lock_hash"], load_dataset_lock(self.workdir / "claims.lock").source_fingerprint)
            self.assertEqual(manifest["total_checks"], 2)
            self.assertEqual(manifest["generated_runner"], "copilot")
            self.assertEqual(manifest["generated_model"], "gpt-5-mini")
            self.assertEqual(manifest["compatibility_story_ids"], [f"story_001_{tier}", f"story_002_{tier}"])

            fixture_path = self.stories_dir / f"story_001_{tier}.json"
            fixture = load_story_fixture(str(fixture_path))
            self.assertEqual(fixture["change_id"], f"story_001_{tier}")
            self.assertEqual(len(fixture["acceptance_criteria"]), 1)
            self.assertEqual(fixture["raw_metadata"]["suite_yaml"], str(tier_yaml))

        synthesis_report = json.loads((self.suites_dir / "synthesis_report.json").read_text(encoding="utf-8"))
        self.assertEqual(synthesis_report["sample_count"], 2)
        self.assertIn("Current source schema drift", "\n".join(synthesis_report["warnings"]))

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
        def fake(runner, prompt, agent, **kwargs):
            marker = "Input payload:\n"
            payload = json.loads(prompt.split(marker, 1)[1])
            stories = json.loads(_fake_llm_response(prompt))["stories"]
            if len(payload["records"]) == 2:
                stories.reverse()
            return json.dumps({"stories": stories})

        report, patched = self._run_synthesis(fake=fake, batch_size=2)

        self.assertEqual(patched.call_count, 1)
        self.assertEqual(report["synthesized_story_count"], 2)
        raw_stories = [json.loads(line) for line in (self.suites_dir / "raw" / "stories.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([story["story_id"] for story in raw_stories], ["story_001", "story_002"])

    def test_missing_tier_acs_are_reported_without_padding(self):
        def fake(runner, prompt, agent, **kwargs):
            response = json.loads(_fake_llm_response(prompt))
            for story in response["stories"]:
                story["acceptance_criteria"] = [
                    ac for ac in story["acceptance_criteria"] if ac["tier"] != "hard"
                ]
            return json.dumps(response)

        report, _ = self._run_synthesis(fake=fake)

        warnings = "\n".join(report["warnings"])
        self.assertIn("no ACs for tier(s): hard", warnings)
        self.assertEqual(report["tiers"]["hard"]["total_checks"], 0)
        self.assertFalse((self.stories_dir / "story_001_hard.json").exists())

    def test_ac_hints_resynthesizes_only_flagged_existing_story(self):
        self._run_synthesis()
        raw_before = [json.loads(line) for line in (self.suites_dir / "raw" / "stories.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(raw_before[1]["title"], "Improve record Grace")
        hints_path = self.workdir / "calibration_report.json"
        with hints_path.open("w", encoding="utf-8") as handle:
            json.dump({"flagged_stories": [{"story_id": "story_001", "hint": "Make the easy check more concrete."}]}, handle)

        prompts = []

        def fake(runner, prompt, agent, **kwargs):
            prompts.append(prompt)
            self.assertIn("Make the easy check more concrete.", prompt)
            self.assertIn("story_001", prompt)
            self.assertNotIn("story_002", prompt)
            return _fake_llm_response(prompt, suffix=" revised")

        report, patched = self._run_synthesis(fake=fake, ac_hints_path=hints_path)

        self.assertEqual(patched.call_count, 1)
        self.assertEqual(report["synthesized_story_count"], 1)
        self.assertEqual(report["reused_story_count"], 1)
        raw_after = [json.loads(line) for line in (self.suites_dir / "raw" / "stories.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(raw_after[0]["title"], "Improve record Ada revised")
        self.assertEqual(raw_after[1]["title"], "Improve record Grace")
        self.assertEqual(prompts[0].count("story_001"), 2)  # record id plus hint line

    def test_main_returns_nonzero_for_missing_lock_and_zero_for_success_with_patch(self):
        with mock.patch("eval.synthesize.run_cmds.run_agent_cmd", side_effect=lambda r, p, a, **k: _fake_llm_response(p)):
            rc = main(
                [
                    "--dataset",
                    str(self.manifest_path),
                    "--output",
                    str(self.suites_dir),
                    "--stories-output",
                    str(self.stories_dir),
                    "--batch-size",
                    "2",
                ]
            )
        self.assertEqual(rc, 0)


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
        with mock.patch("eval.synthesize.run_cmds.run_agent_cmd", side_effect=lambda r, p, a, **k: _fake_llm_response(p)):
            synthesize_suites(
                manifest_path=manifest_path,
                output_dir=workdir / "suites",
                stories_output_dir=workdir / "stories",
                batch_size=5,
            )

        assert (workdir / "suites" / "easy" / "suite_manifest.yaml").exists()
        fixture = load_story_fixture(str(workdir / "stories" / "story_001_easy.json"))
        assert fixture["change_id"] == "story_001_easy"
        assert fixture["acceptance_criteria"]
    finally:
        if workdir.exists():
            shutil.rmtree(workdir)


if __name__ == "__main__":
    unittest.main()
