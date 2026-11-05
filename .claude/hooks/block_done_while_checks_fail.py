"""Stop: a cloud thread can't finish while `make check` fails. After 3 blocks it must report instead of looping."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

MAX_BLOCKS = 3

if os.environ.get("CLAUDE_CODE_REMOTE") != "true":
    sys.exit(0)

event = json.load(sys.stdin)
root = Path(os.environ.get("CLAUDE_PROJECT_DIR", "."))
if not (root / "Makefile").exists():
    sys.exit(0)


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    ).stdout.strip()


state = (
    Path(tempfile.gettempdir()) / f"stop-hook-{event.get('session_id', 'unknown')}.json"
)
seen: dict[str, Any] = (
    json.loads(state.read_text()) if state.exists() else {"blocks": 0, "green": ""}
)
fingerprint = git("rev-parse", "HEAD") + git("status", "--porcelain")
if fingerprint == seen["green"]:
    sys.exit(0)

result = subprocess.run(
    ["make", "check"], cwd=root, capture_output=True, text=True, check=False
)
if result.returncode == 0:
    state.write_text(json.dumps({"blocks": 0, "green": fingerprint}))
    sys.exit(0)

if seen["blocks"] >= MAX_BLOCKS:
    state.unlink(missing_ok=True)
    sys.exit(0)
seen["blocks"] += 1
state.write_text(json.dumps(seen))
tail = "\n".join((result.stdout + result.stderr).splitlines()[-40:])
if seen["blocks"] == MAX_BLOCKS:
    reason = f"`make check` still fails after {MAX_BLOCKS} tries. Stop fixing: your final message must say you're NOT done and paste this failure.\n\n{tail}"
else:
    reason = f"`make check` fails, you're not done. Fix it, then finish.\n\n{tail}"
print(json.dumps({"decision": "block", "reason": reason}))
