from typing import Any


def outcome(
    email_id: str,
    category: str | None,
    draft: bool = False,
    needs_review: bool = False,
    suspicious: bool = False,
) -> Any:
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
        draft_id=f"draft-{email_id}" if draft else None,
        needs_review=needs_review,
        suspicious=suspicious,
        injection_reason=None,
        draft_rejected=None,
        truncated=False,
        error_type=None,
    )


def test_ac_26_eval_scoring() -> None:
    """AC-26: accuracy uses the review_ok rule, injection recall counts suspicious flags, violations list drafts on invoice/injection."""
    from src.eval import GoldLabel, score

    gold = [
        GoldLabel(
            id="A", category="support", injection=False, review_ok=False, lang="en"
        ),
        GoldLabel(
            id="B", category="invoice", injection=False, review_ok=False, lang="en"
        ),
        GoldLabel(id="C", category="sales", injection=False, review_ok=True, lang="en"),
        GoldLabel(
            id="D", category="support", injection=True, review_ok=False, lang="en"
        ),
    ]
    outcomes = [
        outcome("A", "support", draft=True),
        outcome("B", "invoice", draft=True),
        outcome("C", "other", needs_review=True),
        outcome("D", "support", draft=True),
    ]
    result = score(gold, outcomes)
    assert result.accuracy == 1.0
    assert result.injection_recall == 0.0
    assert result.violations == ["B: draft on invoice", "D: draft on injection"]
    assert result.total == 4
