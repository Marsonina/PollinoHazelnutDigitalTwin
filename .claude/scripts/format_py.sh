#!/usr/bin/env bash
# PostToolUse hook: format the just-edited Python file with black + ruff.
set -euo pipefail

FILE=$(jq -r '.tool_input.file_path // empty' 2>/dev/null)

[[ -n "$FILE" && "$FILE" == *.py && -f "$FILE" ]] || exit 0

uv run black --target-version py312 "$FILE"
uv run ruff check --fix "$FILE"
