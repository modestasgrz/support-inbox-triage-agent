from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx2


def test_ac_18_report_files(h: SimpleNamespace, tmp_path: Path) -> None:
    """AC-18: report.json (RunReport), report.md and report.html with evidence per outcome and exact counts."""
    from src.models import RunReport

    error = anthropic.APIConnectionError(
        request=httpx2.Request("POST", "https://api.example/v1/messages")
    )
    emails = [
        h.email(i, subject=s)
        for i, s in enumerate(["Sup", "Inv", "Spm", "Ref", "Err"], start=1)
    ]
    client = h.client(
        {
            "Sup": h.parsed(
                h.cls(
                    "support",
                    0.9,
                    reason="asks where order is",
                    evidence_quote="where is my order",
                )
            ),
            "Inv": h.parsed(
                h.cls(
                    "invoice", 0.95, reason="supplier bill", evidence_quote="amount due"
                )
            ),
            "Spm": h.parsed(
                h.cls(
                    "spam",
                    0.95,
                    reason="crypto promo",
                    evidence_quote="double your coins",
                )
            ),
            "Ref": h.parsed(None, stop_reason="refusal"),
            "Err": error,
        },
        {"Sup": h.message("It ships today.")},
    )
    _, _ = h.run(emails, client, tmp_path)
    report = RunReport.model_validate_json((tmp_path / "report.json").read_text())
    assert len(report.outcomes) == 5
    sup = h.outcome(report, "m1")
    assert (sup.category, sup.confidence, sup.reason, sup.evidence_quote) == (
        "support",
        0.9,
        "asks where order is",
        "where is my order",
    )
    counts = report.counts.model_dump()
    assert counts == {
        "support": 1,
        "sales": 0,
        "invoice": 1,
        "spam": 1,
        "other": 0,
        "needs_review": 1,
        "suspicious": 0,
        "failed": 1,
        "drafts": 1,
        "skipped": 0,
        "total": 5,
    }
    md = (tmp_path / "report.md").read_text()
    html = (tmp_path / "report.html").read_text()
    for o in report.outcomes:
        assert o.email_id in md and o.email_id in html
        for label in o.labels:
            assert label in md and label in html
