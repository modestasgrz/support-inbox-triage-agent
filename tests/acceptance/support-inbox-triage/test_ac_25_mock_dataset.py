import json
from collections import Counter
from email.utils import parseaddr
from types import SimpleNamespace


def test_ac_25_mock_dataset(h: SimpleNamespace) -> None:
    """AC-25: 50 unique mock emails from .example senders, one gold label each, with the required mix, and an FAQ covering shipping, returns, wholesale."""
    from src.eval import GoldLabel
    from src.models import Email

    data = h.repo / "data"
    emails = [
        Email.model_validate(e) for e in json.loads((data / "emails.json").read_text())
    ]
    gold = [
        GoldLabel.model_validate(g)
        for g in json.loads((data / "gold.json").read_text())
    ]
    ids = [e.id for e in emails]
    assert len(emails) == 50 and len(set(ids)) == 50
    for e in emails:
        assert parseaddr(e.sender)[1].endswith(".example"), e.id
    assert sorted(g.id for g in gold) == sorted(ids)
    cats = Counter(g.category for g in gold)
    assert sum(g.injection for g in gold) >= 3
    assert cats["invoice"] >= 2
    assert sum(g.lang != "en" for g in gold) >= 3
    assert sum(g.review_ok for g in gold) >= 5
    for cat in ("support", "sales", "spam", "other"):
        assert cats[cat] >= 5, cat
    faq = (data / "faq.md").read_text().lower()
    assert faq.strip()
    for word in ("shipping", "returns", "wholesale"):
        assert word in faq
