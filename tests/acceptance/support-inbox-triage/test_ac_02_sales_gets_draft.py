from pathlib import Path
from types import SimpleNamespace


def test_ac_02_sales_gets_draft(h: SimpleNamespace, tmp_path: Path) -> None:
    """AC-02: a sales email at 0.85 gets Triage/Sales + Triage/Draft ready, stays unread, one draft; an existing "Re:" is not doubled."""
    emails = [
        h.email(1, subject="Wholesale price list?"),
        h.email(2, subject="RE: Corporate gift boxes"),
    ]
    client = h.client(
        {
            "Wholesale": h.parsed(h.cls("sales", 0.85)),
            "Corporate gift": h.parsed(h.cls("sales", 0.85)),
        },
        {
            "Wholesale": h.message(
                "Thanks, our wholesale sheet is attached in the next mail."
            ),
            "Corporate gift": h.message("Happy to help with gift boxes."),
        },
    )
    box, _ = h.run(emails, client, tmp_path)
    assert box.labels["m1"] == ["Triage/Sales", "Triage/Draft ready"]
    assert "m1" not in box.read
    subjects = {d.email_id: d.subject for d in box.drafts}
    assert subjects == {
        "m1": "Re: Wholesale price list?",
        "m2": "RE: Corporate gift boxes",
    }
