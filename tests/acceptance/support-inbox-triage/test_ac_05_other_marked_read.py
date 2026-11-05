from pathlib import Path
from types import SimpleNamespace


def test_ac_05_other_marked_read(h: SimpleNamespace, tmp_path: Path) -> None:
    """AC-05: a confident "other" email gets only Triage/Other, is marked read, no draft."""
    client = h.client({"Subject 1": h.parsed(h.cls("other", 0.95))})
    box, _ = h.run([h.email(1)], client, tmp_path)
    assert box.labels["m1"] == ["Triage/Other"]
    assert "m1" in box.read
    assert box.drafts == []
