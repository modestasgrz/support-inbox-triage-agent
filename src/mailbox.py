import json
from pathlib import Path
from typing import Protocol

from src.models import Draft, Email


def reply_subject(subject: str) -> str:
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"


class Mailbox(Protocol):
    def fetch_inbox(self) -> list[Email]: ...

    def add_labels(self, email_id: str, names: list[str]) -> None: ...

    def mark_read(self, email_id: str) -> None: ...

    def create_draft(self, email: Email, body: str) -> str: ...


class FakeMailbox:
    def __init__(self, emails: list[Email]) -> None:
        self.emails = emails
        self.labels: dict[str, list[str]] = {}
        self.read: set[str] = set()
        self.drafts: list[Draft] = []

    @classmethod
    def from_json(cls, path: Path) -> "FakeMailbox":
        return cls([Email.model_validate(e) for e in json.loads(path.read_text())])

    def fetch_inbox(self) -> list[Email]:
        return list(self.emails)

    def add_labels(self, email_id: str, names: list[str]) -> None:
        self.labels.setdefault(email_id, []).extend(names)

    def mark_read(self, email_id: str) -> None:
        self.read.add(email_id)

    def create_draft(self, email: Email, body: str) -> str:
        self.drafts.append(
            Draft(
                email_id=email.id,
                thread_id=email.thread_id,
                to=email.sender,
                subject=reply_subject(email.subject),
                body=body,
            )
        )
        return f"draft-{email.id}"
