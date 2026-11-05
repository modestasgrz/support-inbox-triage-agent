from pathlib import Path
from types import SimpleNamespace


def test_ac_06_low_confidence_review(h: SimpleNamespace, tmp_path: Path) -> None:
    """AC-06: confidence 0.69 → category + Needs review, unread, no draft; 0.70 → draft; spam at 0.50 → Needs review and unread."""
    emails = [
        h.email(1, subject="Low one"),
        h.email(2, subject="Edge one"),
        h.email(3, subject="Maybe spam"),
    ]
    client = h.client(
        {
            "Low one": h.parsed(h.cls("support", 0.69)),
            "Edge one": h.parsed(h.cls("support", 0.70)),
            "Maybe spam": h.parsed(h.cls("spam", 0.50)),
        },
        {"Edge one": h.message("Here is the answer.")},
    )
    box, report = h.run(emails, client, tmp_path)
    assert box.labels["m1"] == ["Triage/Support", "Triage/Needs review"]
    assert "m1" not in box.read
    assert h.outcome(report, "m1").needs_review is True
    assert box.labels["m2"] == ["Triage/Support", "Triage/Draft ready"]
    assert [d.email_id for d in box.drafts] == ["m2"]
    assert box.labels["m3"] == ["Triage/Spam", "Triage/Needs review"]
    assert "m3" not in box.read
