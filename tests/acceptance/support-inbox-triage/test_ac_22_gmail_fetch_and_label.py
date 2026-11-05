import base64
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def message(
    msg_id: str, payload: dict[str, Any], headers: list[dict[str, str]]
) -> dict[str, Any]:
    return {
        "id": msg_id,
        "threadId": f"t-{msg_id}",
        "internalDate": "1730451600000",
        "labelIds": ["INBOX", "UNREAD", "Label_1"],
        "payload": {**payload, "headers": headers},
    }


def test_ac_22_gmail_fetch_and_label(h: SimpleNamespace) -> None:
    """AC-22: fetch_inbox parses plain/html/no-subject messages and resolves label names; add_labels creates a missing label once; mark_read removes UNREAD."""
    from src.gmail import GmailMailbox

    base_headers = [
        {"name": "From", "value": "Ana <ana@customer.example>"},
        {"name": "Message-ID", "value": "<x@customer.example>"},
    ]
    messages = {
        "a": message(
            "a",
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": b64("plain wins")}},
                    {
                        "mimeType": "text/html",
                        "body": {"data": b64("<p>html loses</p>")},
                    },
                ],
            },
            [*base_headers, {"name": "Subject", "value": "Alt"}],
        ),
        "b": message(
            "b",
            {
                "mimeType": "text/html",
                "body": {"data": b64("<div><b>Only</b> html</div>")},
            },
            [*base_headers, {"name": "Subject", "value": "Html"}],
        ),
        "c": message(
            "c",
            {"mimeType": "text/plain", "body": {"data": b64("no subject here")}},
            base_headers,
        ),
    }
    service = MagicMock()
    users = service.users.return_value
    users.messages.return_value.list.return_value.execute.return_value = {
        "messages": [{"id": k} for k in messages]
    }

    def get(**kwargs: Any) -> MagicMock:
        call = MagicMock()
        call.execute.return_value = messages[kwargs["id"]]
        return call

    users.messages.return_value.get.side_effect = get
    users.labels.return_value.list.return_value.execute.return_value = {
        "labels": [
            {"id": "INBOX", "name": "INBOX"},
            {"id": "UNREAD", "name": "UNREAD"},
            {"id": "Label_1", "name": "Personal"},
        ]
    }

    emails = {e.id: e for e in GmailMailbox(service).fetch_inbox()}
    assert users.messages.return_value.list.call_args.kwargs["q"] == "in:inbox"
    assert set(emails) == {"a", "b", "c"}
    assert "plain wins" in emails["a"].body and "html loses" not in emails["a"].body
    assert (
        "<" not in emails["b"].body
        and "Only" in emails["b"].body
        and "html" in emails["b"].body
    )
    assert emails["c"].subject == "(no subject)"
    assert "Personal" in emails["a"].label_names
    assert "Label_1" not in emails["a"].label_names

    service = MagicMock()
    users = service.users.return_value
    users.labels.return_value.list.return_value.execute.return_value = {
        "labels": [{"id": "INBOX", "name": "INBOX"}]
    }
    users.labels.return_value.create.return_value.execute.return_value = {
        "id": "Label_9",
        "name": "Triage/Support",
    }
    box = GmailMailbox(service)
    box.add_labels("a", ["Triage/Support"])
    box.add_labels("b", ["Triage/Support"])
    assert users.labels.return_value.create.call_count == 1
    modify = users.messages.return_value.modify
    assert modify.call_args.kwargs["body"]["addLabelIds"] == ["Label_9"]
    box.mark_read("a")
    assert modify.call_args.kwargs["id"] == "a"
    assert modify.call_args.kwargs["body"]["removeLabelIds"] == ["UNREAD"]
