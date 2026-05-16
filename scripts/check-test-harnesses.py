#!/usr/bin/env python3
import os
import sys
import json
import argparse

parser = argparse.ArgumentParser(description='Check for Cypress component test harnesses and component tests')
parser.add_argument('--repo', default='.', help='Path to repository root to scan')
args = parser.parse_args()

root = os.path.abspath(args.repo)
matches = []
csproj_found = False
for dirpath, dirnames, filenames in os.walk(root):
    for fname in filenames:
        if fname.endswith('.component.test-harness.ts') or fname.endswith('.test-harness.ts') or fname.endswith('.component.cy.ts') or fname.endswith('.cy.ts'):
            matches.append(os.path.join(dirpath, fname))
        if fname.endswith('.csproj'):
            csproj_found = True

result = {
    'repo_type': 'dotnet' if csproj_found else 'js',
    'skip_cypress_gate': csproj_found,
    'test_harnesses_present': bool(matches),
    'matches': matches
}
print(json.dumps(result, indent=2))
# exit 0 when found or when repo is dotnet (skip gate), 2 when none found and not dotnet
if matches or csproj_found:
    sys.exit(0)
else:
    sys.exit(2)
