# support-inbox-triage-agent

AI triage for a small business's shared support inbox. Every new email gets classified and labelled in Gmail. Customer questions get a reply **draft** for a human to check and send. Invoices, unsure emails and prompt-injection attempts are left unread for a person. Each run writes a report with the evidence behind every decision.

The business is fictional: **Lumenfold Candle Co.**, a 3-person webshop for handmade candles. All 50 demo emails, people and addresses are invented (`*.example` domains).

## What it replaces
Each morning someone reads the shared inbox, sorts it, answers the routine questions and flags what needs the owner. This agent does the sorting and the first draft. The human only reviews the drafts and the flagged emails.

## How it decides

| Classified as | Gmail labels | Read? | Draft? |
| --- | --- | --- | --- |
| support / sales, confident | `Triage/Support` or `Triage/Sales` + `Triage/Draft ready` | stays unread | yes, threaded reply draft |
| invoice | `Triage/Invoice` | stays unread | no |
| spam / other | `Triage/Spam` / `Triage/Other` | marked read | no |
| confidence < 0.70, or a draft that failed the checks | category + `Triage/Needs review` | stays unread | no |
| model refusal or invalid model output | `Triage/Needs review` only | stays unread | no |
| prompt injection suspected | category + `Triage/Suspicious` | stays unread | no |
| API or Gmail error | none (retried next run) | stays unread | no |

Every outcome in the run report carries: category, confidence, one-line reason, an exact quote from the email, and why it was flagged.

## Safety

- **The code has no send path.** No send, reply, forward, trash or delete call exists in `src/`, and a test enforces it. Gmail has no "drafts only" OAuth scope: the `gmail.modify` scope used here could technically send. So the guarantee comes from the code, not from the token.
- **Prompt-injection guard.** Email text is untrusted data:
  - Every field is fenced in one `<email>` block with `<`/`>` escaped. It never goes into the system prompt.
  - The model has no tools, and its output must validate against a Pydantic schema.
  - Code, not the model, picks every Gmail action from a closed set of labels.
  - A code-side pattern scan plus the model's own verdict decide "suspicious". Either one blocks the draft.
  - A draft may only contain links that appear in the FAQ or the customer's email.
- **Failure isolation.** One bad email never stops the batch. Anthropic calls use `max_retries=3` and a 60 s timeout; Gmail calls use `num_retries=3`. Failed emails stay untouched and are retried on the next run. Already-triaged emails are skipped.
- **Logs** hold only ids, labels, counts and error class names. They never hold bodies, senders, subjects or provider error text.

## Run it

```bash
uv sync
export ANTHROPIC_API_KEY=...                 # never committed
uv run support-inbox-triage run --source mock    # offline: the 50 mock emails in data/
uv run support-inbox-triage eval                 # same run, scored against data/gold.json
```

Each run writes `runs/<timestamp>/report.html`, `report.md` and `report.json`. `eval` also writes `eval.json`.

### Gmail (demo inbox)
1. Create a Desktop OAuth client. Put it at `~/.config/portfolio-demos/client_secret.json`, or set `TRIAGE_GMAIL_CLIENT_SECRET_PATH`. The token is saved to `~/.config/portfolio-demos/token-support-inbox.json` (`TRIAGE_GMAIL_TOKEN_PATH`).
2. `export TRIAGE_DEMO_INBOX=<demo address>`. Gmail commands refuse to run without it, and also when the signed-in account is a different one.
3. `uv run support-inbox-triage seed` inserts the 50 mock emails (Gmail `messages.insert`, nothing is sent).
4. `uv run support-inbox-triage run --source gmail`

Example schedule (not installed): `0 8 * * 1-5 cd /path/to/repo && uv run support-inbox-triage run --source gmail`

## Measured
The gold labels in `data/gold.json` are the intended labels for the 50 mock emails, checked blind by a second reviewer. The eval reports:
- category accuracy (an ambiguous email counts as correct when it is flagged for review)
- injection recall
- violations: any draft written for an invoice or an injection attempt

Result: *pending: filled in from the first real eval run.*

## Develop

```bash
make check   # ruff, mypy, security scans (gitleaks, semgrep, osv-scanner, zizmor), unit + security tests
make test
uv run pytest tests/acceptance   # the spec's acceptance tests (specs/support-inbox-triage.md)
```

Layout:
- `src/pipeline.py`: routing and reports
- `src/classifier.py`: classifier + injection pre-scan
- `src/drafter.py`: reply drafts
- `src/gmail.py`: Gmail mailbox, OAuth, seeding
- `src/mailbox.py`: interface + local fake mailbox
- `src/eval.py`: scoring
- `src/cli.py`: command line
- `data/`: the mock emails, gold labels and the shop FAQ the drafts draw on
