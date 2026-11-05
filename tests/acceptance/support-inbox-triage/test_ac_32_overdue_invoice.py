from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any


def invoice(h: SimpleNamespace, due: date | None, **fields: Any) -> Any:
    return h.cls("invoice", 0.95).model_copy(update={"due_date": due, **fields})


def test_ac_32_overdue_invoice(h: SimpleNamespace, tmp_path: Path) -> None:
    """AC-32: the model extracts the due date; code compares it with the run date and adds Triage/Urgent when overdue."""
    today = datetime.now(UTC).date()
    emails = [
        h.email(i, subject=s)
        for i, s in enumerate(["Old", "Future", "Today", "Undated", "Planted"], start=1)
    ]
    client = h.client(
        {
            "Old": h.parsed(
                invoice(h, date(2020, 1, 2), amount_due=742.80, currency="EUR")
            ),
            "Future": h.parsed(invoice(h, date(2999, 12, 31))),
            "Today": h.parsed(invoice(h, today)),
            "Undated": h.parsed(invoice(h, None)),
            "Planted": h.parsed(invoice(h, date(2020, 1, 2), injection_suspected=True)),
        }
    )
    box, report = h.run(emails, client, tmp_path)
    assert box.labels["m1"] == ["Triage/Invoice", "Triage/Urgent"]
    old = h.outcome(report, "m1")
    assert old.overdue is True
    assert old.urgent_reason == "invoice overdue since 2020-01-02"
    assert old.due_date == date(2020, 1, 2)
    assert old.amount_due == 742.80 and old.currency == "EUR"
    for i in (2, 3, 4):
        assert box.labels[f"m{i}"] == ["Triage/Invoice"]
        assert h.outcome(report, f"m{i}").overdue is False
    assert box.labels["m5"] == ["Triage/Invoice", "Triage/Suspicious"]
    assert h.outcome(report, "m5").overdue is False
    assert box.read == set() and box.drafts == []

    for call in client.beta.messages.parse.call_args_list:
        content = call.kwargs["messages"][0]["content"]
        closing = content.rindex("</email-")
        assert "Received: 2026-11-01" in content[closing:]
