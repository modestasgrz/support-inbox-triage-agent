from pathlib import Path
from types import SimpleNamespace

FAQ = "Lumenfold FAQ. Returns: https://lumenfold.example/returns"


def test_ac_30_scam_warning(h: SimpleNamespace, tmp_path: Path) -> None:
    """AC-30: suspicious + outside host or bank/card ask → scam warning with defanged domains from code; never a link."""
    emails = [
        h.email(
            1,
            subject="One",
            body="Claim at http://refunds-portal.example/claim or https://lumenfold.example/returns",
        ),
        h.email(2, subject="Two", body="Then please reply with your bank details."),
        h.email(3, subject="Three", body="See https://lumenfold.example/returns"),
        h.email(4, subject="Four", body="Like this? https://other-shop.example/item"),
    ]
    client = h.client(
        {
            "One": h.parsed(h.cls("support", 0.9, injection=True)),
            "Two": h.parsed(h.cls("support", 0.9, injection=True)),
            "Three": h.parsed(h.cls("support", 0.9, injection=True)),
            "Four": h.parsed(h.cls("support", 0.9)),
        },
        {"Four": h.message("We have no such item.")},
    )
    _, report = h.run(emails, client, tmp_path, faq=FAQ)
    one, two, three, four = (h.outcome(report, f"m{i}") for i in range(1, 5))
    assert one.scam_warning is True
    assert one.scam_domains == ["refunds-portal[.]example"]
    assert two.scam_warning is True and two.scam_domains == []
    assert three.scam_warning is False
    assert four.scam_warning is False and four.scam_domains == []

    scam = next(line for line in report.summary if line.kind == "scam")
    assert "m1" in scam.text and "m2" in scam.text
    assert "refunds-portal[.]example" in scam.text
    md = (tmp_path / "report.md").read_text()
    page = (tmp_path / "report.html").read_text()
    for text in (md, page):
        assert "refunds-portal[.]example" in text
        assert "refunds-portal.example" not in text
    assert "<a " not in page
