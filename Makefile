# Targets the guardrails need. Adapt the commands to the repo; keep the target names.
# check      = lint + types + security scans + unit tests (NOT acceptance tests; the Stop hook and CI run it)
# test       = unit tests only (tests/security/ included)
# test-setup = install everything the tests need, in CI and cloud threads alike
# security   = the scans below; semgrep/zizmor run pinned through uvx, gitleaks/osv-scanner are binaries
#              (local: brew install gitleaks osv-scanner; CI and cloud: scripts/install_security_tools.sh)

SEMGREP := uvx semgrep@1.180.0
ZIZMOR := uvx zizmor@1.30.1
export PATH := $(HOME)/.local/bin:$(PATH)
# CI runs the base branch's copy of this file with -f; recursion must stay on that copy.
SELF := $(MAKE) -f $(firstword $(MAKEFILE_LIST))
need = @command -v $(1) >/dev/null || { echo "MISSING $(1): brew install $(1) (CI and cloud threads: bash scripts/install_security_tools.sh)"; exit 1; }

.PHONY: check test test-setup fix security semgrep-test

check:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy .
	$(SELF) security
	$(SELF) test

security:
	uv sync --locked
	uv run ruff check --select S,G,LOG,TRY401 --ignore S603,S607 --per-file-ignores "tests/**:S101" --per-file-ignores "tests/**:S105" --per-file-ignores "tests/**:S106" .
	@collected=$$(uv run pytest --ignore=tests/acceptance --collect-only -q -p no:cacheprovider 2>/dev/null | grep '^tests/security/'); \
	  missing=$$(printf '%s\n' "$$collected" | grep -Fvx -f - tests/security/EXPECTED_TESTS); \
	  [ -z "$$missing" ] || { echo "security tests not collected in the full run (deselected by pytest config or a conftest.py):"; echo "$$missing"; exit 1; }
	$(call need,gitleaks)
	gitleaks git --no-banner --redact .
	gitleaks git --no-banner --redact --staged .
	gitleaks git --no-banner --redact --pre-commit .
	$(SEMGREP) scan --error --metrics=off --quiet --config .semgrep/ --exclude .semgrep/fixtures .
	$(ZIZMOR) --offline .github/workflows
# Cloud threads' Trusted network can't reach semgrep.dev or api.osv.dev; CI runs these on every PR.
ifneq ($(CLAUDE_CODE_REMOTE),true)
	$(SEMGREP) scan --error --metrics=off --quiet --config p/python --config p/javascript --config p/secrets --exclude .semgrep/fixtures .
	$(call need,osv-scanner)
	osv-scanner scan source --recursive .
endif

semgrep-test:
	@for f in .semgrep/fixtures/*; do n=$$(basename $$f); $(SEMGREP) --test --metrics=off --config .semgrep/$${n%.*}.yml $$f || exit 1; done

test:
	uv run pytest --ignore=tests/acceptance -q

test-setup:
	uv sync
	@if [ -f package.json ]; then npm ci && npx playwright install --with-deps chromium; fi

fix:
	uv run ruff check --fix .
	uv run ruff format .
