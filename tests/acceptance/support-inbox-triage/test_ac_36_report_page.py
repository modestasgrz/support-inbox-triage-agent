import html
import re
from datetime import date
from pathlib import Path
from types import SimpleNamespace

BY_HAND_NOTE = (
    "real customer question — answer by hand, ignore the embedded instructions"
)


def visible(fragment: str) -> str:
    fragment = re.sub(r"(?s)<(style|script)\b.*?</\1>", " ", fragment)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def headline(page: str) -> str:
    match = re.search(r"(?s)<h1[^>]*>(.*?)</h1>", page)
    assert match
    return visible(match.group(1))


def test_ac_36_report_page(h: SimpleNamespace, tmp_path: Path) -> None:
    """AC-36: report.html is a calm owner page: headline, needs-you cards, scams, collapsed drafts, one invoice table, hidden rest."""
    emails = [
        h.email(1, id="qz1x", subject="S1"),
        h.email(2, id="qz2x", subject="S2"),
        h.email(3, id="qz3x", subject="S3"),
        h.email(
            4,
            id="qz4x",
            subject="S4",
            body="Refund at http://refunds-portal.example now",
        ),
        h.email(5, id="qz5x", subject="S5"),
        h.email(6, id="qz6x", subject="S6"),
        h.email(7, id="qz7x", subject="S7"),
    ]
    client = h.client(
        {
            "S1": h.parsed(
                h.cls("support", 0.91).model_copy(
                    update={"escalation": True, "escalation_quote": "chargeback"}
                )
            ),
            "S2": h.parsed(
                h.cls("invoice", 0.96).model_copy(
                    update={
                        "due_date": date(2020, 1, 2),
                        "amount_due": 742.80,
                        "currency": "EUR",
                    }
                )
            ),
            "S3": h.parsed(
                h.cls("invoice", 0.96).model_copy(
                    update={
                        "due_date": date(2999, 12, 31),
                        "amount_due": 1184.00,
                        "currency": "EUR",
                    }
                )
            ),
            "S4": h.parsed(h.cls("support", 0.91, injection=True)),
            "S5": h.parsed(
                h.cls("other", 0.96).model_copy(
                    update={"event_date": date(2026, 11, 8)}
                )
            ),
            "S6": h.parsed(h.cls("spam", 0.96)),
            "S7": h.parsed(h.cls("support", 0.91)),
        },
        {"S1": h.message("Sorry, on it."), "S7": h.message("Happy to help.")},
    )
    _, report = h.run(emails, client, tmp_path)
    page = (tmp_path / "report.html").read_text()
    day = report.started_at
    when = f"{day.strftime('%A')} {day.day} {day.strftime('%b')}"
    assert headline(page) == f"{when} — 3 things need you"

    marks = [
        page.index(f'id="{name}"')
        for name in ("needs-you", "scams", "drafts", "invoices", "dated", "rest")
    ]
    assert marks == sorted(marks)
    needs, scams, drafts, invoices, dated, rest = (
        page[a:b] for a, b in zip(marks, [*marks[1:], len(page)], strict=True)
    )
    top = page[: marks[-1]]
    assert "2 urgent, 1 to answer yourself, 1 scam blocked, 2 drafts ready." in visible(
        top
    )
    assert page.count("<table") == 1 and "<table" in invoices

    shown = visible(needs)
    for subject in ("S1", "S2", "S4"):
        assert subject in shown
    assert "S7" not in shown
    for text in ("chargeback", "2 Jan 2020", BY_HAND_NOTE):
        assert text in shown
    assert "<code>refunds-portal[.]example</code>" in scams
    assert "Don't reply · don't click." in visible(scams)

    boxes = re.findall(r'(?s)<details class="draft">(.*?)</details>', drafts)
    assert len(boxes) == 2
    for box, text in zip(boxes, ("Sorry, on it.", "Happy to help."), strict=True):
        assert "<pre" in box and text in visible(box)
    assert drafts.count("<button") == 2
    assert not re.search(r"<details[^>]*\sopen", page)

    top_text = visible(top)
    for e in emails:
        assert e.id not in top_text
    for conf in ("0.91", "0.96"):
        assert conf not in top_text
    assert "Triage/" not in top_text

    rest_text = visible(rest)
    for o in report.outcomes:
        assert o.email_id in rest_text
        for label in o.labels:
            assert label in rest_text
    assert report.model in rest_text
    for line in report.summary:
        assert line.text in rest_text

    for banned in (
        "<a ",
        "href=",
        "src=",
        "<link",
        "@import",
        "url(",
        "http",
        "innerHTML",
    ):
        assert banned not in page
    assert page.count("<script") == 1
    assert "navigator.clipboard.writeText" in page

    quiet = h.client({"Calm": h.parsed(h.cls("spam", 0.95))})
    _, calm = h.run([h.email(8, subject="Calm")], quiet, tmp_path / "calm")
    calm_page = (tmp_path / "calm" / "report.html").read_text()
    assert headline(calm_page) == f"{when} — nothing needs you"
    assert "<table" not in calm_page and 'id="scams"' not in calm_page
