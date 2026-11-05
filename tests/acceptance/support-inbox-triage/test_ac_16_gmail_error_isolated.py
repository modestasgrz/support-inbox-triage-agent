from pathlib import Path
from types import SimpleNamespace


def test_ac_16_gmail_error_isolated(h: SimpleNamespace, tmp_path: Path) -> None:
    """AC-16: a mailbox error on one email → that outcome failed with error_type; the others finish and reports are written."""
    from src.mailbox import FakeMailbox

    emails = [
        h.email(1, subject="First"),
        h.email(2, subject="Second"),
        h.email(3, subject="Third"),
    ]

    class Flaky(FakeMailbox):
        def add_labels(self, email_id: str, names: list[str]) -> None:
            if email_id == "m2":
                raise RuntimeError("gmail down")
            super().add_labels(email_id, names)

    client = h.client(
        {s: h.parsed(h.cls("spam", 0.95)) for s in ("First", "Second", "Third")}
    )
    box, report = h.run(emails, client, tmp_path, mailbox=Flaky(emails))
    assert box.labels["m1"] == ["Triage/Spam"]
    assert box.labels["m3"] == ["Triage/Spam"]
    failed = h.outcome(report, "m2")
    assert (failed.status, failed.error_type) == ("failed", "RuntimeError")
    for name in ("report.json", "report.md", "report.html"):
        assert (tmp_path / name).exists()
