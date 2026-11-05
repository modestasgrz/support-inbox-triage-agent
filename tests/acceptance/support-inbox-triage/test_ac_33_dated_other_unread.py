from datetime import date
from pathlib import Path
from types import SimpleNamespace


def test_ac_33_dated_other_unread(h: SimpleNamespace, tmp_path: Path) -> None:
    """AC-33: an 'other' email with a model event date stays unread and keeps the date; without one it is marked read."""
    emails = [h.email(1, subject="Invite"), h.email(2, subject="Notice")]
    client = h.client(
        {
            "Invite": h.parsed(
                h.cls("other", 0.95).model_copy(
                    update={"event_date": date(2026, 11, 8)}
                )
            ),
            "Notice": h.parsed(h.cls("other", 0.95)),
        }
    )
    box, report = h.run(emails, client, tmp_path)
    assert box.labels["m1"] == ["Triage/Other"]
    assert box.labels["m2"] == ["Triage/Other"]
    invite = h.outcome(report, "m1")
    assert "m1" not in box.read and invite.marked_read is False
    assert invite.event_date == date(2026, 11, 8)
    assert "m2" in box.read
