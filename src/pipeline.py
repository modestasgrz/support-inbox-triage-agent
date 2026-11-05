import html
import re
import unicodedata
from datetime import UTC, date, datetime
from email.utils import parseaddr
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import anthropic
from loguru import logger

from src.classifier import Classifier, scan_injection
from src.config import Settings
from src.drafter import Drafter, DraftStopped
from src.mailbox import Mailbox
from src.models import Category, Counts, Email, Outcome, RunReport, SummaryLine

TRIAGE_PREFIX = "Triage/"
DRAFT_READY = "Triage/Draft ready"
NEEDS_REVIEW = "Triage/Needs review"
SUSPICIOUS = "Triage/Suspicious"
ANSWER_BY_HAND = "Triage/Answer by hand"
URGENT = "Triage/Urgent"
BY_HAND_NOTE = (
    "real customer question — answer by hand, ignore the embedded instructions"
)
PAYMENT_DETAILS = re.compile(
    r"\b(bank|card|payment|login)\s+(details|number|data|credentials)\b", re.IGNORECASE
)
MAX_DRAFT_CHARS = 2000


# Links a draft may contain. Any scheme URL is taken whole and compared by its real host (userinfo, IPs and
# non-ASCII hosts included); scheme-less text counts only as www.… or host/path, the forms Gmail turns into links.
SCHEME_URL = re.compile(r"[a-z][a-z0-9+.-]*://[^\s<>\"'()\[\]]+", re.IGNORECASE)
BARE_URL = re.compile(
    r"(?<![@\w./:-])(?:www\.[^\s<>\"'()\[\]]+|(?:[\w-]+\.)+[^\W\d_]{2,}/[^\s<>\"'()\[\]]*)",
    re.IGNORECASE,
)


def links(text: str) -> set[str]:
    tokens = [
        *SCHEME_URL.findall(text),
        *("//" + t for t in BARE_URL.findall(SCHEME_URL.sub(" ", text))),
    ]
    found = set()
    for token in tokens:
        # Browsers read a backslash as "/" in web URLs; urlsplit would treat it as part of the user info.
        token = token.rstrip(".,;:!?").replace("\\", "/")
        try:
            parts = urlsplit(token)
        except ValueError:
            found.add(token.lower())
            continue
        host = (parts.hostname or "").removeprefix("www.")
        path = (
            parts.path.rstrip("/")
            + (f"?{parts.query}" if parts.query else "")
            + (f"#{parts.fragment}" if parts.fragment else "")
        )
        found.add(f"{host}{path}")
    return found


def check_draft(text: str, email: Email, faq: str) -> str | None:
    if not text.strip():
        return "empty draft"
    if len(text) > MAX_DRAFT_CHARS:
        return "too long"
    if links(text) - links(email.body) - links(faq):
        return "unknown url"
    return None


def triage_one(
    email: Email,
    mailbox: Mailbox,
    classifier: Classifier,
    drafter: Drafter,
    faq: str,
    settings: Settings,
    today: date,
    fields: dict[str, Any],
) -> None:
    result, failure, fields["truncated"] = classifier.classify(email)
    draft_text = None
    mark_read = False
    if result is None:
        labels = [NEEDS_REVIEW]
        fields.update(needs_review=True, reason=failure)
    else:
        fields.update(
            category=result.category,
            confidence=result.confidence,
            reason=result.reason,
            evidence_quote=result.evidence_quote,
        )
        customer = result.category in (Category.SUPPORT, Category.SALES)
        if customer:
            fields["escalation"] = result.escalation
        elif result.category == Category.INVOICE:
            fields.update(
                due_date=result.due_date,
                amount_due=result.amount_due,
                currency=result.currency,
            )
        elif result.category == Category.OTHER:
            fields["event_date"] = result.event_date
        prescan = scan_injection(f"{email.sender}\n{email.subject}\n{email.body}")
        labels = [f"{TRIAGE_PREFIX}{result.category.value.capitalize()}"]
        suspicious = bool(result.injection_suspected or prescan)
        if suspicious:
            reasons = []
            if result.injection_suspected:
                reasons.append(result.injection_reason or "model flagged injection")
            if prescan:
                reasons.append(f"pre-scan: {prescan}")
            labels.append(SUSPICIOUS)
            if customer:
                labels.append(ANSWER_BY_HAND)
            outside = sorted(
                {re.split(r"[/?#]", link, maxsplit=1)[0] for link in links(email.body)}
                - {re.split(r"[/?#]", link, maxsplit=1)[0] for link in links(faq)}
            )
            fields.update(
                suspicious=True,
                injection_reason="; ".join(reasons),
                answer_by_hand=customer,
                scam_warning=bool(
                    outside
                    or PAYMENT_DETAILS.search(unicodedata.normalize("NFKC", email.body))
                ),
                scam_domains=[host.replace(".", "[.]") for host in outside],
            )
        elif result.confidence < settings.confidence_threshold:
            labels.append(NEEDS_REVIEW)
            fields["needs_review"] = True
        elif customer:
            try:
                draft_text = drafter.draft(email, result)
                rejected = check_draft(draft_text, email, faq)
            except anthropic.APIError as e:
                rejected = f"draft failed: {type(e).__name__}"
            except DraftStopped as e:
                rejected = f"draft stopped: {e.stop_reason}"
            if rejected:
                draft_text = None
                labels.append(NEEDS_REVIEW)
                fields.update(needs_review=True, draft_rejected=rejected)
            else:
                labels.append(DRAFT_READY)
        elif result.category == Category.SPAM or (
            result.category == Category.OTHER and result.event_date is None
        ):
            mark_read = True
        if customer and result.escalation:
            labels.append(URGENT)
            fields["urgent_reason"] = (
                f'escalation: "{result.escalation_quote}"'
                if result.escalation_quote
                else "escalation"
            )
        elif (
            result.category == Category.INVOICE
            and not suspicious
            and result.due_date is not None
            and result.due_date < today
        ):
            labels.append(URGENT)
            fields.update(
                overdue=True,
                urgent_reason=f"invoice overdue since {result.due_date.isoformat()}",
            )
    # Labels go on before the draft: a labelled email is skipped next run, so a later failure can never double-draft.
    mailbox.add_labels(email.id, labels)
    fields["labels"] = labels
    if draft_text is not None:
        try:
            fields["draft_id"] = mailbox.create_draft(email, draft_text)
            fields["draft_text"] = draft_text
        except Exception as e:
            # Draft ready stays on the email next to Needs review; the report marks it failed.
            fields.update(
                needs_review=True, draft_rejected=f"draft failed: {type(e).__name__}"
            )
            mailbox.add_labels(email.id, [NEEDS_REVIEW])
            fields["labels"] = [*labels, NEEDS_REVIEW]
            raise
    if mark_read:
        mailbox.mark_read(email.id)
        fields["marked_read"] = True


def run_triage(
    mailbox: Mailbox,
    classifier: Classifier,
    drafter: Drafter,
    faq: str,
    settings: Settings,
    out_dir: Path,
) -> RunReport:
    started = datetime.now(UTC)
    emails = mailbox.fetch_inbox()
    todo = [
        e
        for e in emails
        if not any(label.startswith(TRIAGE_PREFIX) for label in e.label_names)
    ]
    outcomes = []
    for email in todo:
        fields: dict[str, Any] = {
            "email_id": email.id,
            "status": "ok",
            "category": None,
            "confidence": None,
            "reason": None,
            "evidence_quote": None,
            "labels": [],
            "marked_read": False,
            "draft_id": None,
            "needs_review": False,
            "suspicious": False,
            "injection_reason": None,
            "draft_rejected": None,
            "truncated": False,
            "error_type": None,
        }
        try:
            triage_one(
                email,
                mailbox,
                classifier,
                drafter,
                faq,
                settings,
                started.date(),
                fields,
            )
        except Exception as e:
            fields.update(status="failed", error_type=type(e).__name__)
            if not fields["labels"]:
                # Nothing reached Gmail, so the row shows no flag that Gmail doesn't have.
                fields.update(
                    suspicious=False,
                    needs_review=False,
                    draft_rejected=None,
                    injection_reason=None,
                    event_date=None,
                    answer_by_hand=False,
                    urgent_reason=None,
                    overdue=False,
                    scam_warning=False,
                    scam_domains=[],
                )
            logger.warning("Email {} failed: {}", email.id, type(e).__name__)
        else:
            logger.info("Email {} labelled {}", email.id, fields["labels"])
        outcomes.append(Outcome(**fields))

    ok = [o for o in outcomes if o.status == "ok"]
    by_id = {e.id: e for e in todo}
    counts = Counts(
        **{c.value: sum(o.category == c for o in ok) for c in Category},
        needs_review=sum(o.needs_review for o in ok),
        suspicious=sum(o.suspicious for o in ok),
        failed=len(outcomes) - len(ok),
        drafts=sum(o.draft_id is not None for o in ok),
        skipped=len(emails) - len(todo),
        total=len(outcomes),
    )
    report = RunReport(
        run_id=started.strftime("%Y%m%d-%H%M%S"),
        started_at=started,
        model=settings.model,
        outcomes=outcomes,
        counts=counts,
        skipped_ids=[e.id for e in emails if e not in todo],
        unreadable_ids=mailbox.unreadable,
        summary=summarize(ok, by_id, counts),
    )
    write_reports(report, by_id, out_dir)
    logger.info("Run {}: {}", report.run_id, counts.model_dump())
    return report


def summarize(
    ok: list[Outcome], emails: dict[str, Email], counts: Counts
) -> list[SummaryLine]:
    def ref(o: Outcome) -> str:
        return f'{o.email_id} "{emails[o.email_id].subject[:60]}"'

    urgent = [o for o in ok if o.urgent_reason]
    by_hand = [o for o in ok if o.answer_by_hand]
    scams = [o for o in ok if o.scam_warning]
    invoices = [o for o in ok if o.category == Category.INVOICE]
    dated = [o for o in ok if o.event_date is not None]
    totals: dict[str, float] = {}
    for o in invoices:
        if o.amount_due is not None:
            currency = (o.currency or "?").upper()
            totals[currency] = totals.get(currency, 0.0) + o.amount_due
    missing = sum(o.amount_due is None for o in invoices)
    money = (
        " + ".join(f"{c} {v:,.2f}" for c, v in sorted(totals.items())) + " total"
        if totals
        else "no amount found"
    )
    if totals and missing:
        money += f" ({missing} without an amount)"
    return [
        SummaryLine(
            kind="urgent",
            text=f"Urgent ({len(urgent)}): "
            + "; ".join(f"{ref(o)} ({o.urgent_reason})" for o in urgent)
            if urgent
            else "Urgent: none",
        ),
        SummaryLine(
            kind="answer_by_hand",
            text=f"Answer by hand ({len(by_hand)}): "
            + "; ".join(ref(o) for o in by_hand)
            + " — real customer questions with planted instructions, no draft written"
            if by_hand
            else "Answer by hand: none",
        ),
        SummaryLine(
            kind="scam",
            text=f"SCAM WARNING ({len(scams)}): "
            + "; ".join(
                f"{ref(o)} → {', '.join(o.scam_domains) or 'asks for bank/card details'}"
                for o in scams
            )
            + " — never send these to customers"
            if scams
            else "Scam warnings: none",
        ),
        SummaryLine(
            kind="invoices",
            text=f"Invoices ({len(invoices)}): {money}, {sum(o.overdue for o in invoices)} overdue"
            if invoices
            else "Invoices: none",
        ),
        SummaryLine(
            kind="dated",
            text=f"Has a date ({len(dated)}): "
            + "; ".join(f"{ref(o)} on {o.event_date}" for o in dated)
            + " — left unread"
            if dated
            else "Has a date: none",
        ),
        SummaryLine(
            kind="drafts",
            text=f"Drafts ready to review: {counts.drafts}",
        ),
        SummaryLine(
            kind="rest",
            text=f"Rest: {counts.spam} spam, {sum(o.category == Category.OTHER and o.marked_read for o in ok)} other marked read, "
            f"{counts.needs_review} need review, {counts.failed} failed, {counts.skipped} skipped",
        ),
    ]


def note(o: Outcome) -> str:
    return "; ".join(
        part
        for part in (
            BY_HAND_NOTE if o.answer_by_hand else "",
            f"SCAM: {', '.join(o.scam_domains) or 'asks for bank/card details'}"
            if o.scam_warning
            else "",
            o.urgent_reason or "",
            f"has a date: {o.event_date}" if o.event_date else "",
            o.injection_reason or o.draft_rejected or o.error_type or "",
        )
        if part
    )


def flags(o: Outcome) -> list[str]:
    return [
        name
        for name, on in (
            ("FAILED", o.status == "failed"),
            ("URGENT", o.urgent_reason is not None),
            ("SCAM", o.scam_warning),
            ("SUSPICIOUS", o.suspicious),
            ("ANSWER BY HAND", o.answer_by_hand),
            ("NEEDS REVIEW", o.needs_review),
            ("DRAFT", o.draft_id is not None),
        )
        if on
    ]


def write_reports(report: RunReport, emails: dict[str, Email], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(report.model_dump_json(indent=2))

    def md(value: object) -> str:
        # Defanged so no Markdown viewer turns a URL, [text](link) or email address from an email or the model into a link.
        text = html.escape(str(value if value is not None else ""), quote=False)
        text = re.sub(
            r"(?i)\bwww\.",
            "www[.]",
            text.replace("://", "[:]//")
            .replace("@", "[@]")
            .replace("|", "\\|")
            .replace("\r", " ")
            .replace("\n", " "),
        )
        # Last, so the "[" added by the defangs above can't open a link either.
        return text.replace("](", "]\\(")

    lines = [
        f"# Triage run {report.run_id}",
        "",
        "## Morning summary",
        "",
        *(f"- {md(line.text)}" for line in report.summary),
        "",
        f"Model: {report.model}",
        "",
        "| "
        + " | ".join(f"{k}: {v}" for k, v in report.counts.model_dump().items())
        + " |",
        "",
        f"Skipped (already triaged): {', '.join(report.skipped_ids) or 'none'}",
        "",
        f"Could not be read (retried next run): {', '.join(report.unreadable_ids) or 'none'}",
        "",
        "| Email | From | Subject | Category | Conf. | Labels | Flags | Reason | Evidence | Note | Draft |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for o in report.outcomes:
        e = emails[o.email_id]
        draft = "<br>".join(md(line) for line in (o.draft_text or "").splitlines())
        lines.append(
            "| "
            + " | ".join(
                md(v)
                for v in (
                    o.email_id,
                    e.sender,
                    e.subject,
                    o.category,
                    o.confidence,
                    ", ".join(o.labels),
                    ", ".join(flags(o)),
                    o.reason,
                    o.evidence_quote,
                    note(o),
                )
            )
            + f" | {draft} |"
        )
    (out_dir / "report.md").write_text("\n".join(lines) + "\n")

    def h(value: object, defang: bool = True) -> str:
        # Escaped, then link-shaped text is broken (as in report.md) or, for draft text that must copy verbatim,
        # entity-encoded so the file never holds a URL scheme, href=/src=, url( or @import.
        text = html.escape(str(value if value is not None else ""))
        if defang:
            text = re.sub(r"(?i)\bwww\.", "www[.]", text.replace("://", "[:]//"))
        text = text.replace("://", "&#58;//")
        text = re.sub(r"(?i)\b(href|src)=", r"\1&#61;", text)
        return re.sub(
            r"(?i)url\(|@import",
            lambda m: "&#" + str(ord(m[0][0])) + ";" + m[0][1:],
            text,
        )

    def day(d: date | None) -> str:
        return f"{d.day} {d:%b %Y}" if d else ""

    def who(o: Outcome) -> str:
        name, address = parseaddr(emails[o.email_id].sender)
        return h(name or address or emails[o.email_id].sender)

    def head(o: Outcome) -> str:
        return f'<p class="who"><b>{who(o)}</b> <span>{h(emails[o.email_id].subject)}</span></p>'

    def money(o: Outcome) -> str:
        return (
            f"{(o.currency or '').upper()} {o.amount_due:,.2f}".strip()
            if o.amount_due is not None
            else "—"
        )

    ok = [o for o in report.outcomes if o.status == "ok"]
    needs = sorted(
        (o for o in ok if o.urgent_reason or o.answer_by_hand),
        key=lambda o: o.urgent_reason is None,
    )
    scams = [o for o in ok if o.scam_warning]
    drafted = sorted(
        (o for o in ok if o.draft_text is not None),
        key=lambda o: (o.urgent_reason is None, o.category != Category.SUPPORT),
    )
    invoices = sorted(
        (o for o in ok if o.category == Category.INVOICE),
        key=lambda o: o.due_date or date.max,
    )
    dated = sorted(
        (o for o in ok if o.event_date is not None),
        key=lambda o: o.event_date or date.max,
    )
    spam = [o for o in ok if o.category == Category.SPAM]
    spam_read = [o for o in spam if o.marked_read]
    other_read = [o for o in ok if o.category == Category.OTHER and o.marked_read]
    review = [o for o in ok if o.needs_review]
    unread_suspicious = [
        o
        for o in ok
        if o.suspicious
        and not (o.answer_by_hand or o.needs_review or o.event_date)
        and o.category != Category.INVOICE
    ]
    failed = [o for o in report.outcomes if o.status == "failed"]
    started = report.started_at
    when = f"{started:%A} {started.day} {started:%b}"
    n = len(needs)
    title = (
        "nothing needs you"
        if n == 0
        else "1 thing needs you"
        if n == 1
        else f"{n} things need you"
    )
    urgent_n = sum(o.urgent_reason is not None for o in ok)
    by_hand_n = sum(o.answer_by_hand for o in ok)
    lede = ", ".join(
        part
        for part in (
            f"{urgent_n} urgent" if urgent_n else "",
            f"{by_hand_n} to answer yourself" if by_hand_n else "",
            f"{len(scams)} scam{'s' if len(scams) != 1 else ''} blocked"
            if scams
            else "",
            f"{len(drafted)} draft{'s' if len(drafted) != 1 else ''} ready",
        )
        if part
    )
    numbers = "".join(
        f"<div><b>{v}</b><span>{k}</span></div>"
        for k, v in (
            ("Needs you", n),
            ("Drafts ready", len(drafted)),
            ("Invoices due", len(invoices)),
            ("Spam", len(spam)),
        )
    )

    cards = []
    for o in needs:
        urgent = (
            "Invoice overdue"
            + (f" · {h(money(o))}" if o.amount_due is not None else "")
            if o.overdue
            else f'<span class="cap">{h(o.urgent_reason)}</span>'
            if o.urgent_reason
            else ""
        )
        why = (
            f'<span class="cap">{h(BY_HAND_NOTE)}</span>'
            if o.answer_by_hand
            else urgent
        )
        extra = []
        if o.answer_by_hand and urgent:
            extra.append(f'<span class="urgent-line">{urgent}</span>')
        if o.scam_warning:
            extra.append(
                "Has a scam link in it — answer the question, never the link. See below."
            )
        if o.draft_text is not None:
            extra.append("Reply drafted — first in Drafts ready.")
        due = f'<p class="due">Due {h(day(o.due_date))}</p>' if o.due_date else ""
        cards.append(
            f'<article class="card {"urgent" if o.urgent_reason else "by-hand"}" title="email id {h(o.email_id)}">'
            f'{head(o)}<p class="gist">{h(o.reason)}</p><p class="why">{why}</p>'
            + "".join(f'<p class="also">{x}</p>' for x in extra)
            + f"{due}</article>"
        )
    needs_html = "".join(cards) or '<p class="quiet">Nothing needs you today.</p>'

    scam_html = ""
    if scams:
        items = "".join(
            f'<li title="email id {h(o.email_id)}">{head(o)}<p class="bad">'
            + (
                " ".join(f"<code>{h(d)}</code>" for d in o.scam_domains)
                if o.scam_domains
                else "asks for bank/card details"
            )
            + "</p></li>"
            for o in scams
        )
        scam_html = (
            '<section id="scams"><h2>Scam attempts</h2>'
            f'<p class="warn">Don\'t reply · don\'t click.</p><ul class="plain">{items}</ul>'
            "<p class=\"quiet\">Never reply through, click or pass on these addresses. The customer's real question is in the card above: answer it by hand. The addresses are broken on purpose so they can't be opened.</p></section>"
        )

    groups = {
        "urgent": "Urgent first",
        "support": "Customer questions",
        "sales": "Sales and wholesale",
    }
    draft_cards = "".join(
        (
            f"<h3>{groups[key]}</h3>"
            if i == 0
            or key
            != (
                "urgent"
                if drafted[i - 1].urgent_reason
                else str(drafted[i - 1].category)
            )
            else ""
        )
        + f'<article class="draft-card" title="email id {h(o.email_id)}">{head(o)}<p class="gist" title="{h(o.reason)}">{h(o.reason)}</p>'
        f'<div class="toggles"><details class="draft"><summary>Show draft</summary><pre>{h(o.draft_text, defang=False)}</pre>'
        f'<button type="button" class="copy">Copy</button></details>'
        f'<details class="why"><summary>Why</summary><p>They wrote: <q>{h(o.evidence_quote)}</q></p></details></div></article>'
        for i, o in enumerate(drafted)
        for key in ["urgent" if o.urgent_reason else str(o.category)]
    )
    drafts_html = (
        f'<section id="drafts"><h2>Drafts ready <span>{len(drafted)}</span></h2>'
        '<p class="quiet">Each one is saved in Gmail as a draft on its thread. Read it, edit if needed, then send it yourself.</p>'
        f"{draft_cards or '<p class=quiet>No drafts this morning.</p>'}</section>"
    )

    invoices_html = ""
    if invoices:
        rows = "".join(
            f'<tr title="email id {h(o.email_id)}"><td>{who(o)}</td><td class="num">{h(money(o))}</td>'
            f"<td>{h(day(o.due_date)) or '—'}</td><td>"
            + ('<span class="tag overdue">overdue</span>' if o.overdue else "")
            + ('<span class="tag">check sender</span>' if o.suspicious else "")
            + "</td></tr>"
            for o in invoices
        )
        invoices_html = (
            f'<section id="invoices"><h2>Invoices <span>{len(invoices)}</span></h2><div class="scroll"><table>'
            f'<thead><tr><th>Supplier</th><th class="num">Amount</th><th>Due</th><th></th></tr></thead><tbody>{rows}</tbody></table></div>'
            '<p class="quiet">Left unread in Gmail for you to pay or check.</p></section>'
        )

    dated_html = ""
    if dated:
        items = "".join(
            f'<li title="email id {h(o.email_id)}"><span class="date">{h(day(o.event_date))}</span>{head(o)}</li>'
            for o in dated
        )
        dated_html = f'<section id="dated"><h2>Has a date <span>{len(dated)}</span></h2><ul class="plain dates">{items}</ul></section>'

    def group(name: str, items: list[Outcome], detail: bool = False) -> str:
        if not items:
            return ""
        lis = "".join(
            f"<li>{head(o)}"
            + (
                f'<p class="quiet">{h(o.injection_reason or o.draft_rejected or o.error_type or o.reason)}</p>'
                if detail
                else ""
            )
            + "</li>"
            for o in items
        )
        return f'<h3>{name} <span>{len(items)}</span></h3><ul class="plain small">{lis}</ul>'

    every = "".join(
        f"<li><code>{h(o.email_id)}</code> {h(o.category or o.status)}"
        + (f" · {o.confidence:.2f}" if o.confidence is not None else "")
        + f" · {h(', '.join(o.labels) or 'no labels')}"
        + "".join(
            f'<br><span class="quiet">{h(text)}</span>'
            for text in (
                o.reason,
                o.evidence_quote and f"“{o.evidence_quote}”",
                o.injection_reason or o.draft_rejected or o.error_type,
            )
            if text
        )
        + "</li>"
        for o in report.outcomes
    )
    rest_n = len(
        {
            o.email_id
            for o in (*review, *failed, *unread_suspicious, *spam_read, *other_read)
        }
    )
    rest_html = (
        f'<details id="rest"><summary>Everything else <span>{rest_n}</span></summary>'
        + group("Needs a look", review, detail=True)
        + group("Failed — retried next run", failed, detail=True)
        + group("Suspicious, left unread", unread_suspicious, detail=True)
        + group("Spam, marked read", spam_read)
        + group("Other, marked read", other_read)
        + '<h3>Morning summary, plain text</h3><ul class="plain small">'
        + "".join(f"<li>{h(line.text)}</li>" for line in report.summary)
        + '</ul><h3>Every email</h3><ul class="plain small mono">'
        + every
        + "</ul><h3>Run</h3>"
        + f'<p class="small">Model {h(report.model)} · run {h(report.run_id)} · started {h(report.started_at.isoformat(timespec="seconds"))}</p>'
        + '<p class="small">'
        + h(
            ", ".join(
                f"{k.replace('_', ' ')} {v}"
                for k, v in report.counts.model_dump().items()
            )
        )
        + "</p>"
        + f'<p class="small">Skipped (already triaged): {h(", ".join(report.skipped_ids) or "none")}<br>Could not be read (retried next run): {h(", ".join(report.unreadable_ids) or "none")}</p>'
        + "</details>"
    )

    page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Inbox — {h(when)}</title>
<style>
:root{{--paper:#faf7f1;--ink:#26231f;--soft:#6e685e;--line:#e7e1d5;--amber:#b06f12;--amber-bar:#d89a33;--red:#a5402f;--white:#fffdf9;
--serif:"New York","Iowan Old Style","Palatino Linotype",Georgia,serif;--sans:-apple-system,"SF Pro Text",Inter,"Helvetica Neue",Arial,sans-serif;--mono:ui-monospace,"SF Mono",Menlo,monospace}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 var(--sans);-webkit-text-size-adjust:100%}}
main{{max-width:760px;margin:0 auto;padding:4.5rem 1.5rem 5rem}}
h1,h2,h3{{font-family:var(--serif);font-weight:500;letter-spacing:-.01em;margin:0}}
h1{{font-size:2.5rem;line-height:1.12}}
h2{{font-size:1.45rem;margin-bottom:.4rem}} h2 span,h3 span,summary span{{color:var(--soft);font-family:var(--sans);font-size:.9rem;font-weight:400;margin-left:.35rem}}
h3{{font-size:1.1rem;margin:1.8rem 0 .4rem}}
.lede{{font-size:1.2rem;color:var(--soft);margin:.9rem 0 2rem}}
.numbers{{display:grid;grid-template-columns:repeat(4,1fr);border-top:1px solid var(--line);border-bottom:1px solid var(--line);padding:1rem 0}}
.numbers b{{display:block;font:500 1.7rem/1.1 var(--serif)}} .numbers span{{color:var(--soft);font-size:.85rem}}
section,#rest{{margin-top:3.5rem}}
p{{margin:0}} .quiet{{color:var(--soft);font-size:.92rem}}
.who b{{font-weight:600}} .who span{{display:block}}
.card{{border-left:3px solid var(--amber-bar);padding:.2rem 0 .2rem 1.1rem;margin:1.3rem 0}}
.card.urgent{{border-left-color:var(--red)}}
.why{{margin-top:.25rem}} .card.urgent .why,.urgent-line{{color:var(--red)}}
.cap{{display:inline-block}} .cap::first-letter{{text-transform:uppercase}}
.also,.due{{color:var(--soft);font-size:.92rem}} .due{{font-variant-numeric:tabular-nums}}
#scams{{border:1px solid #e3c9bd;background:#fbf1ec;border-radius:6px;padding:1.4rem 1.5rem}}
.warn{{color:var(--red);font-weight:600;margin-bottom:.6rem}}
.bad{{margin-top:.2rem}} code{{font-family:var(--mono);font-size:.88em;background:var(--white);border:1px solid var(--line);border-radius:3px;padding:.05rem .35rem;user-select:all}}
ul.plain{{list-style:none;margin:0;padding:0}} ul.plain>li{{padding:.75rem 0;border-top:1px solid var(--line)}} ul.plain>li:first-child{{border-top:0}}
#scams ul.plain>li{{border-color:#ecd8ce}} #scams .quiet{{margin-top:.6rem}}
.draft-card{{padding:.8rem 0 .7rem;border-top:1px solid var(--line)}}
.draft-card .who{{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}} .draft-card .who span{{display:inline;margin-left:.5rem}}
.draft-card .gist{{display:-webkit-box;-webkit-line-clamp:1;-webkit-box-orient:vertical;overflow:hidden}} #drafts .quiet+.draft-card{{margin-top:.6rem}}
.gist{{color:var(--soft);margin-top:.15rem}}
.toggles{{display:flex;flex-wrap:wrap;gap:.2rem 1.4rem;margin-top:.25rem}} .toggles details[open]{{flex-basis:100%}}
summary{{cursor:pointer;color:var(--amber);font-size:.92rem;list-style:none}} summary::-webkit-details-marker{{display:none}}
summary::before{{content:"▸";display:inline-block;width:1em}} details[open]>summary::before{{content:"▾"}}
summary:focus-visible,button:focus-visible{{outline:2px solid var(--amber);outline-offset:2px;border-radius:2px}}
pre{{font:15px/1.6 var(--sans);white-space:pre-wrap;overflow-wrap:anywhere;max-width:65ch;background:var(--white);border:1px solid var(--line);border-radius:6px;padding:1rem 1.2rem;margin:.6rem 0 .5rem}}
button.copy{{font:inherit;font-size:.88rem;color:var(--amber);background:none;border:1px solid var(--amber-bar);border-radius:999px;padding:.2rem .9rem;cursor:pointer}}
button.copy:hover{{background:var(--white)}}
details.why p{{color:var(--soft);font-size:.92rem;margin:.3rem 0 0 1em}} q{{font-style:italic}}
.scroll{{overflow-x:auto}} table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}}
th{{text-align:left;font-weight:500;color:var(--soft);font-size:.85rem;padding:.4rem .8rem .4rem 0;border-bottom:1px solid var(--line)}}
td{{padding:.6rem .8rem .6rem 0;border-bottom:1px solid var(--line);vertical-align:top}} td+td{{white-space:nowrap}} .num{{text-align:right;white-space:nowrap}}
#invoices .quiet{{margin-top:.6rem}}
.tag{{font-size:.8rem;color:var(--soft);border:1px solid var(--line);border-radius:999px;padding:0 .5rem;white-space:nowrap}} .tag.overdue{{color:#fff;background:var(--red);border-color:var(--red)}}
.dates li{{display:grid;grid-template-columns:7.5rem 1fr;gap:1rem}} .date{{font-family:var(--serif)}}
#rest{{border-top:1px solid var(--line);padding-top:1.2rem}}
#rest>summary{{font-family:var(--serif);font-size:1.2rem;color:var(--ink)}}
.small{{font-size:.88rem}} .mono li{{color:var(--soft)}} .mono code{{background:none;border:0;padding:0;color:var(--ink)}}
footer{{margin-top:3rem;color:var(--soft);font-size:.85rem}}
@media (max-width:560px){{main{{padding:2.5rem 1.1rem 3rem}} h1{{font-size:1.95rem}} .when{{display:block;font-size:1.1rem;color:var(--soft);margin-bottom:.2rem}} .lede{{font-size:1.05rem}}
.numbers{{grid-template-columns:repeat(2,1fr);row-gap:.9rem}} thead{{display:none}} tr{{display:grid;grid-template-columns:1fr auto;padding:.6rem 0;border-bottom:1px solid var(--line)}} td{{border:0;padding:0}} td:first-child{{grid-column:1/-1;font-weight:600}} td+td{{white-space:normal}} .num{{text-align:left}} .dates li{{grid-template-columns:1fr;gap:.1rem}} #scams{{padding:1.1rem}}}}
</style></head><body><main>
<h1><span class="when">{h(when)} —</span> {title}</h1>
<p class="lede">{lede}.</p>
<div class="numbers">{numbers}</div>
<section id="needs-you"><h2>Needs you today</h2>{needs_html}</section>
{scam_html}
{drafts_html}
{invoices_html}
{dated_html}
{rest_html}
<footer>Drafts only — nothing was sent.</footer>
</main>
<script>
document.querySelectorAll("button.copy").forEach(function (b) {{
  b.addEventListener("click", function () {{
    var text = b.previousElementSibling;
    function pick() {{ getSelection().selectAllChildren(text); b.textContent = "Selected — press ⌘C"; }}
    if (!navigator.clipboard) {{ pick(); return; }}
    navigator.clipboard.writeText(text.textContent).then(function () {{
      b.textContent = "Copied";
      setTimeout(function () {{ b.textContent = "Copy"; }}, 1600);
    }}, pick);
  }});
}});
</script>
</body></html>
"""
    (out_dir / "report.html").write_text(page)
