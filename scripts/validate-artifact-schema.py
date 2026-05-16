import yaml
import sys
import json

def check_schema(tasks_file):
    try:
        with open(tasks_file, 'r') as f:
            data = yaml.safe_load(f)
    except Exception as e:
        return False, [str(e)]
    
    if not data or 'tasks' not in data:
        return False, ["Missing 'tasks' root element"]
        
    tasks = data.get('tasks', [])
    valid = True
    issues = []
    
    for task in tasks:
        # Use .get('id', 'unknown') to avoid KeyError
        task_id = task.get('id') or task.get('task_id', 'unknown')
        if 'task_id' in task:
            valid = False
            issues.append(f"Task {task_id} uses 'task_id' instead of 'id'")
        if 'acceptance_criteria_mapped' in task:
            valid = False
            issues.append(f"Task {task_id} uses 'acceptance_criteria_mapped' instead of 'ac_mapping'")
        if 'estimated_complexity' in task:
            valid = False
            issues.append(f"Task {task_id} uses 'estimated_complexity' instead of 'complexity'")
            
    return valid, issues

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(json.dumps({"error": "Usage: validate-artifact-schema.py --type tasks <file>"}))
        sys.exit(1)
        
    if sys.argv[1] == "--type" and sys.argv[2] == "tasks":
        # The actual file path is the 3rd argument
        if len(sys.argv) < 4:
            print(json.dumps({"error": "Missing file path"}))
            sys.exit(1)
        tasks_path = sys.argv[3]
        valid, issues = check_schema(tasks_path)
        print(json.dumps({
            "schema_valid": valid,
            "issues": issues
        }, indent=2))
        sys.exit(0 if valid else 1)
    elif sys.argv[1] == "--type" and sys.argv[2] == "impl_report":
        # Validate a generated impl_report.yaml (basic checks)
        if len(sys.argv) < 4:
            print(json.dumps({"error": "Missing file path"}))
            sys.exit(1)
        impl_path = sys.argv[3]
        try:
            with open(impl_path, 'r') as f:
                data = yaml.safe_load(f)
        except Exception as e:
            print(json.dumps({"schema_valid": False, "issues": [str(e)]}))
            sys.exit(1)
        issues = []
        if not isinstance(data, dict):
            issues.append("Top-level impl_report must be a mapping/object")
        else:
            for key in ['uow_id', 'status', 'implementation_summary']:
                if key not in data:
                    issues.append(f"Missing required key: {key}")
            dod = data.get('definition_of_done_status') or data.get('definition_of_done')
            if dod is None:
                issues.append("Missing 'definition_of_done_status' or 'definition_of_done'")
            else:
                if not isinstance(dod, list):
                    issues.append("'definition_of_done_status' should be a list (see Self-Evolved Rules)")
        valid = len(issues) == 0
        print(json.dumps({"schema_valid": valid, "issues": issues}, indent=2))
        sys.exit(0 if valid else 1)
    else:
        print(json.dumps({"error": "Unsupported type"}))
        sys.exit(1)

