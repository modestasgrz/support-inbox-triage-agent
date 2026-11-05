from pathlib import Path
from types import SimpleNamespace
from typing import Any

QUOTE = "I'm disputing it with my bank"


def escalated(h: SimpleNamespace, category: str, confidence: float) -> Any:
    return h.cls(category, confidence).model_copy(
        update={"escalation": True, "escalation_quote": QUOTE}
    )


def test_ac_31_escalation_urgent(h: SimpleNamespace, tmp_path: Path) -> None:
    """AC-31: a customer escalation (model boolean + quote) adds Triage/Urgent last for support/sales only."""
    emails = [
        h.email(i, subject=s)
        for i, s in enumerate(["Sup", "Sal", "Low", "Spm", "Calm"], start=1)
    ]
    client = h.client(
        {
            "Sup": h.parsed(escalated(h, "support", 0.9)),
            "Sal": h.parsed(escalated(h, "sales", 0.9)),
            "Low": h.parsed(escalated(h, "support", 0.5)),
            "Spm": h.parsed(escalated(h, "spam", 0.95)),
            "Calm": h.parsed(h.cls("support", 0.9)),
        },
        {
            "Sup": h.message("We are on it."),
            "Sal": h.message("We are on it."),
            "Calm": h.message("We are on it."),
        },
    )
    box, report = h.run(emails, client, tmp_path)
    assert box.labels["m1"] == [
        "Triage/Support",
        "Triage/Draft ready",
        "Triage/Urgent",
    ]
    sup = h.outcome(report, "m1")
    assert sup.escalation is True
    assert QUOTE in sup.urgent_reason
    assert box.labels["m2"] == ["Triage/Sales", "Triage/Draft ready", "Triage/Urgent"]
    assert box.labels["m3"] == [
        "Triage/Support",
        "Triage/Needs review",
        "Triage/Urgent",
    ]
    assert box.labels["m4"] == ["Triage/Spam"]
    assert "m4" in box.read
    spam = h.outcome(report, "m4")
    assert spam.escalation is False and spam.urgent_reason is None
    assert box.labels["m5"] == ["Triage/Support", "Triage/Draft ready"]
