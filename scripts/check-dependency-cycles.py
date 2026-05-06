import yaml
import sys
import json

def check_dependencies(tasks_file):
    try:
        with open(tasks_file, 'r') as f:
            data = yaml.safe_load(f)
    except Exception as e:
        return False, [str(e)]
    
    tasks = data.get('tasks', [])
    # Use id first, then task_id for compatibility
    adj = {task.get('id', task.get('task_id')): task.get('dependencies', []) for task in tasks}
    
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

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: check-dependency-cycles.py <file>"}))
        sys.exit(1)
        
    tasks_path = sys.argv[1]
    valid, issues = check_dependencies(tasks_path)
    print(json.dumps({
        "dependency_graph_valid": valid,
        "issues": issues
    }, indent=2))
    sys.exit(0 if valid else 1)
