#!/usr/bin/env bash
# postToolUse hook: sanitize artifact files after an agent writes them.
#
# Receives JSON on stdin describing the tool invocation. If the tool wrote
# a known artifact file (assignments.json, tasks.yaml, impl_report.yaml,
# qa_report.yaml, story.yaml), this script invokes the Python sanitizer to
# force the content into valid, parseable JSON/YAML.
#
# Exit 0 always — we never block the agent; we fix in place and log warnings.
set -euo pipefail

# Read the hook payload from stdin
PAYLOAD=$(cat)

# Extract the file path from the tool invocation.
# Copilot sends different shapes depending on the tool; we look for common keys.
FILE_PATH=$(echo "$PAYLOAD" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

# The tool result payload nests differently per tool type.
# For file-edit tools, the path is typically in toolInput.filePath or toolInput.path
tool_input = data.get('toolInput', {}) or {}
path = tool_input.get('filePath') or tool_input.get('path') or tool_input.get('file') or ''
print(path)
" 2>/dev/null || echo "")

# If we didn't extract a file path, nothing to do
if [ -z "$FILE_PATH" ]; then
    exit 0
fi

# Only act on known artifact filenames
BASENAME=$(basename "$FILE_PATH")
case "$BASENAME" in
    assignments.json|tasks.yaml|impl_report.yaml|qa_report.yaml|story.yaml)
        ;;
    *)
        exit 0
        ;;
esac

# Only act if file exists
if [ ! -f "$FILE_PATH" ]; then
    exit 0
fi

# Run the sanitizer
python3 "$(dirname "$0")/sanitize-artifact.py" "$FILE_PATH" 2>/dev/null || true

exit 0

