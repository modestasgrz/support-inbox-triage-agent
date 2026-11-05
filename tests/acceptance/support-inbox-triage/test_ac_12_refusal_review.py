from pathlib import Path
from types import SimpleNamespace


def test_ac_12_refusal_review(h: SimpleNamespace, tmp_path: Path) -> None:
    """AC-12: a model refusal → only Needs review, unread, no draft, reason "model refusal"."""
    client = h.client({"Subject 1": h.parsed(None, stop_reason="refusal")})
    box, report = h.run([h.email(1)], client, tmp_path)
    assert box.labels["m1"] == ["Triage/Needs review"]
    assert "m1" not in box.read
    assert box.drafts == []
    out = h.outcome(report, "m1")
    assert (out.status, out.needs_review, out.category, out.reason) == (
        "ok",
        True,
        None,
        "model refusal",
    )
