from pathlib import Path
from types import SimpleNamespace


def test_ac_03_invoice_left_unread(h: SimpleNamespace, tmp_path: Path) -> None:
    """AC-03: an invoice gets only Triage/Invoice, stays unread, no draft, the drafter model is never called."""
    client = h.client({"Subject 1": h.parsed(h.cls("invoice", 0.95))})
    box, _ = h.run([h.email(1)], client, tmp_path)
    assert box.labels["m1"] == ["Triage/Invoice"]
    assert "m1" not in box.read
    assert box.drafts == []
    client.beta.messages.create.assert_not_called()
