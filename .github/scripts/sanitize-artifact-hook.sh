#!/usr/bin/env bash
# postToolUse / AfterTool hook: sanitize artifact files written by agents.
#
# Works for Claude Code, GitHub Copilot, and Gemini CLI. Each runner sends a
# different JSON payload on stdin; we extract the file path from any of the
# known shapes and call the Python sanitizer.
#
# Claude Code  — tool_input.file_path  (Write / Edit / MultiEdit)
# Gemini CLI   — tool_input.path  OR  tool_input.file_path  (write_file / replace_in_file)
# Copilot      — toolInput.filePath  OR  toolInput.path  (covered by sanitize-artifact.sh)
#
# Exit 0 always — never block the agent.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SANITIZER="$REPO_ROOT/.github/scripts/sanitize-artifact.py"

# Read stdin once into a variable
PAYLOAD=$(cat)

# Extract file path via Python: try all known field paths across runners
FILE_PATH=$(echo "$PAYLOAD" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
# Normalize: flatten nested tool_input / toolInput blocks
ti = data.get('tool_input') or data.get('toolInput') or data
# Try all known path field names
path = (ti.get('file_path')
     or ti.get('filePath')
     or ti.get('path')
     or ti.get('file')
     or '')
print(path)
" 2>/dev/null || echo "")

if [ -z "$FILE_PATH" ]; then
    exit 0
fi

# Resolve relative paths against repo root
if [[ "$FILE_PATH" != /* ]]; then
    FILE_PATH="$REPO_ROOT/$FILE_PATH"
fi

BASENAME=$(basename "$FILE_PATH")
case "$BASENAME" in
    assignments.json|tasks.yaml|impl_report.yaml|qa_report.yaml|story.yaml)
        ;;
    *)
        exit 0
        ;;
esac

[ -f "$FILE_PATH" ] || exit 0

python3 "$SANITIZER" "$FILE_PATH" 2>/dev/null || true

exit 0

