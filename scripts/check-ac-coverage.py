import yaml
import sys
import json

def check_ac_coverage(story_file, tasks_file):
    try:
        with open(story_file, 'r') as f:
            story = yaml.safe_load(f)
        with open(tasks_file, 'r') as f:
            tasks_data = yaml.safe_load(f)
    except Exception as e:
        return False, [str(e)]
    
    acs = story.get('acceptance_criteria', {})
    if isinstance(acs, list):
        ac_ids = [f"AC{i+1}" for i in range(len(acs))]
    else:
        ac_ids = list(acs.keys())
        
    covered_acs = set()
    for task in tasks_data.get('tasks', []):
        mapped = task.get('ac_mapping', []) or task.get('acceptance_criteria_mapped', [])
        covered_acs.update(mapped)
        
    missing = [ac for ac in ac_ids if ac not in covered_acs]
    return len(missing) == 0, missing

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(json.dumps({"error": "Usage: check-ac-coverage.py <story_file> <tasks_file>"}))
        sys.exit(1)
        
    story_path = sys.argv[1]
    tasks_path = sys.argv[2]
    valid, missing = check_ac_coverage(story_path, tasks_path)
    print(json.dumps({
        "ac_coverage_complete": valid,
        "missing_acs": missing
    }, indent=2))
    sys.exit(0 if valid else 1)
