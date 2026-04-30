#!/usr/bin/env bash
# eval/checks.sh — story-specific verification checks for eval fixtures
# Usage:
#   bash eval/checks.sh /path/to/mcs-products-mono-ui [CHANGE_ID] [STORY_FILE]
# Output: JSON with check results + final score to stdout; exits 0 on success

set -euo pipefail

MONO_ROOT="${1:?Usage: checks.sh <path-to-mcs-products-mono-ui> [CHANGE_ID] [STORY_FILE]}"
CHANGE_ID="${2:-}"
STORY_FILE="${3:-}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

ARGS=("$SCRIPT_DIR/story_checks.py" "--mono-root" "$MONO_ROOT")
if [[ -n "$CHANGE_ID" ]]; then
    ARGS+=("--change-id" "$CHANGE_ID")
fi
if [[ -n "$STORY_FILE" ]]; then
    ARGS+=("--story-file" "$STORY_FILE")
fi

python3 "${ARGS[@]}"
