from pathlib import Path
from types import SimpleNamespace


def test_ac_19_report_html_escaped(h: SimpleNamespace, tmp_path: Path) -> None:
    """AC-19: model text in report.html is HTML-escaped."""
    client = h.client(
        {
            "Subject 1": h.parsed(
                h.cls(
                    "invoice",
                    0.95,
                    reason="<script>alert(1)</script>",
                    evidence_quote="<img src=x onerror=alert(1)>",
                )
            )
        }
    )
    h.run([h.email(1)], client, tmp_path)
    html = (tmp_path / "report.html").read_text()
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)" not in html
    assert "<img src=x" not in html
