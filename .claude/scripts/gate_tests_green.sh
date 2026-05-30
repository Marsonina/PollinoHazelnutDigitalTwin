#!/usr/bin/env bash
# Stop hook: block Claude from ending the turn if the unit suite is red.
# Exit 2 keeps Claude working so it can fix the breakage before stopping.
# All output goes to stderr — that is what Claude Code feeds back on Stop hooks.
set -euo pipefail

# Nothing to gate against yet if the unit test directory doesn't exist.
shopt -s nullglob
test_files=(tests/unit/test_*.py)
[[ ${#test_files[@]} -gt 0 ]] || exit 0

echo "Running unit test gate before stopping..." >&2
uv run pytest tests/unit -q 2>&1 >&2 || {
    echo "" >&2
    echo "GATE FAILED: unit tests are red. Fix the failures before finishing." >&2
    exit 2
}
