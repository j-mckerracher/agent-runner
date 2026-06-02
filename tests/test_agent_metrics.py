from pathlib import Path

import yaml

from eval.agent_metrics import aggregate_scores, score_task_generator_case, score_task_plan


CASE = {
    "id": "TG-TEST",
    "agent": "task-generator",
    "story": {
        "change_id": "TG-TEST",
        "title": "Example",
        "description": "Example story",
        "acceptance_criteria": ["AC1: Implement behavior", "AC2: Preserve compatibility", "AC3: Add tests"],
    },
    "expected": {
        "required_ac_ids": ["AC1", "AC2", "AC3"],
        "critical_ac_ids": ["AC1"],
        "min_tasks": 2,
        "max_tasks": 5,
        "must_include_test_task": True,
        "forbidden_scope": ["external service"],
    },
}


def good_plan():
    return {
        "story_id": "TG-TEST",
        "tasks": [
            {
                "id": "T1",
                "title": "Implement scoped behavior",
                "description": "Implement the local behavior and preserve compatibility.",
                "ac_mapping": ["AC1", "AC2"],
                "dependencies": [],
                "priority": "high",
                "complexity": "moderate",
                "definition_of_done": ["Implementation remains scoped."],
            },
            {
                "id": "T2",
                "title": "Verify acceptance behavior",
                "description": "Run tests and validate the acceptance criteria.",
                "ac_mapping": ["AC3"],
                "dependencies": ["T1"],
                "priority": "medium",
                "complexity": "simple",
                "definition_of_done": ["Unit tests cover the acceptance criteria."],
            },
        ],
        "ac_coverage_matrix": {"AC1": ["T1"], "AC2": ["T1"], "AC3": ["T2"]},
    }


def test_score_task_plan_passes_valid_plan():
    score = score_task_plan(story=CASE["story"], expected=CASE["expected"], task_plan=good_plan()).to_dict()
    assert score["status"] == "PASS"
    assert score["gates"]["schema_fields_present"] is True
    assert score["gates"]["required_ac_ids_covered"] is True
    assert score["gates"]["dependency_graph_acyclic"] is True
    assert score["score_weighted"] == 1.0


def test_score_task_plan_fails_missing_coverage_and_unknown_ac_warning():
    plan = good_plan()
    plan["tasks"][1]["ac_mapping"] = ["AC99"]
    plan["ac_coverage_matrix"] = {"AC1": ["T1"], "AC2": ["T1"], "AC99": ["T2"]}
    score = score_task_plan(story=CASE["story"], expected=CASE["expected"], task_plan=plan).to_dict()
    assert score["status"] == "FAIL"
    assert score["gates"]["required_ac_ids_covered"] is False
    assert score["missing_ac_ids"] == ["AC3"]
    assert any(issue.startswith("ac_coverage_missing") for issue in score["issues"])
    assert any(warning.startswith("ac_coverage_unknown") for warning in score["warnings"])


def test_score_task_plan_fails_cycles_and_forbidden_scope():
    plan = good_plan()
    plan["tasks"][0]["dependencies"] = ["T2"]
    plan["tasks"][1]["description"] += " Add an external service."
    score = score_task_plan(story=CASE["story"], expected=CASE["expected"], task_plan=plan).to_dict()
    assert score["status"] == "FAIL"
    assert score["gates"]["dependency_graph_acyclic"] is False
    assert "external service" in score["forbidden_scope_hits"]


def test_score_task_generator_case_reads_yaml_artifact(tmp_path: Path):
    tasks_path = tmp_path / "tasks.yaml"
    tasks_path.write_text(yaml.safe_dump(good_plan(), sort_keys=False), encoding="utf-8")
    score = score_task_generator_case(CASE, tasks_path).to_dict()
    assert score["pass"] is True
    assert score["task_count"] == 2


def test_aggregate_scores_tracks_failed_checks():
    passed = score_task_plan(story=CASE["story"], expected=CASE["expected"], task_plan=good_plan()).to_dict()
    failed = dict(passed)
    failed["passed"] = False
    failed["pass"] = False
    failed["status"] = "FAIL"
    failed["gates"] = {"scope_clean": False}
    failed["issues"] = ["scope_violation: example"]
    summary = aggregate_scores([passed, failed])
    assert summary["reliability"]["pass_rate"] == 0.5
    assert summary["reliability"]["failed_checks"]["scope_clean"] == 1
    assert summary["reliability"]["failure_categories"]["scope_violation"] == 1
