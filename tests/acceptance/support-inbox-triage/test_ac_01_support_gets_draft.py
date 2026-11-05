from pathlib import Path
from types import SimpleNamespace


def test_ac_01_support_gets_draft(h: SimpleNamespace, tmp_path: Path) -> None:
    """AC-01: a confident support email gets Triage/Support + Triage/Draft ready, stays unread, and one threaded draft with the drafter text."""
    email = h.email(1, subject="Order #1042 not arrived")
    text = "Sorry about the delay. Your order ships today."
    client = h.client(
        {"Order #1042": h.parsed(h.cls("support", 0.9))},
        {"Order #1042": h.message(text)},
    )
    box, report = h.run([email], client, tmp_path)
    assert box.labels["m1"] == ["Triage/Support", "Triage/Draft ready"]
    assert "m1" not in box.read
    assert len(box.drafts) == 1
    draft = box.drafts[0]
    assert (draft.thread_id, draft.to, draft.subject, draft.body) == (
        "t1",
        email.sender,
        "Re: Order #1042 not arrived",
        text,
    )
    out = h.outcome(report, "m1")
    assert out.status == "ok"
    assert out.draft_id is not None
