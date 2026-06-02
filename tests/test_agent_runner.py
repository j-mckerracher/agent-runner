import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import yaml

from eval import agent_runner


CASE = {
    "id": "TG-RUNNER-001",
    "agent": "task-generator",
    "difficulty": "easy",
    "story": {
        "change_id": "TG-RUNNER-001",
        "title": "Runner test",
        "description": "Exercise standalone agent eval runner.",
        "acceptance_criteria": {"AC1": "The implementation task covers behavior.", "AC2": "The verification task covers tests."},
    },
    "constraints_md": "Stay scoped.",
    "expected": {
        "required_ac_ids": ["AC1", "AC2"],
        "min_tasks": 2,
        "max_tasks": 5,
        "must_include_test_task": True,
        "forbidden_scope": ["unrelated rewrite"],
    },
}


def _args(tmp_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        agent="task-generator",
        dataset=tmp_path / "dataset.jsonl",
        runner="openai-compat",
        model="unit-model",
        repo=str(tmp_path / "repo"),
        runs=1,
        pass_threshold=0.8,
        context_pack="baseline",
        prompt_path=None,
        artifact_root=tmp_path / "agent-context",
        reports_dir=tmp_path / "reports",
        dry_run=False,
        keep_context=True,
        materialize=False,
        opik=False,
        write_report=False,
        compare_to=None,
        update_baseline=False,
        regression_quality_pp=5.0,
        fail_under=None,
        require_pass_rate=None,
        allow_failures=False,
        log_level="warning",
    )


def test_run_case_scores_artifact_written_by_mocked_agent(tmp_path: Path):
    args = _args(tmp_path)
    variant, context_pack = agent_runner.build_variant(args)

    def fake_task_generator(context, runner="claude", runner_model=None):
        match = agent_runner.re.search(r"agent-context/([\w.-]+)/", context)
        assert match is not None
        run_id = match.group(1)
        path = args.artifact_root / run_id / "planning" / "tasks.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(
                {
                    "story_id": run_id,
                    "tasks": [
                        {
                            "id": "T1",
                            "title": "Implement scoped behavior",
                            "description": "Implement AC1 with a narrow local change.",
                            "ac_mapping": ["AC1"],
                            "dependencies": [],
                            "priority": "high",
                            "complexity": "simple",
                            "definition_of_done": ["Implementation is complete."],
                        },
                        {
                            "id": "T2",
                            "title": "Verify behavior with tests",
                            "description": "Add automated tests for AC1 and AC2.",
                            "ac_mapping": ["AC1", "AC2"],
                            "dependencies": ["T1"],
                            "priority": "high",
                            "complexity": "simple",
                            "definition_of_done": ["Tests pass."],
                        },
                    ],
                    "ac_coverage_matrix": {"AC1": ["T1", "T2"], "AC2": ["T2"]},
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return "wrote task plan"

    with patch("core.steps.step_task_gen_producer", side_effect=fake_task_generator):
        result = agent_runner.run_case(case=CASE, trial_index=1, args=args, variant=variant, context_pack=context_pack)

    assert result["status"] == "PASS"
    assert result["score_weighted"] == 1.0
    assert Path(result["artifact_paths"]["tasks"]).name == "tasks.yaml"


def test_dry_run_cli_writes_report(tmp_path: Path):
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(json.dumps(CASE) + "\n", encoding="utf-8")
    reports = tmp_path / "reports"
    context = tmp_path / "agent-context"
    completed = subprocess.run(
        [
            sys.executable,
            "eval/agent_runner.py",
            "--dataset",
            str(dataset),
            "--reports-dir",
            str(reports),
            "--artifact-root",
            str(context),
            "--dry-run",
            "--require-pass-rate",
            "1.0",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    report = json.loads((reports / "task-generator" / "latest.json").read_text(encoding="utf-8"))
    assert report["summary"]["pass_rate"] == 1.0
    assert report["results"][0]["status"] == "PASS"


def test_dry_run_cli_defaults_to_data_dir_runtime_roots(tmp_path: Path):
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(json.dumps(CASE) + "\n", encoding="utf-8")
    data_dir = tmp_path / "data"
    env = {**os.environ, "AGENT_RUNNER_DATA_DIR": str(data_dir)}

    completed = subprocess.run(
        [
            sys.executable,
            "eval/agent_runner.py",
            "--dataset",
            str(dataset),
            "--dry-run",
            "--require-pass-rate",
            "1.0",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )

    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert (data_dir / "eval" / "agent_reports" / "task-generator" / "latest.json").is_file()
    assert (data_dir / "agent-context").is_dir()


def test_aggregate_scores_reports_gate_pass_rates():
    results = [
        {"status": "PASS", "case_id": "A", "score_weighted": 1.0, "gates": {"schema_fields_present": True}, "metrics": {"wall_seconds": 1.0, "tokens_total": 10}},
        {"status": "FAIL", "case_id": "B", "score_weighted": 0.5, "gates": {"schema_fields_present": False}, "metrics": {"wall_seconds": 3.0, "tokens_total": 30}},
    ]
    summary = agent_runner.aggregate_scores(results)
    assert summary["reliability"]["pass_rate"] == 0.5
    assert summary["quality"]["weighted_score"] == 0.75
    assert summary["quality"]["gate_pass_rates"]["schema_fields_present"] == 0.5
