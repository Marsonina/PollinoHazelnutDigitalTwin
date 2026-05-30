#!/usr/bin/env bash
# PostToolUse hook: run the unit suite when an agronomy core file is edited.
# Failing output goes back to Claude so it can self-correct before finishing.
set -euo pipefail

FILE=$(jq -r '.tool_input.file_path // empty' 2>/dev/null)

[[ -n "$FILE" ]] || exit 0
[[ "$FILE" == *"src/pollino/agronomy/"* ]] || exit 0

echo "Agronomy core changed — running unit tests..."
uv run pytest tests/unit -q
