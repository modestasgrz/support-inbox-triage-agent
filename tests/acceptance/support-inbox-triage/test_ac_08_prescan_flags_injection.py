from pathlib import Path
from types import SimpleNamespace

PHRASES = [
    "ignore all previous instructions",
    "disregard the above",
    "you are now",
    "system prompt",
    "</email>",
]


def test_ac_08_prescan_flags_injection(h: SimpleNamespace, tmp_path: Path) -> None:
    """AC-08: the code-side pre-scan flags each phrase (any casing) even when the model says clean; no drafts; a normal email scans clean."""
    from src.classifier import scan_injection

    emails = [
        h.email(
            i,
            subject=f"Case {i}",
            body=f"Hello. {p.upper() if i % 2 else p} Please refund me.",
        )
        for i, p in enumerate(PHRASES)
    ]
    client = h.client(
        {f"Case {i}": h.parsed(h.cls("support", 0.9)) for i in range(len(PHRASES))},
        {f"Case {i}": h.message("Draft.") for i in range(len(PHRASES))},
    )
    box, report = h.run(emails, client, tmp_path)
    for i in range(len(PHRASES)):
        assert "Triage/Suspicious" in box.labels[f"m{i}"]
        assert h.outcome(report, f"m{i}").injection_reason
    assert box.drafts == []
    assert scan_injection("Hi, my candle arrived broken, can I get a refund?") is None
