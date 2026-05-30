#!/usr/bin/env python3
"""PreToolUse hook: block writes that would commit secrets or session state.

Exit 2 blocks the tool call. Exit 0 allows it.
Triggers on: Write, Edit, NotebookEdit.
"""

import json
import re
import sys

# Paths that must never be written by Claude
BLOCKED_PATHS = [
    "storage_state.json",
    ".env",
]
BLOCKED_PATH_PREFIXES = [
    "secrets/",
]

# Patterns that indicate credential content
CREDENTIAL_PATTERNS = [
    r"PASSWORD\s*=\s*\S+",
    r"api_key\s*[=:]\s*\S+",
    r"api_secret\s*[=:]\s*\S+",
    r"access_token\s*[=:]\s*\S+",
    r"secret_key\s*[=:]\s*\S+",
    # Long base64 blobs typical of session cookies (≥60 chars of base64 chars)
    r"[A-Za-z0-9+/]{60,}={0,2}",
]

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

tool = data.get("tool_name", "")
if tool not in ("Write", "Edit", "NotebookEdit"):
    sys.exit(0)

inp = data.get("tool_input", {})
file_path = inp.get("file_path", "").replace("\\", "/")

# Normalise: strip leading ./ or absolute prefix down to relative
# so checks work regardless of how the path arrives
relative = file_path
for prefix in ("/Users", "/home", "./"):
    if relative.startswith(prefix):
        # keep only the filename / trailing portion for simple checks
        break

file_name = relative.split("/")[-1]

# --- Path checks ---
if file_name in BLOCKED_PATHS:
    print(
        f"SECRETS GUARD BLOCKED: refusing to write '{file_name}' "
        "(session state / env file).",
        file=sys.stderr,
    )
    sys.exit(2)

for prefix in BLOCKED_PATH_PREFIXES:
    if prefix in relative:
        print(
            f"SECRETS GUARD BLOCKED: path '{relative}' is inside a secrets directory.",
            file=sys.stderr,
        )
        sys.exit(2)

# --- Content checks ---
# Template/example files contain placeholder values — skip content scan.
SAFE_SUFFIXES = (".example", ".template")
if any(file_name.endswith(s) for s in SAFE_SUFFIXES):
    sys.exit(0)

if tool == "Write":
    content = inp.get("content", "")
elif tool in ("Edit", "NotebookEdit"):
    content = inp.get("new_string", "")
else:
    content = ""

for pattern in CREDENTIAL_PATTERNS:
    match = re.search(pattern, content)
    if match:
        snippet = match.group(0)[:60]
        print(
            f"SECRETS GUARD BLOCKED: credential-looking content detected "
            f"in '{file_name}': {snippet!r}",
            file=sys.stderr,
        )
        sys.exit(2)

sys.exit(0)
