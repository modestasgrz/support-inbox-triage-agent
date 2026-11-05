"""Shared builders for the support-inbox-triage acceptance tests. src imports stay inside functions, so each test fails on its own while the feature is missing."""

import os
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key in list(os.environ):
        if key.startswith("TRIAGE_"):
            monkeypatch.delenv(key)
    yield


def make_email(i: int, **fields: Any) -> Any:
    from src.models import Email

    data: dict[str, Any] = {
        "id": f"m{i}",
        "thread_id": f"t{i}",
        "rfc822_message_id": f"<m{i}@mail.customer.example>",
        "sender": f"Customer {i} <c{i}@customer.example>",
        "subject": f"Subject {i}",
        "body": "Hello, I have a question about my order.",
        "received_at": datetime(2026, 11, 1, 9, 0, tzinfo=UTC),
        "label_names": [],
    }
    data.update(fields)
    return Email(**data)


def make_cls(
    category: str = "support",
    confidence: float = 0.9,
    injection: bool = False,
    **fields: Any,
) -> Any:
    from src.models import Classification

    return Classification(
        category=category,
        confidence=confidence,
        reason=fields.get("reason", "test reason"),
        evidence_quote=fields.get("evidence_quote", "test quote"),
        injection_suspected=injection,
        injection_reason=fields.get("injection_reason"),
    )


def parsed(result: Any, stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(stop_reason=stop_reason, parsed_output=result, content=[])


def message(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)]
    )


def make_client(
    parse: dict[str, Any], create: dict[str, Any] | None = None
) -> MagicMock:
    """parse/create map an email subject to the response (or exception) for the call whose user message contains it."""

    def pick(table: dict[str, Any]) -> Callable[..., Any]:
        def respond(**kwargs: Any) -> Any:
            content = kwargs["messages"][0]["content"]
            for subject, value in table.items():
                if subject in content:
                    if isinstance(value, BaseException):
                        raise value
                    return value
            raise AssertionError("no fake response for this email")

        return respond

    client = MagicMock()
    client.beta.messages.parse.side_effect = pick(parse)
    client.beta.messages.create.side_effect = pick(create or {})
    return client


def run(
    emails: list[Any],
    client: MagicMock,
    out_dir: Path,
    faq: str = "Lumenfold FAQ: shipping 3-5 days.",
    mailbox: Any = None,
) -> tuple[Any, Any]:
    from src.classifier import Classifier
    from src.config import Settings
    from src.drafter import Drafter
    from src.mailbox import FakeMailbox
    from src.pipeline import run_triage

    settings = Settings()
    box = mailbox or FakeMailbox(emails)
    report = run_triage(
        box,
        Classifier(client, settings),
        Drafter(client, settings, faq),
        faq,
        settings,
        out_dir,
    )
    return box, report


def outcome(report: Any, email_id: str) -> Any:
    return next(o for o in report.outcomes if o.email_id == email_id)


@pytest.fixture
def h() -> SimpleNamespace:
    return SimpleNamespace(
        email=make_email,
        cls=make_cls,
        parsed=parsed,
        message=message,
        client=make_client,
        run=run,
        outcome=outcome,
        repo=REPO,
    )
