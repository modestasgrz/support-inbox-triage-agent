# support-inbox-triage-agent

AI triage for a small business's shared support inbox. Every new email gets classified and labelled in Gmail. Customer questions get a reply **draft** for a human to check and send. Invoices, unsure emails and prompt-injection attempts are left unread for a person. Each run writes a report that opens with a morning summary (what is urgent, what to answer by hand, scam warnings, invoices, drafts to review) and then shows every email with the evidence behind the decision and the full draft text.

The business is fictional: **Lumenfold Candle Co.**, a 3-person webshop for handmade candles. All 50 demo emails, people and addresses are invented (`*.example` domains).

## What it replaces
Each morning someone reads the shared inbox, sorts it, answers the routine questions and flags what needs the owner. This agent does the sorting and the first draft. The human reads the 7-line summary, reviews the drafts and handles the flagged emails.

## How it decides

| Classified as | Gmail labels | Read? | Draft? |
| --- | --- | --- | --- |
| support / sales, confident | `Triage/Support` or `Triage/Sales` + `Triage/Draft ready` | stays unread | yes, threaded reply draft |
| invoice | `Triage/Invoice` | stays unread | no |
| spam / other | `Triage/Spam` / `Triage/Other` | marked read | no |
| other with a date (invitation, pickup, event) | `Triage/Other` | stays unread, listed under "Has a date" | no |
| confidence < 0.70, or a draft that failed the checks | category + `Triage/Needs review` | stays unread | no |
| model refusal or invalid model output | `Triage/Needs review` only | stays unread | no |
| prompt injection suspected | category + `Triage/Suspicious` | stays unread | no |
| …and it is a real customer question (support / sales) | category + `Triage/Suspicious` + `Triage/Answer by hand` | stays unread | no: a person answers it, ignoring the planted text |
| model API error, or Gmail error before labelling | none (retried next run) | stays unread | no |
| Gmail error while creating the draft | category + `Triage/Draft ready` + `Triage/Needs review`, marked failed in the report | stays unread | no |

On top of that, `Triage/Urgent` is added when:
- a customer threatens a chargeback, a bank dispute, legal action or a bad public review (the model returns a yes/no plus the exact quote; the code adds the label), or
- an invoice is overdue (the model reads the due date; the code compares it with the run date).

Every outcome in the run report carries: category, confidence, one-line reason, an exact quote from the email, why it was flagged, and the full draft text when a draft was made.

### The report
`report.html` / `report.md` open with a morning summary, for example (from the offline eval run below):

```
Urgent (2): lf-013 "URGENT!!!! WHERE IS MY ORDER" (escalation: "IF I DONT HEAR BACK TODAY I'M DISPUTING WITH MY BANK."); lf-028 "Payment reminder: invoice GJ-55231 overdue" (invoice overdue since 2026-10-30)
Answer by hand (3): lf-047 "Refund for order 10402"; lf-049 "Fwd: my order question"; lf-050 "Damaged diffuser" — real customer questions with planted instructions, no draft written
SCAM WARNING (2): lf-049 "Fwd: my order question" → refunds-portal[.]example; lf-050 "Damaged diffuser" → lumenfold-refunds[.]example — never send these to customers
Invoices (4): EUR 3,265.95 total, 1 overdue
Has a date (2): lf-043 "Pickup confirmed for tomorrow" on 2026-11-06; lf-045 "Invitation: Craft fair stall planning (Nov 8)" on 2026-11-08 — left unread
Drafts ready to review: 25
Rest: 9 spam, 6 other marked read, 1 need review, 0 failed, 0 skipped
```

The scam warning is decided in code: a suspicious email whose links point to hosts that are not in the FAQ, or that asks for bank/card details. Its domains are written defanged (`[.]`), never as links.

## Safety

- **The code has no send path.** No send, reply, forward, trash or delete call exists in `src/`, and a test enforces it. Gmail has no "drafts only" OAuth scope: the `gmail.modify` scope used here could technically send. So the guarantee comes from the code, not from the token.
- **Prompt-injection guard.** Email text is untrusted data:
  - Every field is fenced in one block whose tag name is random and new on every message (`<email-3f9a…>`), with `<`/`>` escaped, so a fake closing tag stays plain text. Email text never goes into the system prompt.
  - The model has no tools, and its output must validate against a Pydantic schema.
  - Code, not the model, picks every Gmail action from a closed set of labels.
  - A code-side pattern scan plus the model's own verdict decide "suspicious". Either one blocks the draft.
  - A draft may only contain links that appear in the FAQ or the customer's email.
  - The urgency and date fields from the model can only add a label, keep an email unread or fill the report. They can never create a draft or mark a suspicious email read.
  - The reports contain no clickable links: the HTML has no `<a>` tags and the Markdown defangs `://` and `www.`.
- **Failure isolation.** One bad email never stops the batch. Anthropic calls use `max_retries=3` and a 60 s timeout; Gmail calls use `num_retries=3`. An email that failed before it was labelled stays untouched and is retried on the next run. Labels go on before the draft is created, so a rerun can never draft the same email twice. Already-triaged emails are skipped and listed in the report, and so are messages Gmail couldn't return.
- **Logs** hold only ids, labels, counts and error class names. They never hold bodies, senders, subjects or provider error text.

## Run it yourself

Needs [uv](https://docs.astral.sh/uv/) and an Anthropic API key.

```bash
uv sync
export ANTHROPIC_API_KEY=...                     # never committed
uv run support-inbox-triage run --source mock    # offline: the 50 mock emails in data/, ~3 min
uv run support-inbox-triage eval                 # same run, scored against data/gold.json
open runs/<timestamp>/report.html                # macOS; any browser works
```

Each run writes `runs/<timestamp>/report.html`, `report.md` and `report.json`; `eval` also writes `eval.json`. The offline run uses a local fake mailbox, so the drafts live only in the report (full text, next to each email).

### Gmail demo inbox
Use a throwaway Gmail account: the seed step fills its inbox with the 50 mock emails.
1. In Google Cloud, enable the Gmail API and create a Desktop OAuth client. Put its JSON at `~/.config/portfolio-demos/client_secret.json` (or set `TRIAGE_GMAIL_CLIENT_SECRET_PATH`). The first run opens a browser for the "allow" click; the token is saved to `~/.config/portfolio-demos/token-support-inbox.json` (`TRIAGE_GMAIL_TOKEN_PATH`).
2. `export TRIAGE_DEMO_INBOX=<demo address>`. Gmail commands refuse to run without it, and also when the signed-in account is a different one.
3. `uv run support-inbox-triage seed` inserts the 50 mock emails (Gmail `messages.insert`, nothing is sent).
4. `uv run support-inbox-triage run --source gmail`. Gmail now shows the `Triage/*` labels and threaded reply drafts in Drafts; Sent stays empty. A second run skips all 50.

Example schedule (not installed): `0 8 * * 1-5 cd /path/to/repo && uv run support-inbox-triage run --source gmail`

## Measured
The gold labels in `data/gold.json` are the intended labels for the 50 mock emails, checked blind by a second reviewer. The eval reports:
- category accuracy (an ambiguous email counts as correct when it is flagged for review)
- injection recall
- violations: any draft written for an invoice or an injection attempt
- escalation recall: customer threats (chargeback, bank dispute, legal, public review) that got `Triage/Urgent`
- invoice-field accuracy: invoices whose due date and amount were read exactly

The escalation, due-date and amount fields in the gold file were added in round 2: written by the builder from the email text, then labelled blind by a fresh agent (100% agreement: one escalation, `lf-013`; four invoices). "Overdue" is not in the gold file because it depends on the day you run it; the date comparison is code and has its own tests.

## Results

The 50 emails are generated test data for a fictional shop, not real customer mail. A perfect score here shows the pipeline works end to end on realistic messy cases; real inbox data would need its own gold set first.

Live Gmail run, 2026-11-05 (`claude-sonnet-5-5`, threshold 0.70), on the seeded demo inbox:

| Emails | Support | Sales | Invoice | Spam | Other | Drafts | Suspicious | Failed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 50 | 19 | 9 | 4 | 9 | 9 | 25 | 4 | 0 |

Eval on the same data: **50/50 category accuracy**, injection recall **1.0** (all 4 planted attacks caught, no draft written for any), **0 violations**.

Offline eval after round 2 (morning summary, urgency flags, scam warning), 2026-11-05, same model: **50/50 accuracy**, injection recall **1.0**, **0 violations**, escalation recall **1.0** (1/1), invoice-field accuracy **1.0** (4/4), 0 flag errors. Same counts as above; 1 email (an empty "candle" email) went to review.

## Known limits
- Each run reads the newest 100 messages in the inbox. If more than 100 untriaged emails pile up, the oldest wait until newer ones are triaged.
- When the model refuses to classify an email, the SDK reports it as a parsing error, so the report says "invalid model output" instead of "model refusal". The email still goes to `Triage/Needs review`.
- The draft link check covers web links (scheme, `www.` and host/path forms) but not email addresses or port numbers.
- The injection pre-scan is deliberately simple. "Disregard the above address" gets flagged as suspicious (no draft, human review), and rephrased attacks rely on the model's own verdict.
- "Overdue" uses the run date in UTC, so an invoice due today is not overdue yet.
- The scam warning covers suspicious emails only. A plain phishing email (sorted as spam and marked read) gets no warning, and a non-suspicious email's outside links are handled by the draft link check instead.
- "Has a date" relies on the model's reading of the email. A courier pickup notice counts as a date to prepare for, so it stays unread too.
- Every draft is reviewed by a person before sending; nothing is sent automatically.

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
