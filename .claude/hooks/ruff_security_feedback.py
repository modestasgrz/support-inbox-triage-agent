"""PostToolUse Edit/Write on *.py: run ruff's security rules on that file and hand any findings back to Claude. Local and cloud."""

import json
import os
import subprocess
import sys

RUFF_SECURITY = [
    "--select",
    "S,G,LOG,TRY401",
    "--ignore",
    "S603,S607",
    "--per-file-ignores",
    "tests/**:S101",
    "--force-exclude",
    "--output-format",
    "concise",
]

event = json.load(sys.stdin)
tool_input = event.get("tool_input", {})
path = tool_input.get("file_path") or ""
if not path.endswith(".py") or not os.path.exists(path):
    sys.exit(0)

result = subprocess.run(
    ["uv", "run", "--no-sync", "ruff", "check", *RUFF_SECURITY, path],
    cwd=os.environ.get("CLAUDE_PROJECT_DIR", "."),
    capture_output=True,
    text=True,
    check=False,
)
if result.returncode == 1:
    print(
        json.dumps(
            {
                "decision": "block",
                "reason": f"ruff security rules flag {path}. Fix these now; `make check` and CI run the same rules.\n{result.stdout.strip()}",
            }
        )
    )
