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
    else:
        print(json.dumps({"error": "Unsupported type"}))
        sys.exit(1)
