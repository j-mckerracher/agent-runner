import yaml
import sys
import json
from pathlib import Path

from core.yaml_safety import safe_load_yaml_file


def _load_tasks(tasks_file: str) -> dict:
    """Load tasks YAML with error handling. Returns {} on failure."""
    result = safe_load_yaml_file(Path(tasks_file))
    return result.data if result.is_valid else {}


def _load_story(story_file: str) -> dict:
    """Load story YAML with error handling. Returns {} on failure."""
    result = safe_load_yaml_file(Path(story_file))
    return result.data if result.is_valid else {}


def check_schema(tasks_file):
    data = _load_tasks(tasks_file)
    
    tasks = data.get('tasks', [])
    valid = True
    issues = []
    
    for task in tasks:
        if 'task_id' in task:
            valid = False
            issues.append(f"Task {task.get('task_id')} uses 'task_id' instead of 'id'")
        if 'acceptance_criteria_mapped' in task:
            valid = False
            issues.append(f"Task {task.get('task_id', 'unknown')} uses 'acceptance_criteria_mapped' instead of 'ac_mapping'")
        if 'estimated_complexity' in task:
            valid = False
            issues.append(f"Task {task.get('task_id', 'unknown')} uses 'estimated_complexity' instead of 'complexity'")
            
    return valid, issues

def check_ac_coverage(story_file, tasks_file):
    story = _load_story(story_file)
    tasks_data = _load_tasks(tasks_file)
    
    acs = story.get('acceptance_criteria', {})
    if isinstance(acs, list):
        ac_ids = [f"AC{i+1}" for i in range(len(acs))]
    else:
        ac_ids = list(acs.keys())
        
    covered_acs = set()
    for task in tasks_data.get('tasks', []):
        mapped = task.get('acceptance_criteria_mapped', []) or task.get('ac_mapping', [])
        covered_acs.update(mapped)
        
    missing = [ac for ac in ac_ids if ac not in covered_acs]
    return len(missing) == 0, missing

def check_dependencies(tasks_file):
    data = _load_tasks(tasks_file)
    
    tasks = data.get('tasks', [])
    adj = {task.get('task_id', task.get('id')): task.get('dependencies', []) for task in tasks}
    
    visited = set()
    rec_stack = set()
    
    def has_cycle(v):
        visited.add(v)
        rec_stack.add(v)
        for neighbor in adj.get(v, []):
            if neighbor not in visited:
                if has_cycle(neighbor):
                    return True
            elif neighbor in rec_stack:
                return True
        rec_stack.remove(v)
        return False

    for node in adj:
        if node not in visited:
            if has_cycle(node):
                return False, ["Cycle detected"]
    return True, []

def main():
    change_id = "calibration_story_001_easy_attempt_01-RUN-03"
    tasks_path = f"agent-context/{change_id}/planning/tasks.yaml"
    story_path = f"agent-context/{change_id}/intake/story.yaml"
    
    schema_valid, schema_issues = check_schema(tasks_path)
    ac_coverage, missing_acs = check_ac_coverage(story_path, tasks_path)
    dep_valid, dep_issues = check_dependencies(tasks_path)
    
    tasks_data = _load_tasks(tasks_path)
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
    main()
