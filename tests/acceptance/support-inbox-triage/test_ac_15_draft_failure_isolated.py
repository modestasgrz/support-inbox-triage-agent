from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx2


def test_ac_15_draft_failure_isolated(h: SimpleNamespace, tmp_path: Path) -> None:
    """AC-15: a drafter API error → category + Needs review, draft_rejected names the error; the next email is drafted."""
    request = httpx2.Request("POST", "https://api.example/v1/messages")
    error = anthropic.InternalServerError(
        "server down", response=httpx2.Response(500, request=request), body=None
    )
    emails = [h.email(1, subject="First"), h.email(2, subject="Second")]
    client = h.client(
        {
            "First": h.parsed(h.cls("support", 0.9)),
            "Second": h.parsed(h.cls("support", 0.9)),
        },
        {"First": error, "Second": h.message("Answer.")},
    )
    box, report = h.run(emails, client, tmp_path)
    assert box.labels["m1"] == ["Triage/Support", "Triage/Needs review"]
    assert h.outcome(report, "m1").draft_rejected == "draft failed: InternalServerError"
    assert [d.email_id for d in box.drafts] == ["m2"]
