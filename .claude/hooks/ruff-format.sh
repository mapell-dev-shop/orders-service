#!/usr/bin/env bash
# PostToolUse hook for Edit and Write: runs ruff format on the edited file.
# Reads the hook JSON on stdin. Skips anything that isn't a Python file.
set -euo pipefail

file=$(jq -r '.tool_input.file_path // empty')
case "$file" in
  *.py | *.pyi) ;;
  *) exit 0 ;;
esac
[[ -f "$file" ]] || exit 0

cd "${CLAUDE_PROJECT_DIR:-.}"
uv run ruff format --quiet "$file" >&2
