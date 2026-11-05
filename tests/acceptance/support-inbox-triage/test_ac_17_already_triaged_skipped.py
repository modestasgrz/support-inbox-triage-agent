from pathlib import Path
from types import SimpleNamespace


def test_ac_17_already_triaged_skipped(h: SimpleNamespace, tmp_path: Path) -> None:
    """AC-17: an email with a Triage/* label is skipped (not classified, no new labels, counted); unrelated labels don't skip."""
    emails = [
        h.email(1, subject="Done before", label_names=["Triage/Spam"]),
        h.email(2, subject="Personal one", label_names=["Personal"]),
        h.email(3, subject="Fresh one"),
    ]
    client = h.client(
        {
            "Personal one": h.parsed(h.cls("other", 0.95)),
            "Fresh one": h.parsed(h.cls("other", 0.95)),
        }
    )
    box, report = h.run(emails, client, tmp_path)
    assert client.beta.messages.parse.call_count == 2
    assert box.labels.get("m1", []) == []
    assert box.labels["m2"] == ["Triage/Other"]
    assert report.counts.skipped == 1
    assert {o.email_id for o in report.outcomes} == {"m2", "m3"}
