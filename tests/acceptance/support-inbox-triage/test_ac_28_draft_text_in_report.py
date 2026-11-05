from pathlib import Path
from types import SimpleNamespace

DRAFT = "Hi <Ana>,\nIt ships today.\n— Lumenfold Candle Co. support"


def test_ac_28_draft_text_in_report(h: SimpleNamespace, tmp_path: Path) -> None:
    """AC-28: the full draft text is in the outcome, the fake mailbox and all three report files; rejected drafts are not."""
    from src.models import RunReport

    emails = [
        h.email(1, subject="Good"),
        h.email(2, subject="Bad"),
        h.email(3, subject="Bill"),
    ]
    client = h.client(
        {
            "Good": h.parsed(h.cls("support", 0.9)),
            "Bad": h.parsed(h.cls("support", 0.9)),
            "Bill": h.parsed(h.cls("invoice", 0.95)),
        },
        {
            "Good": h.message(DRAFT),
            "Bad": h.message("Pay here: https://evil.example/pay"),
        },
    )
    box, report = h.run(emails, client, tmp_path)
    assert h.outcome(report, "m1").draft_text == DRAFT
    assert box.drafts[0].body == DRAFT
    assert h.outcome(report, "m2").draft_text is None
    assert h.outcome(report, "m3").draft_text is None

    saved = RunReport.model_validate_json((tmp_path / "report.json").read_text())
    assert h.outcome(saved, "m1").draft_text == DRAFT
    md = (tmp_path / "report.md").read_text()
    row = next(line for line in md.splitlines() if line.startswith("| m1 "))
    assert "It ships today." in row
    page = (tmp_path / "report.html").read_text()
    assert "Hi &lt;Ana&gt;," in page and "<Ana>" not in page
    for name in ("report.json", "report.md", "report.html"):
        assert "evil.example" not in (tmp_path / name).read_text()
