"""Spec <-> test <-> PR checks for the agent engineering procedure. Stdlib only, so it runs anywhere CI or a cloud thread does.

Commands:
  consistency                         every spec: ACs, work split, test map, test files and Security block agree
  pr --title T --base REF --head BR  a PR stays inside its lane and changes no guardrail, dependency or check-config file and adds no suppression (any branch); prints feature/part for CI
  tests --feature F [--part P]        list a feature's acceptance tests
  run --feature F [--part P]          run them (pytest for .py, Playwright for .js/.ts)

Stdlib only and Python 3.9-safe (hooks import it and may run under macOS /usr/bin/python3): `X | None` annotations are quoted.
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())
SPECS = ROOT / "specs"
PROTECTED = ("specs/", "tests/acceptance/")
GUARDRAIL_FILES = (
    ".git/",
    "scripts/agent_guardrails.py",
    "scripts/install_security_tools.sh",
    ".github/",
    ".claude/",
    ".semgrep/",
    "tests/security/",
    ".gitleaks.toml",
    ".gitleaksignore",
    ".semgrepignore",
    ".pre-commit-config.yaml",
    ".python-version",
)
GUARDED_NAMES = {
    "makefile",
    "claude.md",
    "conftest.py",
    "pytest.ini",
    "setup.cfg",
    "tox.ini",
    "ruff.toml",
    ".ruff.toml",
    "mypy.ini",
    ".mypy.ini",
    ".coveragerc",
    "sitecustomize.py",
    "usercustomize.py",
}
DEPENDENCY_FILES = {
    "pyproject.toml",
    "uv.lock",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
}
REQUIREMENTS = re.compile(r"requirements.*\.(txt|in)$")
SUPPRESSION = re.compile(
    r"#\s*noqa\b|ruff\s*:\s*noqa|\bnosem(grep)?\b|\bnosec\b|gitleaks:allow|type:\s*ignore|mypy:\s*ignore-errors|pyright:\s*ignore"
    r"|\bmark\.(skip|skipif|xfail)\b|pytest\.(skip|xfail|importorskip)\b|\bimportorskip\b|\bskip(If|Unless)\b|unittest\.skip"
    r"|\.(skip|fixme)\(|__test__\s*=\s*False|pytest_collection_modifyitems|pytest_ignore_collect|collect_ignore|--deselect"
    r"|eslint-disable|@ts-(ignore|nocheck|expect-error)",
    re.IGNORECASE,
)
TEST_CONFIG_FILES = {
    "pyproject.toml",
    "uv.lock",
    "package.json",
    "package-lock.json",
    "tests/acceptance/conftest.py",
}
CLOUD_BRANCH = "claude/"
TITLE_MARKER = re.compile(
    r"^\[(?P<feature>[a-z0-9-]+)/(?P<part>SPEC|REFACTOR|PART-[A-Z])\]|^\[no-spec\]"
)
PYTEST_CMD = os.environ.get("ACCEPTANCE_PYTEST_CMD", "uv run pytest")
PLAYWRIGHT_CMD = os.environ.get("ACCEPTANCE_PLAYWRIGHT_CMD", "npx playwright test")


def guarded(path: str) -> bool:
    """Owner-only file: spec/tests, guardrails, dependencies or anything that configures the checks. Case-insensitive."""
    rel = os.path.normpath(path).lower()
    name = os.path.basename(rel)
    return (
        any(
            rel == g.rstrip("/").lower() or rel.startswith(g.rstrip("/").lower() + "/")
            for g in (*PROTECTED, *GUARDRAIL_FILES)
        )
        or name in GUARDED_NAMES
        or name in DEPENDENCY_FILES
        or bool(REQUIREMENTS.search(name))
    )


def added_suppressions(base: str) -> list[str]:
    diff = subprocess.run(
        ["git", "diff", "-U0", "--no-renames", f"{base}...HEAD"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=True,
    )
    found: list[str] = []
    path = ""
    for line in diff.stdout.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else ""
        elif (
            line.startswith("+")
            and path
            and not path.endswith(".md")
            and SUPPRESSION.search(line)
        ):
            found.append(f"{path}: adds a check suppression: {line[1:].strip()}")
    return found


def table_rows(text: str, heading: str) -> list[list[str]]:
    section = re.search(
        rf"^## {heading}\s*$(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL
    )
    if not section:
        return []
    lines = [
        line for line in section.group(1).splitlines() if line.strip().startswith("|")
    ]
    return [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in lines[2:]
    ]


def backticked(cell: str) -> list[str]:
    return re.findall(r"`([^`]+)`", cell)


def owns(owned: list[str], path: str) -> bool:
    return any(path == o or path.startswith(o.rstrip("/") + "/") for o in owned)


class Spec:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.feature = path.stem
        text = path.read_text()
        status = re.search(r"^Status:\s*(\w+)", text, re.MULTILINE)
        self.status = status.group(1) if status else "missing"
        self.acs = [
            m.group(1)
            for m in re.finditer(
                r"^- \*\*AC-(\d+)\*\*(?!\s*—\s*\(removed)", text, re.MULTILINE
            )
        ]
        self.parts: dict[str, dict[str, list[str]]] = {}
        for row in table_rows(text, "Work split"):
            if len(row) >= 4 and row[0].startswith("PART-"):
                self.parts[row[0]] = {
                    "owns": backticked(row[2]),
                    "acs": re.findall(r"AC-(\d+)", row[3]),
                }
        self.security = [row for row in table_rows(text, "Security") if len(row) >= 3]
        self.tests: dict[str, tuple[str, str]] = {}
        for row in table_rows(text, "Test map"):
            if len(row) >= 3 and row[0].startswith("AC-"):
                test = (backticked(row[2]) or [row[2]])[0]
                self.tests[row[0][3:]] = (row[1], test)

    def test_file(self, ac: str) -> str:
        return re.split(r"::| ›", self.tests[ac][1])[0].strip()

    def tests_for(self, part: "str | None") -> list[str]:
        return [
            self.tests[ac][1]
            for ac in self.acs
            if ac in self.tests and (part is None or self.tests[ac][0] == part)
        ]

    def problems(self) -> list[str]:
        out: list[str] = []
        where = f"specs/{self.feature}.md"
        if self.status not in {"draft", "approved", "changed", "built"}:
            out.append(
                f"{where}: status '{self.status}' is not draft/approved/changed/built"
            )
        for ac in self.acs:
            owners = [p for p, d in self.parts.items() if ac in d["acs"]]
            if len(owners) != 1:
                out.append(
                    f"{where}: AC-{ac} belongs to {len(owners)} parts, needs exactly 1"
                )
            if ac not in self.tests:
                out.append(f"{where}: AC-{ac} has no test map row")
                continue
            if owners and self.tests[ac][0] != owners[0]:
                out.append(
                    f"{where}: AC-{ac} is {owners[0]}'s in the work split but {self.tests[ac][0]}'s in the test map"
                )
            if self.status == "draft":
                continue
            test_file = ROOT / self.test_file(ac)
            if not str(self.test_file(ac)).startswith(
                f"tests/acceptance/{self.feature}/"
            ):
                out.append(
                    f"{where}: AC-{ac} test must live in tests/acceptance/{self.feature}/"
                )
            if not test_file.exists():
                out.append(f"{where}: AC-{ac} test file {self.test_file(ac)} missing")
            elif f"ac_{ac}" not in test_file.read_text():
                out.append(f"{where}: AC-{ac} test file has no test named ac_{ac}")
        for area, _, test in (row[:3] for row in self.security):
            tested = (
                test.lower().startswith("n/a")
                or any(ac in self.acs for ac in re.findall(r"AC-(\d+)", test))
                or any(t.startswith("tests/security/") for t in backticked(test))
            )
            if not tested:
                out.append(
                    f"{where}: Security row '{area}' needs an existing AC-NN, a `tests/security/...` test or 'n/a — <why>'"
                )
        for ac in self.tests:
            if ac not in self.acs:
                out.append(f"{where}: test map row AC-{ac} has no matching AC")
        seen: dict[str, str] = {}
        for part, data in self.parts.items():
            for path in data["owns"]:
                if path.startswith(PROTECTED):
                    out.append(f"{where}: {part} owns protected path {path}")
                for other_path, other in seen.items():
                    if owns([other_path], path) or owns([path], other_path):
                        out.append(
                            f"{where}: {path} ({part}) overlaps {other_path} ({other})"
                        )
                seen[path] = part
        test_dir = ROOT / "tests" / "acceptance" / self.feature
        mapped = {self.test_file(ac) for ac in self.tests}
        if test_dir.exists():
            for f in test_dir.rglob("test_ac_*"):
                if "__pycache__" in f.parts or f.suffix not in {".py", ".js", ".ts"}:
                    continue
                rel = str(f.relative_to(ROOT))
                if rel not in mapped:
                    out.append(f"{rel}: acceptance test not in the test map of {where}")
        return out


def load(feature: str) -> Spec:
    path = SPECS / f"{feature}.md"
    if not path.exists():
        sys.exit(f"no spec at specs/{feature}.md")
    return Spec(path)


def cmd_consistency() -> int:
    problems = [p for path in sorted(SPECS.glob("*.md")) for p in Spec(path).problems()]
    for p in problems:
        print(f"FAIL {p}")
    print(
        "ok: specs, work splits and acceptance tests agree"
        if not problems
        else f"{len(problems)} problem(s)"
    )
    return 1 if problems else 0


def cmd_pr(title: str, base: str, head: str) -> int:
    marker = TITLE_MARKER.match(title)
    if not marker:
        print(
            "FAIL PR title must start with [<feature-slug>/PART-X], [<feature-slug>/SPEC], [<feature-slug>/REFACTOR] or [no-spec]"
        )
        return 1
    diff = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", f"{base}...HEAD"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=True,
    )
    changed = [f for f in diff.stdout.splitlines() if f]
    feature, part = marker.group("feature"), marker.group("part")
    from_cloud = head.startswith(CLOUD_BRANCH)
    problems: list[str] = []
    notes: list[str] = []
    for f in changed:
        if guarded(f) and not f.startswith(PROTECTED):
            spec_config = part == "SPEC" and f in TEST_CONFIG_FILES
            (notes if spec_config else problems).append(
                f"{f}: guardrail, dependency or check-config file changed; only the owner merges this, knowingly"
            )
    (notes if part == "SPEC" else problems).extend(added_suppressions(base))
    if part == "REFACTOR":
        if from_cloud:
            problems.append(f"REFACTOR PRs come from the owner's branch, not {head}")
        owned = [p for d in load(feature).parts.values() for p in d["owns"]]
        base_spec = subprocess.run(
            ["git", "show", f"{base}:specs/{feature}.md"],
            capture_output=True,
            text=True,
            cwd=ROOT,
            check=False,
        )
        if base_spec.returncode == 0:
            owned += [
                p
                for row in table_rows(base_spec.stdout, "Work split")
                if len(row) >= 3
                for p in backticked(row[2])
            ]
        for f in changed:
            allowed = (
                f == f"specs/{feature}.md"
                or f.startswith(f"tests/acceptance/{feature}/")
                or f in TEST_CONFIG_FILES
                or f == "README.md"
                or owns(owned, f)
                or f.startswith(GUARDRAIL_FILES)
            )
            if not allowed:
                problems.append(
                    f"{f}: a refactor PR may only change {feature}'s spec, tests, owned files and README.md"
                )
    elif part == "SPEC":
        if from_cloud:
            problems.append(f"SPEC PRs come from the owner's spec/ branch, not {head}")
        for f in changed:
            spec_file = f == f"specs/{feature}.md"
            own_tests = f.startswith(f"tests/acceptance/{feature}/")
            config = f in TEST_CONFIG_FILES or (
                "/" not in f and f.startswith("playwright.config")
            )
            if not (spec_file or own_tests or config):
                problems.append(
                    f"{f}: a spec PR may only change specs/{feature}.md, tests/acceptance/{feature}/ and root test config"
                )
    else:
        problems += [
            f"{f}: only spec PRs may change specs/ or tests/acceptance/"
            for f in changed
            if f.startswith(PROTECTED)
        ]
        if part:
            spec = load(feature)
            if spec.status != "approved":
                problems.append(
                    f"specs/{feature}.md is '{spec.status}', builds need 'approved'"
                )
            if part not in spec.parts:
                problems.append(
                    f"{part} is not in the work split of specs/{feature}.md"
                )
            else:
                owned = spec.parts[part]["owns"]
                problems += [
                    f"{f}: outside {part}'s files {owned}"
                    for f in changed
                    if not f.startswith(PROTECTED) and not owns(owned, f)
                ]
    for n in notes:
        print(f"NOTE owner check (SPEC PR): {n}")
    for p in problems:
        print(f"FAIL {p}")
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a") as fh:
            fh.write(
                f"feature={feature or ''}\npart={'ALL' if part == 'REFACTOR' else part if part and part != 'SPEC' else ''}\n"
            )
    print("ok: PR stays in its lane" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


def cmd_run(feature: str, part: "str | None") -> int:
    spec = load(feature)
    part = None if part == "ALL" else part
    if part and part not in spec.parts:
        print(f"FAIL {part} is not in the work split of specs/{feature}.md")
        return 1
    tests = spec.tests_for(part)
    py = [t for t in tests if t.split("::")[0].endswith(".py")]
    js = sorted(
        {
            t.split(" ›")[0].strip()
            for t in tests
            if not t.split("::")[0].endswith(".py")
        }
    )
    code = 0
    if py:
        cmd = [*PYTEST_CMD.split(), *py]
        code |= subprocess.run(cmd, cwd=ROOT, check=False).returncode  # nosemgrep
    if js:
        cmd = [*PLAYWRIGHT_CMD.split(), *js]
        code |= subprocess.run(cmd, cwd=ROOT, check=False).returncode  # nosemgrep
    if not tests:
        print(f"FAIL no acceptance tests found for {feature} {part or ''}")
        return 1
    return code


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("consistency")
    pr = sub.add_parser("pr")
    pr.add_argument("--title", required=True)
    pr.add_argument("--base", required=True)
    pr.add_argument("--head", required=True, help="PR head branch name")
    for name in ("tests", "run"):
        p = sub.add_parser(name)
        p.add_argument("--feature", required=True)
        p.add_argument("--part")
    args = parser.parse_args()
    if args.cmd == "consistency":
        return cmd_consistency()
    if args.cmd == "pr":
        return cmd_pr(args.title, args.base, args.head)
    if args.cmd == "tests":
        print("\n".join(load(args.feature).tests_for(args.part)))
        return 0
    return cmd_run(args.feature, args.part)


if __name__ == "__main__":
    sys.exit(main())
