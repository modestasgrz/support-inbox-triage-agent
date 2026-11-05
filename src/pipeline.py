import html
import re
import unicodedata
from datetime import UTC, date, datetime
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

    def h(value: object) -> str:
        return html.escape(str(value if value is not None else ""))

    tiles = "".join(
        f'<div class="tile"><b>{h(v)}</b><span>{h(k.replace("_", " "))}</span></div>'
        for k, v in report.counts.model_dump().items()
    )
    alert = (
        " alert"
        if any(o.scam_warning for o in report.outcomes if o.status == "ok")
        else ""
    )
    summary = "".join(
        f'<li class="{line.kind}{alert if line.kind == "scam" else ""}">{h(line.text)}</li>'
        for line in report.summary
    )
    rows = []
    for o in report.outcomes:
        e = emails[o.email_id]
        chips = "".join(
            f'<span class="chip {h(f.split()[0].lower())}">{h(f)}</span>'
            for f in flags(o)
        )
        conf = f"{o.confidence:.2f}" if o.confidence is not None else ""
        rows.append(
            f"<tr><td>{h(o.email_id)}</td><td>{h(e.sender)}<br><small>{h(e.subject)}</small></td><td>{h(o.category)}</td><td>{h(conf)}</td>"
            f"<td>{h(', '.join(o.labels))}</td><td>{chips}</td><td>{h(o.reason)}<br><q>{h(o.evidence_quote)}</q></td><td>{h(note(o))}</td><td><div class='reply'>{h(o.draft_text)}</div></td></tr>"
        )
    page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Triage run {h(report.run_id)}</title>
<style>
body{{font:14px/1.45 system-ui,sans-serif;margin:2rem;color:#1d1d1f;background:#fafaf7}}
h1{{font-size:1.4rem;margin:0 0 .2rem}} .sub{{color:#666;margin-bottom:1.2rem}}
.tiles{{display:flex;flex-wrap:wrap;gap:.5rem;margin-bottom:1.5rem}}
.tile{{background:#fff;border:1px solid #e3e1da;border-radius:8px;padding:.5rem .8rem;min-width:5.5rem}}
.tile b{{display:block;font-size:1.3rem}} .tile span{{color:#666;font-size:.8rem}}
table{{border-collapse:collapse;width:100%;background:#fff}}
th,td{{border-bottom:1px solid #eee;padding:.45rem .5rem;text-align:left;vertical-align:top}}
th{{background:#f2f0ea;font-size:.8rem;text-transform:uppercase;letter-spacing:.03em}}
small{{color:#666}} q{{color:#555;font-style:italic}}
.chip{{display:inline-block;border-radius:10px;padding:0 .5rem;margin:0 .2rem .2rem 0;font-size:.75rem;font-weight:600}}
.summary{{background:#fff;border:1px solid #e3e1da;border-radius:8px;padding:.6rem 1.2rem;margin-bottom:1.5rem;max-width:70rem}}
.summary h2{{font-size:1.05rem;margin:.3rem 0}} .summary ul{{margin:.2rem 0;padding-left:1.1rem}} .summary li{{margin:.25rem 0}}
.summary .urgent{{font-weight:600;color:#a11}} .summary .alert{{background:#a11;color:#fff;font-weight:700;font-size:1.05rem;padding:.4rem .6rem;border-radius:6px;list-style:none;margin-left:-1.1rem}}
.reply{{white-space:pre-wrap;max-width:28rem;font-size:.85rem;color:#333}}
.chip.failed,.chip.urgent,.chip.scam{{background:#fde2e1;color:#a11}} .chip.answer{{background:#ffe3cc;color:#8a3b00}} .chip.suspicious{{background:#fff0c2;color:#7a5200}} .chip.needs{{background:#e6ecff;color:#1d3d9a}} .chip.draft{{background:#dff5e3;color:#17652a}}
</style></head><body>
<h1>Lumenfold support inbox: triage run {h(report.run_id)}</h1>
<div class="sub">Model {h(report.model)} · started {h(report.started_at.isoformat(timespec="seconds"))} · drafts only, nothing was sent</div>
<section class="summary"><h2>Morning summary</h2><ul>{summary}</ul></section>
<div class="tiles">{tiles}</div>
<table><thead><tr><th>Email</th><th>From / subject</th><th>Category</th><th>Conf.</th><th>Labels</th><th>Flags</th><th>Reason / evidence</th><th>Note</th><th>Draft</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table>
<p class="sub">Skipped (already triaged): {h(", ".join(report.skipped_ids) or "none")}<br>Could not be read (retried next run): {h(", ".join(report.unreadable_ids) or "none")}</p>
</body></html>
"""
    (out_dir / "report.html").write_text(page)
