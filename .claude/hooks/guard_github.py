"""PreToolUse Bash + mcp__*: how agents touch git, GitHub and the network.

Cloud threads (CLAUDE_CODE_REMOTE=true), allowlist: `git push -u origin <current claude/* branch>` only; gh read
commands, `gh pr create|comment`, `gh api` GET and POST to .../pulls or .../issues/N/comments only; no curl/wget/ssh,
no inline code that talks to the network; GitHub-mutating MCP tools denied. Anything that doesn't parse into an
allowed form is denied.

Local sessions, merge gate: an agent's `gh pr merge` / `gh api -X PUT .../pulls/N/merge` goes through only for an
open, non-draft PR that changes no owner-only file, whose checks are all pass/skipping with the core checks passed,
pinned to the PR head with --match-head-commit / -f sha=. Covers merges an agent runs through gh in a Claude session
only; GitHub web-UI merges stay on the owner.

Heuristic over command-position tokens (shell_parse); fails closed. Stdlib only, Python 3.9-safe.
"""

import filecmp
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import NamedTuple

from shell_parse import SHELLS, Command, inline_code, is_dynamic, parse, program

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
guardrails = importlib.import_module("agent_guardrails")

CORE_CHECKS = ("checks", "spec-guard", "secrets-history")
PART_TITLE = re.compile(r"\[[a-z0-9-]+/PART-[A-Z]\]")
OK_BUCKETS = {"pass", "skipping"}
TIMEOUT = 20
GH_TOP = {
    "auth",
    "browse",
    "codespace",
    "discussion",
    "gist",
    "issue",
    "org",
    "pr",
    "project",
    "release",
    "repo",
    "skill",
    "cache",
    "run",
    "workflow",
    "co",
    "agent-task",
    "alias",
    "api",
    "attestation",
    "completion",
    "config",
    "copilot",
    "extension",
    "gpg-key",
    "label",
    "licenses",
    "preview",
    "ruleset",
    "search",
    "secret",
    "ssh-key",
    "status",
    "variable",
    "help",
    "version",
}
GH_CLOUD_ALLOWED = {
    ("pr", "create"),
    ("pr", "comment"),
    ("pr", "view"),
    ("pr", "checks"),
    ("pr", "diff"),
    ("pr", "list"),
    ("pr", "status"),
    ("issue", "view"),
    ("issue", "list"),
    ("run", "list"),
    ("run", "view"),
    ("run", "watch"),
    ("workflow", "list"),
    ("workflow", "view"),
    ("repo", "view"),
    ("release", "list"),
    ("release", "view"),
    ("label", "list"),
    ("auth", "status"),
}
GH_VALUE_FLAGS = {
    "-R",
    "--repo",
    "-X",
    "--method",
    "-f",
    "--raw-field",
    "-F",
    "--field",
    "-H",
    "--header",
    "-q",
    "--jq",
    "-t",
    "--template",
    "--input",
    "--cache",
    "-p",
    "--preview",
    "--hostname",
    "-b",
    "--body",
    "--body-file",
    "--title",
    "-B",
    "--base",
    "--head",
    "-a",
    "--assignee",
    "-l",
    "--label",
    "-m",
    "--milestone",
    "-r",
    "--reviewer",
    "-A",
    "--author-email",
    "--subject",
    "--match-head-commit",
    "-s",
    "--state",
    "-L",
    "--limit",
    "--json",
    "-S",
    "--search",
    "-e",
    "--exit-status",
}
GH_DATA_FLAGS = {
    "-f",
    "--raw-field",
    "-F",
    "--field",
    "-b",
    "--body",
    "--body-file",
    "-t",
    "--title",
    "--subject",
    "-q",
    "--jq",
    "--template",
}
FIELD_FLAGS = ("-f", "--raw-field", "-F", "--field", "--input")
PUSH_FLAGS = {
    "-u",
    "--set-upstream",
    "-q",
    "--quiet",
    "-v",
    "--verbose",
    "--progress",
    "--porcelain",
    "--no-verify",
}
NETWORK_TOOLS = {
    "curl",
    "wget",
    "nc",
    "ncat",
    "netcat",
    "socat",
    "ssh",
    "scp",
    "sftp",
    "telnet",
    "ftp",
    "http",
    "https",
    "xh",
    "aria2c",
}
NETWORK_CODE = re.compile(
    r"https?:|urllib|requests|httpx|http\.client|socket|fetch\(|net/http|github|GH_TOKEN|GITHUB_TOKEN",
    re.IGNORECASE,
)
MCP_READ_ONLY = re.compile(r"^(get|list|search|read)_", re.IGNORECASE)
INTERPRETERS = SHELLS | {"python", "python3", "node", "perl", "ruby", "php"}
GIT_SUBCOMMANDS = {
    "add",
    "am",
    "apply",
    "archive",
    "bisect",
    "blame",
    "branch",
    "bundle",
    "cat-file",
    "check-ignore",
    "checkout",
    "cherry",
    "cherry-pick",
    "clean",
    "clone",
    "commit",
    "config",
    "describe",
    "diff",
    "difftool",
    "fetch",
    "for-each-ref",
    "format-patch",
    "fsck",
    "gc",
    "grep",
    "help",
    "init",
    "log",
    "ls-files",
    "ls-remote",
    "ls-tree",
    "merge",
    "merge-base",
    "mv",
    "name-rev",
    "notes",
    "pull",
    "push",
    "range-diff",
    "rebase",
    "reflog",
    "remote",
    "reset",
    "restore",
    "rev-list",
    "rev-parse",
    "revert",
    "rm",
    "shortlog",
    "show",
    "show-ref",
    "sparse-checkout",
    "stash",
    "status",
    "submodule",
    "switch",
    "tag",
    "version",
    "whatchanged",
    "worktree",
}
SWITCHING = {
    "checkout",
    "switch",
    "reset",
    "branch",
    "rebase",
    "worktree",
    "update-ref",
    "symbolic-ref",
}
API_MERGE = re.compile(r"^(?:repos/([^/]+/[^/]+)/)?pulls/(\d+)/merge$")
API_ALLOWED_POST = re.compile(r"^repos/[^/]+/[^/]+/(pulls|issues/\d+/comments)$")


class Denied(Exception):
    pass


class Gh(NamedTuple):
    positionals: "list[str]"
    options: "dict[str, list[str]]"
    dynamic: bool


class Target(NamedTuple):
    via_api: bool
    selector: "list[str]"
    repo: str
    head: str
    words: "list[str]"


def parse_gh(words: "list[str]") -> Gh:
    positionals: list[str] = []
    options: dict[str, list[str]] = {}
    dynamic = False
    i = 1
    while i < len(words):
        word = words[i]
        name, value = word, None
        if word.startswith("--") and "=" in word:
            name, value = word.split("=", 1)
        elif word in GH_VALUE_FLAGS:
            value = words[i + 1] if i + 1 < len(words) else ""
            i += 1
        elif re.match(r"^-[XfFHRq].+", word):
            name, value = word[:2], word[2:]
        if value is not None:
            options.setdefault(name, []).append(value)
            dynamic = dynamic or (is_dynamic(value) and name not in GH_DATA_FLAGS)
        elif not word.startswith("-"):
            positionals.append(word)
            dynamic = dynamic or is_dynamic(word)
        i += 1
    return Gh(positionals, options, dynamic)


def api_request(gh: Gh) -> "tuple[str, str]":
    path = gh.positionals[1] if len(gh.positionals) > 1 else ""
    explicit = (gh.options.get("-X") or []) + (gh.options.get("--method") or [])
    method = (
        explicit[-1].upper()
        if explicit
        else ("POST" if any(f in gh.options for f in FIELD_FLAGS) else "GET")
    )
    path = urllib.parse.unquote(path).split("?")[0]
    path = re.sub(r"/+", "/", path).strip("/")
    return method, path


def run_gh(args: "list[str]", ok_codes: "tuple[int, ...]" = (0,)) -> str:
    try:
        result = subprocess.run(
            ["gh", *args],
            cwd=os.environ.get("CLAUDE_PROJECT_DIR") or None,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Denied(f"`gh {' '.join(args)}` failed: {type(error).__name__}") from error
    if result.returncode not in ok_codes:
        raise Denied(f"`gh {' '.join(args)}` failed: {result.stderr.strip()[:200]}")
    return result.stdout


def current_branch() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=os.environ.get("CLAUDE_PROJECT_DIR") or None,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def is_copy_of(path: str, tool: str) -> bool:
    """path is git/gh under another name: a symlink to it or a byte copy of it."""
    real = shutil.which(tool)
    if not os.path.isfile(path):
        return False
    if os.path.basename(os.path.realpath(path)) == tool:
        return True
    return (
        bool(real)
        and os.path.getsize(path) == os.path.getsize(str(real))
        and filecmp.cmp(path, str(real), shallow=False)
    )


def cloud_git(args: "list[str]", switched: bool) -> None:
    root = os.path.normpath(os.environ.get("CLAUDE_PROJECT_DIR", "."))
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] == "-C":
            target = args[i + 1] if i + 1 < len(args) else ""
            if os.path.normpath(os.path.join(root, target)) != root:
                raise Denied("git -C to another directory is blocked in cloud threads")
            i += 1
        elif args[i] in ("-c", "--git-dir", "--work-tree", "--exec-path") or args[
            i
        ].startswith(("--git-dir=", "--work-tree=", "--exec-path=", "-c")):
            raise Denied(f"git {args[i]} is blocked in cloud threads")
        i += 1
    sub, rest = (args[i], args[i + 1 :]) if i < len(args) else ("", [])
    if sub and not is_dynamic(sub) and sub not in GIT_SUBCOMMANDS:
        raise Denied(
            f"`git {sub}` (plumbing, alias or unusual command) is blocked in cloud threads"
        )
    positional = [w for w in rest if not w.startswith("-")]
    if is_dynamic(sub) or (
        sub in ("push", "remote", "config") and any(is_dynamic(w) for w in rest)
    ):
        raise Denied(
            "git command with a shell variable or substitution; write it out literally"
        )
    if (
        sub == "remote"
        and positional[:1]
        and positional[0] not in ("-v", "show", "get-url")
    ):
        raise Denied("changing git remotes is blocked in cloud threads")
    if sub == "config" and (
        len(positional) > 1
        or any(
            w in ("--add", "--unset", "--unset-all", "--replace-all", "--edit", "-e")
            for w in rest
        )
    ):
        raise Denied("writing git config is blocked in cloud threads")
    if sub == "push":
        branch = current_branch()
        bad_flags = [w for w in rest if w.startswith("-") and w not in PUSH_FLAGS]
        own = positional == ["origin", branch] or (
            positional in ([], ["origin", "HEAD"]) and not switched
        )
        if bad_flags or not own or not branch.startswith("claude/"):
            raise Denied(
                f"cloud threads push only their own branch: `git push -u origin {branch or '<current claude/* branch>'}`"
            )


def cloud_gh(gh: Gh) -> None:
    if gh.dynamic:
        raise Denied(
            "gh command with a shell variable or substitution outside a body/field value; write it out literally"
        )
    if any(f in gh.options for f in ("-R", "--repo", "--hostname")):
        raise Denied("gh -R/--repo/--hostname is blocked in cloud threads")
    top = gh.positionals[:1]
    if top == ["api"]:
        method, path = api_request(gh)
        headers = " ".join(gh.options.get("-H", []) + gh.options.get("--header", []))
        if "method" in headers.lower():
            raise Denied("HTTP method headers (X-HTTP-Method...) are blocked")
        if (method == "GET" and path and path != "graphql") or (
            method == "POST" and API_ALLOWED_POST.match(path)
        ):
            return
        raise Denied(
            f"gh api {method} {path} is blocked in cloud threads (allowed: GET, POST .../pulls, POST .../issues/N/comments)"
        )
    if tuple(gh.positionals[:2]) not in GH_CLOUD_ALLOWED and top != ["search"]:
        raise Denied(f"`gh {' '.join(gh.positionals[:2])}` is blocked in cloud threads")


def script_run(command: Command) -> str:
    """The script file a shell or `source` runs, if any."""
    name, args = program(command), command.words[1:]
    if name in ("source", ".") and args:
        return args[0]
    if name in SHELLS and "-c" not in args:
        return next((a for a in args if not a.startswith("-")), "")
    return ""


def cloud_reason(commands: "list[Command]") -> None:
    root = os.path.normpath(os.environ.get("CLAUDE_PROJECT_DIR", "."))
    written: set[str] = set()
    switched = False
    for command in commands:
        name = program(command)
        if any(e.startswith(("GIT_", "GH_")) for e in command.env) or (
            name == "export"
            and any(w.startswith(("GIT_", "GH_")) for w in command.words[1:])
        ):
            raise Denied(
                "GIT_* / GH_* environment variables are blocked in cloud threads"
            )
        if "env -S" in command.wrapped:
            raise Denied("env -S is blocked in cloud threads; run the command directly")
        operands = [w for w in command.words[1:] if not w.startswith("-")]
        if (
            re.sub(r"[\d.]+$", "", name) in INTERPRETERS
            and inline_code(command) is None
            and "-c" not in command.words
            and (command.stdin_fed or command.after == "|")
            and (not operands or operands[0] == "-")
        ):
            raise Denied(
                "feeding code to a shell or interpreter through stdin is blocked in cloud threads"
            )
        if name.startswith("git-") or "/git-core/" in (command.words or [""])[0]:
            raise Denied(
                "git plumbing binaries are blocked in cloud threads; use `git <command>`"
            )
        if (
            command.words
            and "/" in command.words[0]
            and any(is_copy_of(command.words[0], t) for t in ("git", "gh"))
        ):
            raise Denied("run git / gh by name, not a copy or link of it")
        if name in ("cp", "ln", "install", "rsync") and any(
            os.path.basename(o) in ("git", "gh") for o in operands
        ):
            raise Denied("copying or linking git / gh is blocked in cloud threads")
        written.update(
            os.path.normpath(os.path.join(root, t)) for t in command.redirects
        )
        script = script_run(command)
        if script:
            path = os.path.normpath(os.path.join(root, script))
            if not path.startswith(root + "/") or path in written:
                raise Denied(
                    "running a script from outside the repo or one written in the same command is blocked in cloud threads"
                )
        if not name:
            continue
        if is_dynamic(command.words[0]):
            raise Denied("can't tell which program this runs; write the command out")
        if name in NETWORK_TOOLS:
            raise Denied(
                f"{name} is blocked in cloud threads; the only network path is gh / git push"
            )
        code = inline_code(command)
        if code is not None and NETWORK_CODE.search(code):
            raise Denied(
                "inline code that talks to the network or starts processes is blocked in cloud threads"
            )
        if name in ("git", "gh") and command.opaque:
            raise Denied(
                f"{name} behind xargs or an unparseable string: its arguments aren't visible"
            )
        if name == "git":
            cloud_git(command.words[1:], switched)
            switched = switched or bool(set(command.words[1:3]) & SWITCHING)
        elif name == "gh":
            cloud_gh(parse_gh(command.words))


def merge_targets(commands: "list[Command]", text: str) -> "list[Target]":
    targets: list[Target] = []
    if re.search(r"pr\s+merge|/merge\b", text) and any(script_run(c) for c in commands):
        raise Denied(
            "a merge mentioned next to a script run; run `gh pr merge N` directly"
        )
    if "merge" in text and any(
        (program(c) in SHELLS and (c.stdin_fed or c.after == "|"))
        or (
            program(c) in ("cp", "ln", "install")
            and any(os.path.basename(w) == "gh" for w in c.words[1:])
        )
        for c in commands
    ):
        raise Denied(
            "a merge through a shell's stdin or a copy of gh; run `gh pr merge N` directly"
        )
    for command in commands:
        name, words = program(command), command.words
        if words and "/" in words[0] and is_copy_of(words[0], "gh"):
            name = "gh"
        if words and is_dynamic(words[0]) and any("merge" in w for w in words):
            raise Denied(
                "can't tell which program runs this merge; use a literal `gh pr merge N`"
            )
        if name != "gh":
            if "merge" in " ".join(words) and "github.com" in " ".join(words):
                raise Denied(
                    "merge PRs only with `gh pr merge N --match-head-commit <sha>`"
                )
            continue
        if command.opaque:
            raise Denied(
                "gh behind xargs or an unparseable string: the PR isn't visible"
            )
        gh = parse_gh(words)
        top, sub = (gh.positionals + ["", ""])[:2]
        if top and top not in GH_TOP:
            raise Denied(
                f"`gh {top}` looks like an alias or extension, which can hide a merge; spell out the full gh command"
            )
        if top == "alias" and any("merge" in w for w in words):
            raise Denied(
                "gh aliases that merge are blocked; run `gh pr merge` directly"
            )
        repo = (gh.options.get("-R") or gh.options.get("--repo") or [""])[-1]
        if top == "pr" and (is_dynamic(sub) or sub == "merge"):
            selector = gh.positionals[2:3]
            if (
                gh.dynamic
                or any(is_dynamic(w) for w in selector)
                or "GH_REPO" in text
                or "GH_HOST" in text
            ):
                raise Denied(
                    "can't tell which PR this merges; use a literal PR number (and -R, not GH_REPO)"
                )
            targets.append(
                Target(
                    False,
                    selector,
                    repo,
                    (gh.options.get("--match-head-commit") or [""])[-1],
                    words,
                )
            )
        elif top == "api":
            method, path = api_request(gh)
            if gh.dynamic or not path:
                raise Denied(
                    "gh api with a shell variable in the path or method; write it out literally"
                )
            match = API_MERGE.match(
                path.replace("{owner}/{repo}", "").replace("repos//", "")
            )
            fields = (
                gh.options.get("-f", [])
                + gh.options.get("-F", [])
                + gh.options.get("--field", [])
                + gh.options.get("--raw-field", [])
            )
            if match and method == "PUT":
                head = next(
                    (f.split("=", 1)[1] for f in fields if f.startswith("sha=")), ""
                )
                targets.append(
                    Target(True, [match.group(2)], match.group(1) or repo, head, words)
                )
            elif method != "GET" and (
                re.search(r"merges$|git/refs|pulls/\d+/merge", path)
                or (path == "graphql" and "merge" in " ".join(fields).lower())
            ):
                raise Denied(
                    f"gh api {method} {path} merges or moves branches outside the merge gate; use `gh pr merge N`"
                )
    return targets


def gate(target: Target) -> None:
    repo_flag = ["--repo", target.repo] if target.repo else []
    try:
        pr = json.loads(
            run_gh(
                [
                    "pr",
                    "view",
                    *target.selector,
                    *repo_flag,
                    "--json",
                    "number,isDraft,state,headRefOid,title",
                ]
            )
        )
        number = str(pr["number"])
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise Denied(
            f"can't read the PR {' '.join(target.selector) or '(current branch)'}"
        ) from error
    if pr["state"] != "OPEN":
        raise Denied(f"PR #{number} is {pr['state']}, not OPEN")
    if pr["isDraft"]:
        raise Denied(
            f"PR #{number} is a draft; the owner marks it ready (`gh pr ready {number}`) first"
        )
    repo_path = target.repo or "{owner}/{repo}"
    files = run_gh(
        [
            "api",
            f"repos/{repo_path}/pulls/{number}/files",
            "--paginate",
            "--jq",
            ".[].filename",
        ]
    ).split()
    owner_only = [f for f in files if guardrails.guarded(f)]
    if owner_only:
        raise Denied(
            f"PR #{number} changes owner-only files ({', '.join(owner_only[:5])}); the owner reviews and merges it in the GitHub UI"
        )
    try:
        checks = json.loads(
            run_gh(
                ["pr", "checks", number, *repo_flag, "--json", "name,bucket,state"],
                ok_codes=(0, 1, 8),
            )
            or "[]"
        )
    except json.JSONDecodeError:
        checks = []
    if not checks:
        raise Denied(
            f"PR #{number} has no checks yet (CI registers ~20-60 s after a push); wait and retry"
        )
    bad = [
        f"{c['name']}={c['bucket']}" for c in checks if c["bucket"] not in OK_BUCKETS
    ]
    required = list(CORE_CHECKS) + (
        ["acceptance"] if PART_TITLE.search(pr["title"]) else []
    )
    passed = {c["name"] for c in checks if c["bucket"] == "pass"}
    missing = [name for name in required if name not in passed]
    if bad or missing:
        raise Denied(
            f"PR #{number} isn't green: "
            + ", ".join(bad + [f"{m} not passed" for m in missing])
            + ". Fix it, or if the base moved since CI ran, `gh run rerun <run-id>`."
        )
    if target.head != pr["headRefOid"]:
        if target.via_api:
            exact = f"gh api -X PUT repos/{repo_path}/pulls/{number}/merge -f sha={pr['headRefOid']}"
        else:
            kept = [
                w
                for w in target.words[3:]
                if w not in (*target.selector, target.head, "--match-head-commit")
            ]
            exact = " ".join(
                ["gh pr merge", number, *kept, "--match-head-commit", pr["headRefOid"]]
            )
        raise Denied(
            f"pin the merge to the commit that was checked. Run exactly: {exact}"
        )


def reason_for(tool_name: str, command_text: str, remote: bool) -> "str | None":
    try:
        if tool_name.startswith("mcp__"):
            if remote and not MCP_READ_ONLY.match(tool_name.split("__")[-1]):
                return f"{tool_name}: cloud threads may use only read-only MCP tools (get_/list_/search_/read_)."
            return None
        try:
            commands = parse(command_text)
        except ValueError as error:
            if remote or "merge" in command_text:
                raise Denied(
                    "couldn't parse this command (unbalanced quotes?)"
                ) from error
            return None
        if remote:
            cloud_reason(commands)
            return None
        for target in merge_targets(commands, command_text):
            gate(target)
    except Denied as denied:
        return f"Blocked: {denied}"
    return None


if __name__ == "__main__":
    event = json.load(sys.stdin)
    reason = reason_for(
        event.get("tool_name", ""),
        event.get("tool_input", {}).get("command", ""),
        os.environ.get("CLAUDE_CODE_REMOTE") == "true",
    )
    if reason:
        print(reason, file=sys.stderr)
        sys.exit(2)
