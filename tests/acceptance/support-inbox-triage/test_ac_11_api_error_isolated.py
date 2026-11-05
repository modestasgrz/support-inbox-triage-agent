from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx2


def test_ac_11_api_error_isolated(h: SimpleNamespace, tmp_path: Path) -> None:
    """AC-11: an API error on one email leaves it unlabelled and unread with status failed; the others are processed."""
    error = anthropic.APIConnectionError(
        request=httpx2.Request("POST", "https://api.example/v1/messages")
    )
    emails = [
        h.email(1, subject="First"),
        h.email(2, subject="Second"),
        h.email(3, subject="Third"),
    ]
    client = h.client(
        {
            "First": h.parsed(h.cls("spam", 0.95)),
            "Second": error,
            "Third": h.parsed(h.cls("support", 0.9)),
        },
        {"Third": h.message("Answer.")},
    )
    box, report = h.run(emails, client, tmp_path)
    assert box.labels["m1"] == ["Triage/Spam"]
    assert box.labels["m3"] == ["Triage/Support", "Triage/Draft ready"]
    assert box.labels.get("m2", []) == []
    assert "m2" not in box.read
    failed = h.outcome(report, "m2")
    assert (failed.status, failed.error_type, failed.category) == (
        "failed",
        "APIConnectionError",
        None,
    )
    assert report.counts.failed == 1
