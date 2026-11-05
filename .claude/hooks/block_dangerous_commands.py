"""PreToolUse Bash: blocks destructive and remote-code commands everywhere; in cloud threads also blocks dependency
changes and running unpinned packages. Looks only at commands in command position (shell_parse), so quoted text
like a commit message or a grep pattern never trips it. Heuristic: a determined agent can hide a command (e.g. in a
script file); CI and review are the backstop. Wired with "onFailure": "block". Stdlib only, Python 3.9-safe.
"""

import json
import os
import re
import sys

from shell_parse import SHELLS, Command, inline_code, parse, program

INTERPRETERS = SHELLS | {"python", "python3", "node", "perl", "ruby", "php"}
DOWNLOADERS = {"curl", "wget"}
PINNED = re.compile(r"@\d|==\d")


def flags(words: list[str]) -> "tuple[set[str], set[str]]":
    short = {
        c for w in words if w.startswith("-") and not w.startswith("--") for c in w[1:]
    }
    long = {w.split("=")[0] for w in words if w.startswith("--")}
    return short, long


def destructive(command: Command, previous: "Command | None") -> "str | None":
    name, args = program(command), command.words[1:]
    short, long = flags(args)
    if (
        name == "rm"
        and ({"r", "R"} & short or "--recursive" in long)
        and ("f" in short or "--force" in long)
    ):
        return "rm -rf is blocked for agents. Delete specific files without -rf, or ask the owner to run it."
    if (
        name == "find"
        and ("-delete" in args or "rm" in args)
        and args
        and args[0] in ("/", "~", "$HOME", "..")
    ):
        return "Deleting with find outside the repo is blocked."
    if name == "git" and args:
        sub, rest = args[0], args[1:]
        if sub == "push" and (
            "f" in short
            or any(w.startswith("--force") for w in long)
            or any(w.startswith("+") for w in rest)
        ):
            return "Force-push is blocked. Push a new commit instead."
        if sub == "reset" and "--hard" in long:
            return "git reset --hard throws away work. Use git stash or a new commit, or ask the owner."
        if sub == "clean" and ("f" in short or "--force" in long):
            return "git clean -f deletes untracked files. Remove specific files, or ask the owner."
        if (
            sub in ("checkout", "restore")
            and "--staged" not in long
            and any(p in (".", ":/", "*") for p in rest)
        ):
            return f"git {sub} . throws away all uncommitted changes. Name the files, or ask the owner."
    if any(re.match(r"^[<$]\((curl|wget)\b", w) for w in args) or (
        name in INTERPRETERS
        and previous is not None
        and command.after == "|"
        and program(previous) in DOWNLOADERS
    ):
        return "Running downloaded code is blocked. Download, read, then ask the owner."
    if (
        name in SHELLS
        and command.after == "|"
        and not [w for w in args if not w.startswith("-")]
    ):
        return "Piping text into a shell hides what runs; it's blocked. Run the commands directly."
    if name == "claude" and (
        {"--dangerously-skip-permissions", "--allow-dangerously-skip-permissions"}
        & long
        or "--permission-mode=bypassPermissions" in args
        or (
            "--permission-mode" in args
            and args[args.index("--permission-mode") + 1 :][:1] == ["bypassPermissions"]
        )
    ):
        return "Starting Claude Code with permission checks off is blocked."
    return None


def changes_dependencies(command: Command) -> bool:
    name, args = program(command), command.words[1:]
    positional = [w for w in args if not w.startswith("-")]
    head = positional[:2]
    if name in ("pip", "pip3") and head[:1] == ["install"]:
        return True
    if re.match(r"^python[\d.]*$", name) and args[:3] == ["-m", "pip", "install"]:
        return True
    if name == "uv":
        if head[:1] in (["add"], ["remove"]) or head in (
            ["pip", "install"],
            ["pip", "sync"],
            ["tool", "install"],
        ):
            return True
        if head[:1] in (["sync"], ["lock"]) and any(
            w in ("-U", "--upgrade") or w.startswith("--upgrade-package") for w in args
        ):
            return True
    if name in ("poetry", "pipx") and head[:1] in (["add"], ["install"], ["run"]):
        return True
    if name in ("npm", "pnpm", "yarn", "bun") and head[:1] in (
        ["add"],
        ["install"],
        ["i"],
        ["update"],
        ["upgrade"],
    ):
        return len(positional) > 1 or head[:1] in (["update"], ["upgrade"])
    if {"uvx", "npx", "bunx", "pnpm"} & set(command.wrapped):
        return not (PINNED.search(name) or name == "playwright")
    return False


def reason_for(command_text: str, remote: bool) -> "str | None":
    try:
        commands = parse(command_text)
    except ValueError:
        return (
            "Couldn't parse this command (unbalanced quotes?), so it's blocked in cloud threads."
            if remote
            else None
        )
    previous: Command | None = None
    for command in commands:
        reason = destructive(command, previous)
        if reason:
            return reason
        if remote and changes_dependencies(command):
            return "Adding, upgrading or running unpinned packages is blocked in cloud threads. Dependencies come from the lockfile (`make test-setup`). If you need one, stop and ask the owner."
        code = inline_code(command)
        if code and re.search(r"\$\(\s*(curl|wget)\b", code):
            return "Running downloaded code is blocked. Download, read, then ask the owner."
        previous = command
    return None


if __name__ == "__main__":
    event = json.load(sys.stdin)
    if event.get("tool_name") == "Bash":
        reason = reason_for(
            event.get("tool_input", {}).get("command", ""),
            os.environ.get("CLAUDE_CODE_REMOTE") == "true",
        )
        if reason:
            print(reason, file=sys.stderr)
            sys.exit(2)
