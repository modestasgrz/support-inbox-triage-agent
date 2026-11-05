from pathlib import Path
from types import SimpleNamespace


def test_ac_04_spam_marked_read(h: SimpleNamespace, tmp_path: Path) -> None:
    """AC-04: confident spam gets only Triage/Spam, is marked read, no draft."""
    client = h.client({"Subject 1": h.parsed(h.cls("spam", 0.95))})
    box, _ = h.run([h.email(1)], client, tmp_path)
    assert box.labels["m1"] == ["Triage/Spam"]
    assert "m1" in box.read
    assert box.drafts == []
