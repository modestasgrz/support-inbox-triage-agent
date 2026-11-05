from pathlib import Path
from types import SimpleNamespace

from pydantic import ValidationError


def test_ac_13_invalid_output_review(h: SimpleNamespace, tmp_path: Path) -> None:
    """AC-13: parsed_output None or a ValidationError → only Needs review, no draft, reason "invalid model output"; raw text never in reports."""
    raw = "RAW_MODEL_TEXT_91c2"
    broken = h.parsed(None)
    broken.content = [SimpleNamespace(type="text", text=raw)]
    error = ValidationError.from_exception_data(
        "Classification", [{"type": "missing", "loc": ("category",), "input": raw}]
    )
    client = h.client({"Subject 1": broken, "Subject 2": error})
    box, report = h.run([h.email(1), h.email(2)], client, tmp_path)
    for email_id in ("m1", "m2"):
        assert box.labels[email_id] == ["Triage/Needs review"]
        assert h.outcome(report, email_id).reason == "invalid model output"
    assert box.drafts == []
    for name in ("report.json", "report.md", "report.html"):
        assert raw not in (tmp_path / name).read_text()
