"""Splits a Bash command into simple commands for the kit's hooks: line continuations joined, quotes resolved,
operators split, heredoc bodies dropped, `$(...)` / `<(...)` / here-string / `sh -c` / `eval` / `env -S` contents
parsed as commands of their own, wrappers (env, sudo, timeout, command...) and runners (uv run, uvx, npx, poetry run,
pdm run, find -exec, xargs) unwrapped to the command they run. A heuristic, not a shell: hooks treat what it can't
resolve as unsafe. Stdlib only, Python 3.9-safe.
"""

import os
import re
import shlex
from typing import NamedTuple

OPERATOR_CHARS = "();<>|&\n"
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish"}
KEYWORDS = {"do", "then", "else", "elif", "if", "while", "until", "!", "{", "}"}
WRAPPERS = {
    "command",
    "builtin",
    "exec",
    "nohup",
    "time",
    "sudo",
    "doas",
    "env",
    "nice",
    "timeout",
    "stdbuf",
    "xargs",
}
WRAPPER_VALUE_FLAGS = {"-u", "-g", "-n", "-s", "-k", "-C", "-p", "-o", "-e", "-i"}
XARGS_VALUE_FLAGS = {"-n", "-I", "-L", "-P", "-d", "-E", "-s", "-a", "-i"}
RUNNER_VALUE_FLAGS = {
    "--with",
    "--with-editable",
    "--with-requirements",
    "--from",
    "--python",
    "-p",
    "--directory",
    "--project",
    "--env-file",
    "--extra",
    "--group",
    "--package",
    "--index",
    "--index-url",
    "-c",
    "--call",
}
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
BRACE_EXPANSION = re.compile(r"\{[^{}]*,[^{}]*\}")
INLINE_CODE_FLAGS = {
    "python": {"-c"},
    "node": {"-e", "-p", "--eval", "--print"},
    "deno": {"eval"},
    "bun": {"-e", "--eval"},
    "ruby": {"-e"},
    "perl": {"-e", "-E"},
    "php": {"-r"},
}


class Command(NamedTuple):
    words: "list[str]"
    redirects: "list[str]"
    after: str
    opaque: bool
    env: "list[str]"
    stdin_fed: bool
    wrapped: "list[str]"


def program(command: Command) -> str:
    return os.path.basename(command.words[0]) if command.words else ""


def is_dynamic(word: str) -> bool:
    return "$" in word or "`" in word or bool(BRACE_EXPANSION.search(word))


def inline_code(command: Command) -> "str | None":
    name = re.sub(r"[\d.]+$", "", program(command))
    flags = INLINE_CODE_FLAGS.get(name, set())
    for i, word in enumerate(command.words[1:-1], start=1):
        if word in flags:
            return command.words[i + 1]
    return None


def strip_heredocs(text: str) -> str:
    out: list[str] = []
    lines = iter(text.split("\n"))
    for line in lines:
        out.append(line)
        for delimiter in re.findall(r"(?<!<)<<(?!<)-?[ \t]*['\"]?(\w+)['\"]?", line):
            for body in lines:
                if body.strip() == delimiter:
                    break
    return "\n".join(out)


OPERATOR = re.compile(r">>|<<|&&|\|\||;;|>&|<&|&>|\|&|[();<>|&\n]")


def tokens(text: str) -> "list[str]":
    lexer = shlex.shlex(
        strip_heredocs(text.replace("\\\n", "")),
        posix=True,
        punctuation_chars=OPERATOR_CHARS,
    )
    lexer.whitespace = " \t\r"
    lexer.commenters = ""
    lexer.whitespace_split = True
    return [
        piece
        for tok in lexer
        for piece in (
            OPERATOR.findall(tok) if set(tok) <= set(OPERATOR_CHARS) else [tok]
        )
    ]


def parse(text: str) -> "list[Command]":
    """Raises ValueError when the text can't be tokenised (e.g. an unclosed quote)."""
    return _commands(tokens(text))


def _closing(toks: "list[str]", start: int) -> int:
    depth = 0
    for j in range(start, len(toks)):
        depth += {"(": 1, ")": -1}.get(toks[j], 0)
        if depth == 0:
            return j
    raise ValueError("unbalanced parenthesis")


def _commands(toks: "list[str]") -> "list[Command]":
    out: list[Command] = []
    words: list[str] = []
    redirects: list[str] = []
    after = ""
    stdin_fed = False
    i = 0
    while i < len(toks):
        tok = toks[i]
        operator = set(tok) <= set(OPERATOR_CHARS)
        substitution = tok == "(" and (
            (words and words[-1].endswith("$")) or (i > 0 and toks[i - 1] in ("<", ">"))
        )
        if substitution:
            end = _closing(toks, i)
            inner = toks[i + 1 : end]
            out.extend(_commands(inner))
            text = "(" + " ".join(inner) + ")"
            if i > 0 and toks[i - 1] in ("<", ">"):
                if redirects and redirects[-1] == "":
                    redirects.pop()
                words.append(toks[i - 1] + text)
            else:
                words[-1] = words[-1] + text
            i = end + 1
            continue
        if tok == "<<" and i + 2 < len(toks) and toks[i + 1] == "<":
            out.extend(parse(toks[i + 2]))
            stdin_fed = True
            i += 3
            continue
        if operator and ("<" in tok or ">" in tok):
            if words and words[-1].isdigit():
                words.pop()
            target = (
                toks[i + 1]
                if i + 1 < len(toks) and not set(toks[i + 1]) <= set(OPERATOR_CHARS)
                else ""
            )
            if target and "<" not in tok:
                redirects.append(target)
            elif not target and i + 1 < len(toks) and toks[i + 1] == "(":
                redirects.append("")
            stdin_fed = stdin_fed or ("<" in tok and toks[i + 1 : i + 2] != ["("])
            i += 2 if target else 1
            continue
        if operator:
            out.extend(_finish(words, redirects, after, stdin_fed))
            words, redirects, stdin_fed = [], [], False
            after = tok
            i += 1
            continue
        words.append(tok)
        i += 1
    out.extend(_finish(words, redirects, after, stdin_fed))
    return out


def _unwrap(words: "list[str]") -> "tuple[list[str], list[str], list[str], list[str]]":
    """Strips assignments, keywords, wrappers and runners. Returns (words, env assignments, wrappers seen, extra command texts)."""
    env: list[str] = []
    wrapped: list[str] = []
    texts: list[str] = []
    while words:
        name = os.path.basename(words[0])
        if ASSIGNMENT.match(words[0]) or words[0] in KEYWORDS:
            if ASSIGNMENT.match(words[0]):
                env.append(words[0])
            words = words[1:]
        elif name in WRAPPERS:
            words = words[1:]
            wrapped.append(name)
            value_flags = XARGS_VALUE_FLAGS if name == "xargs" else WRAPPER_VALUE_FLAGS
            while words and (words[0].startswith("-") or ASSIGNMENT.match(words[0])):
                flag = words.pop(0)
                if ASSIGNMENT.match(flag):
                    env.append(flag)
                elif name == "env" and flag.startswith("-S"):
                    wrapped.append("env -S")
                    texts.append(flag[2:] or (words.pop(0) if words else ""))
                elif flag in value_flags and words:
                    words.pop(0)
            if name == "timeout" and words and re.match(r"^[\d.]+[smhd]?$", words[0]):
                words.pop(0)
        elif (name == "uv" and words[1:2] == ["run"]) or (
            name in ("poetry", "pdm", "pipx", "hatch") and words[1:2] == ["run"]
        ):
            wrapped.append(f"{name} run")
            words = _skip_flags(words[2:])
        elif name in ("uvx", "npx", "bunx") or (
            name == "pnpm" and words[1:2] == ["dlx"]
        ):
            wrapped.append(name)
            words = _skip_flags(words[2:] if name == "pnpm" else words[1:])
        else:
            break
    return words, env, wrapped, texts


def _skip_flags(words: "list[str]") -> "list[str]":
    while words and words[0].startswith("-"):
        flag = words.pop(0)
        if flag in RUNNER_VALUE_FLAGS and words:
            words.pop(0)
    return words


def _finish(
    words: "list[str]", redirects: "list[str]", after: str, stdin_fed: bool
) -> "list[Command]":
    words, env, wrapped, texts = _unwrap(list(words))
    if not words and not redirects and not texts:
        return []
    command = Command(
        words, redirects, after, "xargs" in wrapped, env, stdin_fed, wrapped
    )
    name = program(command)
    if name == "find":
        for flag in ("-exec", "-execdir", "-ok", "-okdir"):
            if flag in words:
                inner = words[words.index(flag) + 1 :]
                inner = inner[
                    : next(
                        (j for j, w in enumerate(inner) if w in (";", "+")), len(inner)
                    )
                ]
                texts.append(shlex.join(inner))
    if name in SHELLS and "-c" in words[1:]:
        index = words.index("-c")
        texts.append(words[index + 1] if index + 1 < len(words) else "")
    elif name == "eval":
        texts.append(" ".join(words[1:]))
    nested: list[Command] = []
    for text in texts + [inner for word in words for inner in _substitutions(word)]:
        try:
            nested += [c._replace(wrapped=c.wrapped + wrapped) for c in parse(text)]
        except ValueError:
            command = command._replace(opaque=True)
    return ([command] if words or redirects else []) + nested


def _substitutions(word: str) -> "list[str]":
    """Bodies of `$(...)` and backtick substitutions left inside a (double-quoted) word."""
    found = re.findall(r"`([^`]*)`", word)
    start = word.find("$(")
    while start != -1:
        depth, end = 0, start + 1
        for end in range(start + 1, len(word)):
            depth += {"(": 1, ")": -1}.get(word[end], 0)
            if depth == 0:
                break
        found.append(word[start + 2 : end])
        start = word.find("$(", end)
    return found
