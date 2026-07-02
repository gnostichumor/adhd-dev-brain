#!/bin/bash
# PostToolUse (Edit|Write): auto-fix lint/format on the file just touched.
# No-ops gracefully until the environment scaffolding task lands (no
# pyproject.toml / ruff yet) -- see AGENTS.md "Build & Test".
set -u

input="$(cat)"
file_path="$(echo "$input" | jq -r '.tool_input.file_path // empty')"

[[ -z "$file_path" ]] && exit 0
[[ "$file_path" != *.py ]] && exit 0
[[ ! -f "$file_path" ]] && exit 0

if ! command -v ruff >/dev/null 2>&1; then
  exit 0
fi

ruff check --fix --quiet "$file_path" >/dev/null 2>&1
ruff format --quiet "$file_path" >/dev/null 2>&1

remaining="$(ruff check --quiet "$file_path" 2>&1)"
if [[ -n "$remaining" ]]; then
  printf '%s' "$remaining" | jq -Rs --arg f "$file_path" \
    '{hookSpecificOutput:{hookEventName:"PostToolUse", decision:"block", reason:("ruff still reports issues in " + $f + " after autofix:\n" + .)}}'
fi

exit 0
