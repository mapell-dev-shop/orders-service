#!/usr/bin/env bash
# WorktreeCreate hook: creates the worktree next to the service as
# ../orders-service-<name> instead of under .claude/worktrees/.
# Reads the hook JSON on stdin and prints the worktree's absolute path on stdout.
set -euo pipefail

name=$(jq -r '.name // empty')
if [[ -z "$name" ]]; then
  echo "worktree-create: no name in hook input" >&2
  exit 1
fi

repo=$(git -C "${CLAUDE_PROJECT_DIR:-.}" rev-parse --show-toplevel)
path="$(dirname "$repo")/$(basename "$repo")-$name"

# Everything except the final path goes to stderr; stdout is the hook's answer.
git -C "$repo" worktree add -b "worktree-$name" "$path" HEAD >&2

echo "$path"
