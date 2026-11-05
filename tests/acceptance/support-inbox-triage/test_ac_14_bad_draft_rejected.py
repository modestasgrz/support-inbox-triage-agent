from pathlib import Path
from types import SimpleNamespace

import pytest

CASES = [
    ("Please pay at https://evil.example/pay today.", "unknown url"),
    ("   \n ", "empty draft"),
    ("x" * 2001, "too long"),
]


@pytest.mark.parametrize(("text", "rejected"), CASES)
def test_ac_14_bad_draft_rejected(
    h: SimpleNamespace, tmp_path: Path, text: str, rejected: str
) -> None:
    """AC-14: a draft with an unknown URL, empty, or over 2,000 chars is not created; category + Needs review; draft_rejected says why."""
    client = h.client(
        {"Subject 1": h.parsed(h.cls("support", 0.9))}, {"Subject 1": h.message(text)}
    )
    box, report = h.run([h.email(1)], client, tmp_path)
    assert box.drafts == []
    assert box.labels["m1"] == ["Triage/Support", "Triage/Needs review"]
    assert h.outcome(report, "m1").draft_rejected == rejected


def test_ac_14_known_urls_allowed(h: SimpleNamespace, tmp_path: Path) -> None:
    """AC-14: a URL from the email body or the FAQ is allowed in a draft."""
    email = h.email(1, body="Is https://shop.lumenfold.example/order/77 right?")
    faq = "Returns: https://lumenfold.example/returns"
    text = "Yes, https://shop.lumenfold.example/order/77 is right. Returns: https://lumenfold.example/returns"
    client = h.client(
        {"Subject 1": h.parsed(h.cls("support", 0.9))}, {"Subject 1": h.message(text)}
    )
    box, _ = h.run([email], client, tmp_path, faq=faq)
    assert len(box.drafts) == 1
