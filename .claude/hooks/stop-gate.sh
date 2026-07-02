#!/bin/bash
# Stop: lightweight verification gate (harness-engineering's
# verification-and-lifecycle skill, Lecture 09-10). Only fires the
# pytest/ruff check when Python source actually changed and the
# environment has been scaffolded (pyproject.toml exists) -- otherwise
# this is a no-op so it doesn't fight normal doc/planning turns.
set -u
cd "$(dirname "$0")/../.." || exit 0

input="$(cat)"
already_blocking="$(echo "$input" | jq -r '.stop_hook_active // false' 2>/dev/null)"
[[ "$already_blocking" == "true" ]] && exit 0

[[ ! -f pyproject.toml ]] && exit 0

changed_py="$(git status --porcelain -- '*.py' 2>/dev/null)"
[[ -z "$changed_py" ]] && exit 0

if command -v pytest >/dev/null 2>&1; then
  if ! pytest -q >/tmp/adhd-dash-stop-pytest.log 2>&1; then
    tail -n 40 /tmp/adhd-dash-stop-pytest.log | jq -Rs '{decision:"block", reason:("pytest is failing on changed Python files -- fix before stopping. Last output:\n" + .)}'
    exit 0
  fi
fi

if command -v ruff >/dev/null 2>&1; then
  if ! ruff check --quiet . >/tmp/adhd-dash-stop-ruff.log 2>&1; then
    tail -n 40 /tmp/adhd-dash-stop-ruff.log | jq -Rs '{decision:"block", reason:("ruff check is failing -- fix before stopping. Last output:\n" + .)}'
    exit 0
  fi
fi

exit 0
