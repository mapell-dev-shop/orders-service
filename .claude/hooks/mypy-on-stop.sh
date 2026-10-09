#!/usr/bin/env bash
# Stop hook: runs mypy over src when Claude finishes a turn.
# On errors it exits 2, which keeps Claude working and hands it mypy's output.
# Reads the hook JSON on stdin.
set -euo pipefail

# Already continuing because of this hook: let Claude stop rather than loop
# forever on errors it can't fix.
if [[ $(jq -r '.stop_hook_active // false') == "true" ]]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}"
if ! output=$(uv run mypy src 2>&1); then
  printf 'uv run mypy src failed. Fix these errors:\n%s\n' "$output" >&2
  exit 2
fi
