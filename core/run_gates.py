import yaml
import json
import sys
from .check_gates import check_schema, check_ac_coverage, check_dependencies

def run_gates(change_id):
    tasks_path = f"agent-context/{change_id}/planning/tasks.yaml"
    story_path = f"agent-context/{change_id}/intake/story.yaml"
    
    schema_valid, schema_issues = check_schema(tasks_path)
    ac_coverage, missing_acs = check_ac_coverage(story_path, tasks_path)
    dep_valid, dep_issues = check_dependencies(tasks_path)
    
    with open(tasks_path, 'r') as f:
        tasks_data = yaml.safe_load(f)
        task_count = len(tasks_data.get('tasks', []))
    
    task_count_valid = 2 <= task_count <= 15
    
    all_passed = schema_valid and ac_coverage and dep_valid and task_count_valid
    
    print(json.dumps({
        "schema_valid": schema_valid,
        "schema_issues": schema_issues,
        "ac_coverage_complete": ac_coverage,
        "missing_acs": missing_acs,
        "dependency_graph_valid": dep_valid,
        "dependency_issues": dep_issues,
        "task_count_in_range": task_count_valid,
        "task_count": task_count,
        "all_gates_passed": all_passed
    }, indent=2))

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python run_gates.py <change_id>")
        sys.exit(1)
    run_gates(sys.argv[1])
