import json
from datetime import date
from types import SimpleNamespace
from typing import Any


def outcome(email_id: str, category: str, **fields: Any) -> Any:
    from src.models import Outcome

    return Outcome(
        email_id=email_id,
        status="ok",
        category=category,
        confidence=0.9,
        reason="r",
        evidence_quote="q",
        labels=[],
        marked_read=False,
        draft_id=None,
        needs_review=False,
        suspicious=False,
        injection_reason=None,
        draft_rejected=None,
        truncated=False,
        error_type=None,
        **fields,
    )


def test_ac_35_eval_flags(h: SimpleNamespace) -> None:
    """AC-35: eval scores escalation recall and invoice fields (due date + amount) against gold; gold carries them."""
    from src.eval import GoldLabel, score

    base = {"injection": False, "review_ok": False, "lang": "en"}
    gold = [
        GoldLabel(id="A", category="support", escalation=True, **base),
        GoldLabel(
            id="B",
            category="invoice",
            due_date=date(2026, 10, 30),
            amount_due=742.80,
            **base,
        ),
        GoldLabel(
            id="C",
            category="invoice",
            due_date=date(2026, 11, 20),
            amount_due=1184.00,
            **base,
        ),
        GoldLabel(id="D", category="support", **base),
    ]
    outcomes = [
        outcome("A", "support", escalation=False),
        outcome("B", "invoice", due_date=date(2026, 10, 30), amount_due=742.8),
        outcome("C", "invoice", due_date=date(2026, 11, 21), amount_due=1184.0),
        outcome("D", "support", escalation=True),
    ]
    result = score(gold, outcomes)
    assert result.escalation_recall == 0.0
    assert result.invoice_fields_accuracy == 0.5
    assert result.flag_errors == [
        "A: escalation missed",
        "C: invoice fields",
        "D: unexpected escalation",
    ]

    labels = [
        GoldLabel.model_validate(g)
        for g in json.loads((h.repo / "data" / "gold.json").read_text())
    ]
    assert any(g.escalation for g in labels)
    assert all(g.amount_due is not None for g in labels if g.category == "invoice")
