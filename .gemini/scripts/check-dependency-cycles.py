#!/usr/bin/env python3
"""Validate dependency graphs for cycles using Kahn's algorithm (BFS topological sort).

Supports two artifact formats:
  - tasks   (YAML): tasks.yaml with tasks[].id / tasks[].dependencies
  - assignments (JSON): assignments.json with batches[].uows[].uow_id / dependencies
"""

import argparse
import json
import sys
from collections import deque
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # deferred error if actually needed


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse_tasks(data: dict) -> tuple[set[str], dict[str, list[str]]]:
    """Return (node_set, adjacency_dict) from a tasks YAML structure."""
    nodes: set[str] = set()
    adj: dict[str, list[str]] = {}
    for task in data.get("tasks", []):
        tid = str(task["id"])
        nodes.add(tid)
        adj[tid] = [str(d) for d in task.get("dependencies", [])]
    return nodes, adj


def parse_assignments(data: dict) -> tuple[set[str], dict[str, list[str]]]:
    """Return (node_set, adjacency_dict) from an assignments JSON structure."""
    nodes: set[str] = set()
    adj: dict[str, list[str]] = {}
    for batch in data.get("batches", []):
        for uow in batch.get("uows", []):
            uid = str(uow["uow_id"])
            nodes.add(uid)
            adj[uid] = [str(d) for d in uow.get("dependencies", [])]
    return nodes, adj


# ---------------------------------------------------------------------------
# Graph analysis
# ---------------------------------------------------------------------------

def find_dangling(nodes: set[str], adj: dict[str, list[str]]) -> list[dict]:
    """Return list of {node, missing_dep} for deps referencing non-existent IDs."""
    dangling: list[dict] = []
    for node, deps in adj.items():
        for dep in deps:
            if dep not in nodes:
                dangling.append({"node": node, "missing_dep": dep})
    return dangling


def kahns_algorithm(
    nodes: set[str], adj: dict[str, list[str]]
) -> tuple[bool, list[str], list[list[str]]]:
    """Run Kahn's BFS topological sort.

    Returns (has_cycles, topological_order, cycles).
    *cycles* contains simple-cycle representations extracted from remaining nodes.
    """
    # Build in-degree map and forward adjacency (dep -> dependant is the edge
    # direction for scheduling, but the natural reading of adj is
    # node -> [things it depends on], i.e. edges point *from* dependency *to*
    # dependent).  For Kahn's we need in-degree per node where an edge
    # dep -> node means "dep must come before node".
    in_degree: dict[str, int] = {n: 0 for n in nodes}
    forward: dict[str, list[str]] = {n: [] for n in nodes}

    for node, deps in adj.items():
        for dep in deps:
            if dep in nodes:  # skip dangling
                forward[dep].append(node)
                in_degree[node] += 1

    queue: deque[str] = deque(n for n, d in in_degree.items() if d == 0)
    order: list[str] = []

    while queue:
        cur = queue.popleft()
        order.append(cur)
        for neighbour in forward[cur]:
            in_degree[neighbour] -= 1
            if in_degree[neighbour] == 0:
                queue.append(neighbour)

    if len(order) == len(nodes):
        return False, order, []

    # Remaining nodes are involved in cycles – extract cycle paths.
    remaining = {n for n in nodes if n not in set(order)}
    cycles = _extract_cycles(remaining, forward)
    return True, order, cycles


def _extract_cycles(
    remaining: set[str], forward: dict[str, list[str]]
) -> list[list[str]]:
    """Extract simple cycles from the subgraph of remaining nodes via DFS."""
    visited: set[str] = set()
    cycles: list[list[str]] = []

    for start in sorted(remaining):
        if start in visited:
            continue
        stack: list[str] = []
        on_stack: set[str] = set()
        _dfs_cycle(start, remaining, forward, visited, stack, on_stack, cycles)

    return cycles


def _dfs_cycle(
    node: str,
    remaining: set[str],
    forward: dict[str, list[str]],
    visited: set[str],
    stack: list[str],
    on_stack: set[str],
    cycles: list[list[str]],
) -> None:
    visited.add(node)
    stack.append(node)
    on_stack.add(node)

    for neighbour in forward.get(node, []):
        if neighbour not in remaining:
            continue
        if neighbour in on_stack:
            # Found a cycle – extract from the repeated node onward.
            idx = stack.index(neighbour)
            cycles.append(stack[idx:] + [neighbour])
        elif neighbour not in visited:
            _dfs_cycle(neighbour, remaining, forward, visited, stack, on_stack, cycles)

    stack.pop()
    on_stack.discard(node)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def detect_type(filepath: str) -> str:
    ext = Path(filepath).suffix.lower()
    if ext in (".yaml", ".yml"):
        return "tasks"
    if ext == ".json":
        return "assignments"
    return ""


def load_artifact(filepath: str, artifact_type: str) -> tuple[set[str], dict[str, list[str]]]:
    with open(filepath, "r", encoding="utf-8") as fh:
        if artifact_type == "tasks":
            if yaml is None:
                print("Error: pyyaml is required for YAML files. Install with: pip install pyyaml", file=sys.stderr)
                sys.exit(2)
            data = yaml.safe_load(fh)
        else:
            data = json.load(fh)

    if artifact_type == "tasks":
        return parse_tasks(data)
    return parse_assignments(data)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check a dependency graph artifact for cycles."
    )
    parser.add_argument("artifact_file", help="Path to the artifact file (YAML or JSON).")
    parser.add_argument(
        "--type",
        choices=["tasks", "assignments"],
        default=None,
        dest="artifact_type",
        help="Artifact type. Auto-detected from extension if omitted.",
    )
    args = parser.parse_args()

    artifact_type = args.artifact_type or detect_type(args.artifact_file)
    if not artifact_type:
        print(
            f"Error: cannot auto-detect type for '{args.artifact_file}'. "
            "Use --type tasks|assignments.",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        nodes, adj = load_artifact(args.artifact_file, artifact_type)
    except (json.JSONDecodeError, Exception) as exc:
        print(f"Error: failed to parse artifact – {exc}", file=sys.stderr)
        sys.exit(2)

    dangling = find_dangling(nodes, adj)
    edge_count = sum(
        1 for deps in adj.values() for d in deps if d in nodes
    )
    has_cycles, topo_order, cycles = kahns_algorithm(nodes, adj)

    result = {
        "status": "fail" if has_cycles else "pass",
        "artifact_type": artifact_type,
        "nodes_count": len(nodes),
        "edges_count": edge_count,
        "has_cycles": has_cycles,
        "cycles": cycles,
        "dangling_dependencies": dangling,
        "topological_order": topo_order,
    }

    json.dump(result, sys.stdout, indent=2)
    print()  # trailing newline

    sys.exit(1 if has_cycles else 0)


if __name__ == "__main__":
    main()
