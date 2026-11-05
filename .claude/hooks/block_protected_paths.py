"""PreToolUse, cloud threads only: no edits to owner-only files (specs, acceptance/security tests, guardrails,
dependency and check-config files, see agent_guardrails.guarded) and no new check suppressions. Local sessions (the
spec writer, the owner) may. The Bash part is a heuristic over command-position tokens; CI's spec-guard is the
backstop. Wired with "onFailure": "block". Stdlib only, Python 3.9-safe.
"""

import importlib
import json
import os
import re
import sys
from pathlib import Path

from shell_parse import Command, inline_code, parse, program

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
guardrails = importlib.import_module("agent_guardrails")
GUARDED_PREFIXES = [
    g.lower() for g in (*guardrails.PROTECTED, *guardrails.GUARDRAIL_FILES)
]

WRITERS = {
    "cp",
    "mv",
    "install",
    "rsync",
    "ln",
    "tee",
    "touch",
    "truncate",
    "rm",
    "rmdir",
    "unlink",
    "shred",
    "mkdir",
    "chmod",
    "dd",
}
EDIT_IN_PLACE = {"sed": "-i", "perl": "-i"}
COPIERS = {"cp", "mv", "install", "rsync"}
ALWAYS_BLOCKED = {"patch", "unzip"}
CODE_WRITES = re.compile(
    r"write|unlink|rename|replace|rmtree|remove|copy|move|truncate|chmod|symlink|mkdir|open\(",
    re.IGNORECASE,
)
REASON = "specs/, tests/acceptance/, tests/security/, guardrail files (.claude/, .github/, .semgrep/, Makefile, CLAUDE.md, any conftest.py, pytest/ruff/mypy config) and dependency files belong to the owner. If one looks wrong, say exactly what and stop."


def relative(path: str, root: str) -> str:
    absolute = (
        os.path.normpath(os.path.join(root, path)) if root else os.path.normpath(path)
    )
    return (
        os.path.relpath(absolute, root)
        if root and absolute.startswith(root.rstrip("/") + "/")
        else path
    )


def touches(path: str, root: str, ancestors: bool) -> bool:
    """guarded(path); with ancestors, also a folder that contains guarded paths (`rm -r tests`, `git checkout x -- .`)."""
    rel = relative(path.rstrip("/") or "/", root)
    if rel.startswith("../") or os.path.isabs(rel):
        return False
    if ancestors and (
        rel in (".", "")
        or any(g.startswith(rel.lower() + "/") for g in GUARDED_PREFIXES)
    ):
        return True
    return bool(guardrails.guarded(rel))


def bash_reason(commands: "list[Command]", root: str) -> "str | None":
    inside_guarded = False
    for command in commands:
        name, args = program(command), command.words[1:]
        operands = [a for a in args if not a.startswith("-")]
        if name == "cd":
            inside_guarded = bool(operands) and touches(
                operands[0], root, ancestors=False
            )
            continue
        targets = list(command.redirects)
        if name == "dd":
            targets += [a[3:] for a in args if a.startswith("of=")]
        elif name in WRITERS:
            targets += operands
            if name in COPIERS and operands:
                targets += [
                    os.path.join(operands[-1], os.path.basename(o.rstrip("/")))
                    for o in operands[:-1]
                ]
        elif name in EDIT_IN_PLACE and EDIT_IN_PLACE[name] in " ".join(args):
            targets += operands
        if inside_guarded and (
            command.redirects or name in WRITERS or name in EDIT_IN_PLACE
        ):
            return REASON
        destructive = name in ("rm", "rmdir", "mv", "unlink", "shred")
        if any(touches(t, root, ancestors=destructive) for t in targets if t):
            return REASON
        if name == "git" and args:
            if args[0] in ("apply", "am"):
                return "git apply / git am can rewrite any file, including owner-only ones; they're blocked in cloud threads."
            paths = operands[1:] if args[0] in ("rm", "mv", "restore") else []
            if args[0] == "checkout":
                paths = args[args.index("--") + 1 :] if "--" in args else operands[2:]
            if any(touches(p, root, ancestors=True) for p in paths):
                return REASON
        if name in ALWAYS_BLOCKED or (
            name == "tar" and args and "x" in args[0].lstrip("-")
        ):
            return f"{name} can overwrite any file, including owner-only ones; it's blocked in cloud threads."
        code = inline_code(command)
        if code and CODE_WRITES.search(code):
            return "Inline code that writes files (python -c, node -e...) is blocked in cloud threads; edit files with the Edit/Write tools."
    return None


def added_suppressions(old: str, new: str) -> "list[str]":
    before = {line.strip() for line in old.splitlines()}
    return [
        line.strip()
        for line in new.splitlines()
        if guardrails.SUPPRESSION.search(line) and line.strip() not in before
    ]


if __name__ == "__main__":
    if os.environ.get("CLAUDE_CODE_REMOTE") != "true":
        sys.exit(0)
    event = json.load(sys.stdin)
    tool_input = event.get("tool_input", {})
    root = os.environ.get("CLAUDE_PROJECT_DIR", "")
    reason = None
    if event.get("tool_name") == "Bash":
        try:
            reason = bash_reason(parse(tool_input.get("command", "")), root)
        except ValueError:
            reason = "Couldn't parse this command (unbalanced quotes?), so it's blocked in cloud threads."
    else:
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        rel = relative(path, root)
        if touches(path, root, ancestors=False):
            reason = REASON
        elif not rel.endswith(".md"):
            edits = tool_input.get("edits") or [tool_input]
            new = "\n".join(
                e.get("new_string") or e.get("content") or e.get("new_source") or ""
                for e in edits
            )
            old = "\n".join(e.get("old_string") or "" for e in edits)
            if "content" in tool_input and os.path.isfile(path):
                old = Path(path).read_text(errors="ignore")
            found = added_suppressions(old, new)
            if found:
                reason = f"Adding check suppressions is blocked ({found[0][:80]}). Fix the finding; if you think it's a false positive, say so and stop."
    if reason:
        print(reason, file=sys.stderr)
        sys.exit(2)
