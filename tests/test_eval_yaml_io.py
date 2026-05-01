import shutil
import unittest
from pathlib import Path

from eval.dataset_manifest import load_dataset_manifest
from eval.models import AcceptanceCriterion, EvalStory, SuiteManifest
from eval.suite_io import dump_eval_story, dump_suite_manifest, load_eval_story, load_suite_manifest
from eval.yaml_io import YamlError, dump_yaml, load_yaml_mapping


class EvalYamlIOTests(unittest.TestCase):
    def setUp(self):
        self.workdir = Path(__file__).parent / ".phase1_yaml_io_workdir"
        if self.workdir.exists():
            shutil.rmtree(self.workdir)
        self.workdir.mkdir()

    def tearDown(self):
        if self.workdir.exists():
            shutil.rmtree(self.workdir)

    def test_dump_and_load_yaml_mapping(self):
        path = self.workdir / "manifest.yaml"

        dump_yaml({"dataset_id": "claims", "sampling": {"sample_size": 1}}, path)
        loaded = load_yaml_mapping(path)

        self.assertEqual(loaded["dataset_id"], "claims")

    def test_load_yaml_mapping_rejects_missing_file(self):
        with self.assertRaisesRegex(YamlError, "not found"):
            load_yaml_mapping(self.workdir / "missing.yaml")

    def test_dataset_manifest_validation_requires_positive_sample_size(self):
        path = self.workdir / "dataset.yaml"
        dump_yaml(
            {
                "dataset_id": "claims",
                "display_name": "Claims",
                "source": {"type": "csv", "path": "claims.csv"},
                "sampling": {"strategy": "head", "sample_size": 0},
                "domain_context": "Claims domain",
            },
            path,
        )

        with self.assertRaisesRegex(ValueError, "sample_size"):
            load_dataset_manifest(path)

    def test_story_yaml_round_trip(self):
        path = self.workdir / "story.yaml"
        story = EvalStory(
            story_id="story-001",
            title="Story",
            description="Description",
            acceptance_criteria=[AcceptanceCriterion(ac_id="AC1", text="Do it", tier="easy")],
        )

        dump_eval_story(story, path)
        loaded = load_eval_story(path)

        self.assertEqual(loaded.acceptance_criteria[0].text, "Do it")

    def test_suite_manifest_yaml_round_trip(self):
        path = self.workdir / "suite_manifest.yaml"
        suite = SuiteManifest(
            suite_id="claims-hard",
            suite_tier="hard",
            dataset_id="claims",
            stories=["story-001.yaml"],
            total_checks=3,
        )

        dump_suite_manifest(suite, path)
        loaded = load_suite_manifest(path)

        self.assertEqual(loaded.suite_tier, "hard")
        self.assertEqual(loaded.total_checks, 3)


if __name__ == "__main__":
    unittest.main()
