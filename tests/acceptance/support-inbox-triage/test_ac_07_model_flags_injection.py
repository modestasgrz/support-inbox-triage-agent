from pathlib import Path
from types import SimpleNamespace


def test_ac_07_model_flags_injection(h: SimpleNamespace, tmp_path: Path) -> None:
    """AC-07: injection_suspected from the model → category + Suspicious, unread, no draft, drafter never called."""
    client = h.client(
        {
            "Subject 1": h.parsed(
                h.cls(
                    "support",
                    0.9,
                    injection=True,
                    injection_reason="asks to change rules",
                )
            )
        }
    )
    box, report = h.run([h.email(1)], client, tmp_path)
    assert box.labels["m1"] == ["Triage/Support", "Triage/Suspicious"]
    assert "m1" not in box.read
    assert box.drafts == []
    client.beta.messages.create.assert_not_called()
    assert h.outcome(report, "m1").suspicious is True
