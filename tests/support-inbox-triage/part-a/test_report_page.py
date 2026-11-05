import html
import re
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from src.models import Category, Counts, Email, Outcome, RunReport, SummaryLine
from src.pipeline import BY_HAND_NOTE, write_reports

STARTED = datetime(2026, 11, 5, 7, 30, tzinfo=UTC)
HOSTILE = (
    '"\'><script>alert(1)</script> & javascript:alert(1) onmouseover="x()" <img src=x>'
)


def mail(i: int, **fields: Any) -> Email:
    data: dict[str, Any] = {
        "id": f"msg-{i:04d}",
        "thread_id": f"t{i}",
        "rfc822_message_id": f"<m{i}@mail.customer.example>",
        "sender": f"Customer {i} <c{i}@customer.example>",
        "subject": f"Subject {i}",
        "body": "Hello.",
        "received_at": datetime(2026, 11, 1, 9, 0, tzinfo=UTC),
    }
    data.update(fields)
    return Email(**data)


def outcome(i: int, **fields: Any) -> Outcome:
    data: dict[str, Any] = {
        "email_id": f"msg-{i:04d}",
        "status": "ok",
        "category": Category.SUPPORT,
        "confidence": 0.87,
        "reason": f"reason {i}",
        "evidence_quote": f"quote {i}",
        "labels": ["Triage/Support"],
        "marked_read": False,
        "draft_id": None,
        "needs_review": False,
        "suspicious": False,
        "injection_reason": None,
        "draft_rejected": None,
        "truncated": False,
        "error_type": None,
    }
    data.update(fields)
    return Outcome(**data)


def render(
    tmp_path: Path,
    outcomes: list[Outcome],
    emails: list[Email] | None = None,
    summary: list[SummaryLine] | None = None,
) -> str:
    by_id = {e.id: e for e in emails or []}
    for o in outcomes:
        if o.email_id not in by_id:
            by_id[o.email_id] = mail(int(o.email_id.split("-")[1]))
    report = RunReport(
        run_id="20261105-073000",
        started_at=STARTED,
        model="claude-test-model",
        outcomes=outcomes,
        counts=Counts(total=len(outcomes)),
        summary=summary or [],
    )
    write_reports(report, by_id, tmp_path)
    return (tmp_path / "report.html").read_text()


def before_rest(page: str) -> str:
    return page.split('<details id="rest">')[0]


def rest(page: str) -> str:
    return page.split('<details id="rest">')[1]


def section(page: str, sid: str) -> str:
    return page.split(f'<section id="{sid}">')[1].split("</section>")[0]


class Visible(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0
        self.tags: list[tuple[str, list[tuple[str, str | None]]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, attrs))
        if tag in ("style", "script"):
            self.skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("style", "script"):
            self.skip -= 1

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def parse(fragment: str) -> Visible:
    parser = Visible()
    parser.feed(fragment)
    parser.close()
    return parser


def visible(fragment: str) -> str:
    return " ".join(" ".join(parse(fragment).parts).split())


def h1(page: str) -> str:
    return visible(page.split("<h1>")[1].split("</h1>")[0])


def lede(page: str) -> str:
    return visible(page.split('<p class="lede">')[1].split("</p>")[0])


def cards(page: str) -> list[str]:
    return re.findall(r"<article class=\"card[^\"]*\".*?</article>", page, re.S)


# ---------- failed outcomes ----------


def test_failed_outcomes_appear_only_inside_rest(tmp_path: Path) -> None:
    # A mark-read failure after labels went on keeps category, urgency and draft text on a failed outcome.
    failed = outcome(
        1,
        status="failed",
        category=Category.INVOICE,
        error_type="HttpError",
        urgent_reason="invoice overdue since 2020-01-02",
        overdue=True,
        due_date=date(2020, 1, 2),
        amount_due=99.0,
        currency="EUR",
        draft_text="FAILED DRAFT TEXT",
        answer_by_hand=True,
        scam_warning=True,
        scam_domains=["evil[.]example"],
        event_date=date(2026, 11, 8),
    )
    bare = outcome(
        2,
        status="failed",
        category=None,
        confidence=None,
        reason=None,
        evidence_quote=None,
        labels=[],
        error_type="APIConnectionError",
    )
    emails = [
        mail(1, sender="Failing Supplier <f@sup.example>", subject="FAILSUBJ-ONE"),
        mail(2, sender="Gone Person <g@x.example>", subject="FAILSUBJ-TWO"),
    ]
    page = render(tmp_path, [failed, bare], emails)
    top = before_rest(page)
    for text in (
        "FAILSUBJ-ONE",
        "FAILSUBJ-TWO",
        "Failing Supplier",
        "FAILED DRAFT TEXT",
        "evil[.]example",
    ):
        assert text not in top
    assert h1(page).endswith("— nothing needs you")
    assert lede(page) == "0 drafts ready."
    assert cards(page) == []
    for sid in ("scams", "invoices", "dated"):
        assert f'id="{sid}"' not in page
    assert '<article class="draft-card"' not in page
    numbers = visible(page.split('<div class="numbers">')[1].split("</div></div>")[0])
    assert numbers == "0 Needs you 0 Drafts ready 0 Invoices due 0 Spam"
    bottom = rest(page)
    failed_group = bottom.split("Failed — retried next run")[1].split("<h3>")[0]
    assert "FAILSUBJ-ONE" in failed_group and "FAILSUBJ-TWO" in failed_group
    assert "HttpError" in failed_group and "APIConnectionError" in failed_group
    assert "msg-0001" in bottom and "msg-0002" in bottom
    assert "Everything else <span>2</span>" in page


# ---------- urgent + answer by hand ----------


def test_urgent_and_answer_by_hand_is_one_red_card_with_the_note_once(
    tmp_path: Path,
) -> None:
    both = outcome(
        1,
        suspicious=True,
        answer_by_hand=True,
        escalation=True,
        urgent_reason="escalation: chargeback",
        labels=["Triage/Support", "Triage/Suspicious", "Triage/Answer by hand"],
    )
    page = render(tmp_path, [both], [mail(1, subject="Refund or I dispute it")])
    found = cards(page)
    assert len(found) == 1
    assert found[0].startswith('<article class="card urgent"')
    assert "Refund or I dispute it" in found[0]
    assert "chargeback" in visible(found[0])
    assert page.count(html.escape(BY_HAND_NOTE)) == 1
    assert html.escape(BY_HAND_NOTE) in found[0]
    assert h1(page).endswith("— 1 thing needs you")
    assert lede(page) == "1 urgent, 1 to answer yourself, 0 drafts ready."


def test_urgent_cards_come_before_answer_by_hand_cards(tmp_path: Path) -> None:
    by_hand = outcome(1, answer_by_hand=True, suspicious=True)
    urgent = outcome(2, escalation=True, urgent_reason="escalation: legal action")
    page = render(tmp_path, [by_hand, urgent])
    found = cards(page)
    assert [c.split('"')[1] for c in found] == ["card urgent", "card by-hand"]
    assert "legal action" in found[0]
    assert html.escape(BY_HAND_NOTE) in found[1].split('<p class="why">')[1]
    assert h1(page).endswith("— 2 things need you")


# ---------- invoices ----------


def test_invoice_without_amount_without_currency_and_suspicious(
    tmp_path: Path,
) -> None:
    no_amount = outcome(
        1,
        category=Category.INVOICE,
        due_date=date(2999, 1, 1),
        amount_due=None,
        currency="EUR",
    )
    no_currency = outcome(
        2,
        category=Category.INVOICE,
        due_date=date(2999, 2, 1),
        amount_due=1184.0,
        currency=None,
    )
    shady = outcome(
        3,
        category=Category.INVOICE,
        suspicious=True,
        due_date=date(2020, 1, 2),
        amount_due=742.8,
        currency="eur",
        labels=["Triage/Invoice", "Triage/Suspicious"],
    )
    no_date = outcome(4, category=Category.INVOICE, amount_due=0.0, currency="USD")
    emails = [
        mail(1, sender="Wax Supplier <w@wax.example>"),
        mail(2, sender="Wick Co <k@wick.example>"),
        mail(3, sender="Shady <s@shady.example>"),
        mail(4, sender="Jar Co <j@jar.example>"),
    ]
    page = render(tmp_path, [no_amount, no_currency, shady, no_date], emails)
    inv = section(page, "invoices")
    assert page.count("<table") == 1 and "<table" in inv
    rows = {
        visible(r).split()[0]: r for r in re.findall(r"<tr title=.*?</tr>", inv, re.S)
    }
    assert set(rows) == {"Wax", "Wick", "Shady", "Jar"}
    assert '<td class="num">—</td>' in rows["Wax"]
    assert "1 Jan 2999" in rows["Wax"]
    assert '<td class="num">1,184.00</td>' in rows["Wick"]
    assert "None" not in rows["Wick"]
    assert '<span class="tag">check sender</span>' in rows["Shady"]
    assert '<td class="num">EUR 742.80</td>' in rows["Shady"]
    assert "overdue" not in rows["Shady"]
    assert '<td class="num">USD 0.00</td>' in rows["Jar"]
    assert "<td>—</td>" in rows["Jar"]
    for name in ("Wax", "Wick", "Jar"):
        assert "check sender" not in rows[name]
    assert cards(page) == []
    assert "4 Invoices due" in visible(page.split('<div class="numbers">')[1])


def test_overdue_invoice_card_without_amount(tmp_path: Path) -> None:
    late = outcome(
        1,
        category=Category.INVOICE,
        overdue=True,
        urgent_reason="invoice overdue since 2020-01-02",
        due_date=date(2020, 1, 2),
        labels=["Triage/Invoice", "Triage/Urgent"],
    )
    page = render(tmp_path, [late])
    (card,) = cards(page)
    assert 'class="card urgent"' in card
    assert "Due 2 Jan 2020" in visible(card)
    assert "None" not in visible(card)
    assert '<span class="tag overdue">overdue</span>' in section(page, "invoices")


# ---------- singular forms and the lede ----------


def test_singular_forms(tmp_path: Path) -> None:
    urgent = outcome(
        1,
        escalation=True,
        urgent_reason="escalation: chargeback",
        draft_text="Draft one",
        draft_id="d1",
    )
    scam = outcome(
        2,
        suspicious=True,
        category=Category.SPAM,
        scam_warning=True,
        scam_domains=[],
    )
    page = render(tmp_path, [urgent, scam])
    assert h1(page) == "Thursday 5 Nov — 1 thing needs you"
    assert lede(page) == "1 urgent, 1 scam blocked, 1 draft ready."
    assert "asks for bank/card details" in section(page, "scams")
    assert "Drafts ready <span>1</span>" in page


def test_plural_forms(tmp_path: Path) -> None:
    outcomes = [
        outcome(1, scam_warning=True, scam_domains=["a[.]example"], suspicious=True),
        outcome(2, scam_warning=True, scam_domains=["b[.]example"], suspicious=True),
        outcome(3, draft_text="x", draft_id="d3"),
        outcome(4, draft_text="y", draft_id="d4"),
    ]
    page = render(tmp_path, outcomes)
    assert lede(page) == "2 scams blocked, 2 drafts ready."
    assert h1(page).endswith("— nothing needs you")


def test_lede_leaves_out_zero_parts_but_keeps_drafts(tmp_path: Path) -> None:
    page = render(tmp_path, [outcome(1, answer_by_hand=True, suspicious=True)])
    assert lede(page) == "1 to answer yourself, 0 drafts ready."
    for zero in ("0 urgent", "0 scam", "0 to answer"):
        assert zero not in lede(page)


def test_empty_run_lede_and_headline(tmp_path: Path) -> None:
    page = render(tmp_path, [])
    assert h1(page) == "Thursday 5 Nov — nothing needs you"
    assert lede(page) == "0 drafts ready."
    assert "Nothing needs you today." in section(page, "needs-you")
    assert "Everything else <span>0</span>" in page


# ---------- details closed ----------


def test_no_details_open_by_default(tmp_path: Path) -> None:
    outcomes = [
        outcome(1, draft_text="open sesame", draft_id="d1", reason="open"),
        outcome(2, draft_text="details open", draft_id="d2"),
        outcome(3, category=Category.SPAM, marked_read=True),
    ]
    page = render(tmp_path, outcomes)
    details = [attrs for tag, attrs in parse(page).tags if tag == "details"]
    assert len(details) == 5
    assert all("open" not in dict(attrs) for attrs in details)
    assert not re.search(r"<details[^>]*\bopen", page)


# ---------- hostile text ----------


def test_hostile_text_stays_escaped_everywhere(tmp_path: Path) -> None:
    evil = outcome(
        1,
        reason=HOSTILE,
        evidence_quote=HOSTILE,
        draft_text=HOSTILE + "\n</pre></details><button>",
        draft_id="d1",
        escalation=True,
        urgent_reason="escalation: " + HOSTILE,
        injection_reason=HOSTILE,
        needs_review=True,
    )
    inv = outcome(
        2,
        category=Category.INVOICE,
        amount_due=1.0,
        currency=HOSTILE,
        due_date=date(2999, 1, 1),
    )
    scam = outcome(
        3,
        scam_warning=True,
        scam_domains=[HOSTILE],
        suspicious=True,
        event_date=date(2026, 11, 8),
    )
    emails = [
        mail(i, sender=f"{HOSTILE} <x{i}@evil.example>", subject=HOSTILE)
        for i in (1, 2, 3)
    ]
    page = render(
        tmp_path,
        [evil, inv, scam],
        emails,
        summary=[SummaryLine(kind="rest", text=HOSTILE)],
    )
    assert "<script>alert" not in page and "<img" not in page
    assert page.count("<script") == 1
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "&amp; javascript:alert(1)" in page
    parser = parse(page)
    allowed = {"class", "id", "title", "lang", "charset", "name", "content", "type"}
    for tag, attrs in parser.tags:
        assert tag not in ("img", "a", "iframe", "link"), tag
        for key, _ in attrs:
            assert key in allowed, (tag, key)
    titles = [v or "" for _, attrs in parser.tags for k, v in attrs if k == "title"]
    assert HOSTILE in titles
    raw_titles = re.findall(r'title="([^"]*)"', page)
    assert any("&quot;&#x27;&gt;&lt;script&gt;" in t for t in raw_titles)
    assert all("<" not in t and ">" not in t for t in raw_titles)
    assert page.count("<pre>") == 1 and page.count("</pre>") == 1
    assert page.count("<button") == 1


def test_hostile_email_id_in_title_attribute_stays_escaped(tmp_path: Path) -> None:
    eid = 'msg-0001" onmouseover="alert(1)'
    o = outcome(1, email_id=eid, draft_text="hi", draft_id="d1", answer_by_hand=True)
    page = render(tmp_path, [o], [mail(1, id=eid)])
    assert 'onmouseover="alert(1)' not in page
    for _, attrs in parse(page).tags:
        assert all(k != "onmouseover" for k, _ in attrs)


# ---------- nothing technical before rest ----------


def test_no_email_id_confidence_or_label_in_visible_text_before_rest(
    tmp_path: Path,
) -> None:
    labels = {
        1: ["Triage/Support", "Triage/Urgent", "Triage/Draft ready"],
        2: ["Triage/Support", "Triage/Suspicious", "Triage/Answer by hand"],
        3: ["Triage/Invoice", "Triage/Urgent"],
        4: ["Triage/Spam", "Triage/Suspicious"],
        5: ["Triage/Other"],
        6: ["Triage/Sales", "Triage/Needs review"],
    }
    outcomes = [
        outcome(
            1,
            confidence=0.91,
            escalation=True,
            urgent_reason="escalation: chargeback",
            draft_text="Sorry about that.",
            draft_id="d1",
            labels=labels[1],
        ),
        outcome(
            2,
            confidence=0.73,
            suspicious=True,
            answer_by_hand=True,
            injection_reason="planted instructions",
            labels=labels[2],
        ),
        outcome(
            3,
            category=Category.INVOICE,
            confidence=0.98,
            overdue=True,
            urgent_reason="invoice overdue since 2020-01-02",
            due_date=date(2020, 1, 2),
            amount_due=742.8,
            currency="EUR",
            labels=labels[3],
        ),
        outcome(
            4,
            category=Category.SPAM,
            confidence=0.64,
            suspicious=True,
            scam_warning=True,
            scam_domains=["refunds-portal[.]example"],
            labels=labels[4],
        ),
        outcome(
            5,
            category=Category.OTHER,
            confidence=0.95,
            event_date=date(2026, 11, 8),
            labels=labels[5],
        ),
        outcome(
            6,
            category=Category.SALES,
            confidence=0.42,
            needs_review=True,
            labels=labels[6],
        ),
    ]
    summary = [SummaryLine(kind="urgent", text="Urgent (2): msg-0001; msg-0003")]
    page = render(tmp_path, outcomes, summary=summary)
    top = visible(before_rest(page))
    for o in outcomes:
        assert o.email_id not in top
        assert f"{o.confidence:.2f}" not in top
    assert "Triage/" not in top
    assert "Triage" not in page.split("<title>")[1].split("</title>")[0]
    bottom = rest(page)
    for o in outcomes:
        assert o.email_id in bottom
        assert f"{o.confidence:.2f}" in bottom
        for label in o.labels:
            assert label in bottom
    assert "claude-test-model" in bottom
    assert "Urgent (2): msg-0001; msg-0003" in bottom


# ---------- everything else count ----------


def test_everything_else_count_is_spam_other_read_review_failed(
    tmp_path: Path,
) -> None:
    outcomes = [
        outcome(1, category=Category.SPAM, marked_read=True),
        outcome(2, category=Category.SPAM, marked_read=True),
        outcome(3, category=Category.OTHER, marked_read=True),
        outcome(4, category=Category.OTHER, marked_read=False),
        outcome(5, category=Category.OTHER, event_date=date(2026, 11, 8)),
        outcome(6, needs_review=True),
        outcome(7, category=Category.INVOICE, needs_review=True),
        outcome(8, status="failed", error_type="HttpError"),
        outcome(9, status="failed", error_type="TimeoutError", labels=[]),
        outcome(10, draft_text="hi", draft_id="d10"),
        outcome(11, category=Category.INVOICE),
    ]
    page = render(tmp_path, outcomes)
    assert "Everything else <span>7</span>" in page
    bottom = rest(page)
    assert "Spam, marked read <span>2</span>" in bottom
    assert "Other, marked read <span>1</span>" in bottom
    assert "Needs a look <span>2</span>" in bottom
    assert "Failed — retried next run <span>2</span>" in bottom
    numbers = visible(page.split('<div class="numbers">')[1].split("</div></div>")[0])
    assert numbers == "0 Needs you 1 Drafts ready 2 Invoices due 2 Spam"
