#!/bin/bash
# PreToolUse (Bash): enforce WIP=1 on `bd update <id> --claim`.
# The harness controls the state transition, not the agent -- see
# harness-engineering's state-and-scope skill (Lecture 07). Only one
# bd issue may be in_progress at a time; close or unclaim the current
# one before claiming another.
set -u

input="$(cat)"
cmd="$(echo "$input" | jq -r '.tool_input.command // empty')"

[[ -z "$cmd" ]] && exit 0
echo "$cmd" | grep -qE 'bd (--[a-zA-Z-]+ +)*update .*--claim' || exit 0

in_progress="$(bd list --status=in_progress --json 2>/dev/null)"
count="$(echo "$in_progress" | jq 'length' 2>/dev/null || echo 0)"

if [[ "$count" -ge 1 ]]; then
  echo "$in_progress" | jq \
    '{hookSpecificOutput:{hookEventName:"PreToolUse", permissionDecision:"deny", permissionDecisionReason:("WIP=1 violation: issue(s) [" + ([.[].id] | join(" ")) + "] already in_progress. Close or unclaim before claiming another (AGENTS.md WIP=1 rule).")}}'
fi

exit 0
