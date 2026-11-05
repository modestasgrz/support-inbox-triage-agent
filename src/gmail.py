import base64
import os
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime
from html.parser import HTMLParser
from typing import Any

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from loguru import logger

from src.config import Settings
from src.mailbox import reply_subject
from src.models import Email

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
RETRIES = 3
MAX_FETCH = 100
LUMENFOLD_ADDRESS = "support@lumenfold.example"


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        self.chunks.append(data)


def _decode(part: dict[str, Any]) -> str:
    data = part.get("body", {}).get("data", "")
    return (
        base64.urlsafe_b64decode(data).decode("utf-8", errors="replace") if data else ""
    )


def _body(payload: dict[str, Any]) -> str:
    mime = payload.get("mimeType", "")
    parts = payload.get("parts")
    if parts:
        if mime == "multipart/alternative":
            for wanted in ("text/plain", "text/html"):
                for part in parts:
                    if part.get("mimeType") == wanted and (text := _body(part)):
                        return text
            return ""
        return "\n\n".join(text for part in parts if (text := _body(part)))
    if mime == "text/plain":
        return _decode(payload)
    if mime == "text/html":
        parser = _TextExtractor()
        parser.feed(_decode(payload))
        return " ".join(" ".join(parser.chunks).split())
    return ""


class GmailMailbox:
    def __init__(self, service: Any) -> None:
        self._users = service.users()
        self._label_ids: dict[str, str] = {}
        self.unreadable: list[str] = []

    def _labels(self) -> dict[str, str]:
        if not self._label_ids:
            result = self._users.labels().list(userId="me").execute(num_retries=RETRIES)
            self._label_ids = {
                label["name"]: label["id"] for label in result.get("labels", [])
            }
        return self._label_ids

    def fetch_inbox(self) -> list[Email]:
        result = (
            self._users.messages()
            .list(userId="me", q="in:inbox", maxResults=MAX_FETCH)
            .execute(num_retries=RETRIES)
        )
        names = {label_id: name for name, label_id in self._labels().items()}
        emails = []
        self.unreadable = []
        for ref in result.get("messages", []):
            try:
                msg = (
                    self._users.messages()
                    .get(userId="me", id=ref["id"], format="full")
                    .execute(num_retries=RETRIES)
                )
                headers = {
                    h["name"].lower(): h["value"]
                    for h in msg["payload"].get("headers", [])
                }
                emails.append(
                    Email(
                        id=msg["id"],
                        thread_id=msg["threadId"],
                        rfc822_message_id=headers.get("message-id", ""),
                        sender=headers.get("from", ""),
                        subject=headers.get("subject") or "(no subject)",
                        body=_body(msg["payload"]),
                        received_at=datetime.fromtimestamp(
                            int(msg["internalDate"]) / 1000, tz=UTC
                        ),
                        label_names=[
                            names.get(label_id, label_id)
                            for label_id in msg.get("labelIds", [])
                        ],
                    )
                )
            except Exception as e:
                self.unreadable.append(ref["id"])
                logger.warning("Skipped message {}: {}", ref["id"], type(e).__name__)
        logger.info("Fetched {} inbox emails", len(emails))
        return emails

    def add_labels(self, email_id: str, names: list[str]) -> None:
        labels = self._labels()
        for name in names:
            if name not in labels:
                created = (
                    self._users.labels()
                    .create(userId="me", body={"name": name})
                    .execute(num_retries=RETRIES)
                )
                labels[name] = created["id"]
                logger.info("Created label {}", name)
        self._users.messages().modify(
            userId="me", id=email_id, body={"addLabelIds": [labels[n] for n in names]}
        ).execute(num_retries=RETRIES)

    def mark_read(self, email_id: str) -> None:
        self._users.messages().modify(
            userId="me", id=email_id, body={"removeLabelIds": ["UNREAD"]}
        ).execute(num_retries=RETRIES)

    def create_draft(self, email: Email, body: str) -> str:
        message = EmailMessage()
        message["To"] = email.sender
        message["Subject"] = reply_subject(email.subject)
        if email.rfc822_message_id:
            message["In-Reply-To"] = email.rfc822_message_id
            message["References"] = email.rfc822_message_id
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        draft = (
            self._users.drafts()
            .create(
                userId="me", body={"message": {"raw": raw, "threadId": email.thread_id}}
            )
            .execute(num_retries=RETRIES)
        )
        draft_id: str = draft["id"]
        return draft_id


def build_service(settings: Settings) -> Any:
    creds: Any = None
    load_token: Any = Credentials.from_authorized_user_file
    if settings.gmail_token_path.exists():
        creds = load_token(str(settings.gmail_token_path), SCOPES)
    if not creds or not creds.valid:
        refreshed = False
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                refreshed = True
            except RefreshError:
                logger.warning("Gmail token refresh failed, signing in again")
        if not refreshed:
            creds = InstalledAppFlow.from_client_secrets_file(
                str(settings.gmail_client_secret_path), SCOPES
            ).run_local_server(port=0)
        settings.gmail_token_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        settings.gmail_token_path.parent.chmod(0o700)
        with os.fdopen(
            os.open(
                settings.gmail_token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
            ),
            "w",
        ) as token:
            token.write(creds.to_json())
        settings.gmail_token_path.chmod(0o600)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def check_inbox(service: Any, expected_inbox: str) -> None:
    profile = service.users().getProfile(userId="me").execute(num_retries=RETRIES)
    if profile.get("emailAddress", "").lower() != expected_inbox.lower():
        raise ValueError("Signed-in Gmail account is not the expected demo inbox")


def seed(service: Any, emails: list[Email], expected_inbox: str) -> int:
    check_inbox(service, expected_inbox)
    users = service.users()
    for email in emails:
        message = EmailMessage()
        message["From"] = email.sender
        message["To"] = LUMENFOLD_ADDRESS
        message["Subject"] = email.subject
        message["Date"] = format_datetime(email.received_at)
        message["Message-ID"] = email.rfc822_message_id
        message.set_content(email.body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        users.messages().insert(
            userId="me",
            body={"raw": raw, "labelIds": ["INBOX", "UNREAD"]},
            internalDateSource="dateHeader",
        ).execute(num_retries=RETRIES)
    logger.info("Seeded {} emails", len(emails))
    return len(emails)
