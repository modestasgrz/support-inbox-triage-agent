from datetime import date
from pathlib import Path
from types import SimpleNamespace


def test_ac_34_morning_summary(h: SimpleNamespace, tmp_path: Path) -> None:
    """AC-34: seven summary lines in a fixed order, at the top of report.md and report.html."""
    emails = [
        h.email(1, subject="S1"),
        h.email(2, subject="S2"),
        h.email(3, subject="S3"),
        h.email(4, subject="S4", body="Refund at http://refunds-portal.example now"),
        h.email(5, subject="S5"),
        h.email(6, subject="S6"),
        h.email(7, subject="S7"),
    ]
    client = h.client(
        {
            "S1": h.parsed(
                h.cls("support", 0.9).model_copy(
                    update={"escalation": True, "escalation_quote": "chargeback"}
                )
            ),
            "S2": h.parsed(
                h.cls("invoice", 0.95).model_copy(
                    update={
                        "due_date": date(2020, 1, 2),
                        "amount_due": 742.80,
                        "currency": "EUR",
                    }
                )
            ),
            "S3": h.parsed(
                h.cls("invoice", 0.95).model_copy(
                    update={
                        "due_date": date(2999, 12, 31),
                        "amount_due": 1184.00,
                        "currency": "EUR",
                    }
                )
            ),
            "S4": h.parsed(h.cls("support", 0.9, injection=True)),
            "S5": h.parsed(
                h.cls("other", 0.95).model_copy(
                    update={"event_date": date(2026, 11, 8)}
                )
            ),
            "S6": h.parsed(h.cls("spam", 0.95)),
            "S7": h.parsed(h.cls("support", 0.9)),
        },
        {"S1": h.message("Sorry, on it."), "S7": h.message("Happy to help.")},
    )
    _, report = h.run(emails, client, tmp_path)
    lines = {line.kind: line.text for line in report.summary}
    assert [line.kind for line in report.summary] == [
        "urgent",
        "answer_by_hand",
        "scam",
        "invoices",
        "dated",
        "drafts",
        "rest",
    ]
    assert lines["urgent"].startswith("Urgent (2):")
    assert "m1" in lines["urgent"] and "m2" in lines["urgent"]
    assert lines["answer_by_hand"].startswith("Answer by hand (1):")
    assert "m4" in lines["answer_by_hand"]
    assert lines["scam"].startswith("SCAM WARNING (1):")
    assert "refunds-portal[.]example" in lines["scam"]
    assert lines["invoices"] == "Invoices (2): EUR 1,926.80 total, 1 overdue"
    assert lines["dated"].startswith("Has a date (1):")
    assert "2026-11-08" in lines["dated"]
    assert lines["drafts"] == "Drafts ready to review: 2"
    assert (
        lines["rest"]
        == "Rest: 1 spam, 0 other marked read, 0 need review, 0 failed, 0 skipped"
    )

    md = (tmp_path / "report.md").read_text()
    page = (tmp_path / "report.html").read_text()
    table_md = md.index("| Email |")
    table_html = page.index("<table")
    for line in report.summary:
        assert -1 < md.find(line.text) < table_md
    for text in ("Urgent (2):", "SCAM WARNING (1):", "Drafts ready to review: 2"):
        assert -1 < page.find(text) < table_html

    quiet = h.client({"Calm": h.parsed(h.cls("spam", 0.95))})
    _, calm = h.run([h.email(8, subject="Calm")], quiet, tmp_path / "calm")
    texts = [line.text for line in calm.summary]
    for empty in (
        "Urgent: none",
        "Answer by hand: none",
        "Scam warnings: none",
        "Invoices: none",
        "Has a date: none",
    ):
        assert empty in texts
