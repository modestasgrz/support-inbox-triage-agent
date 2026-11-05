import base64
import email as email_lib
from types import SimpleNamespace
from unittest.mock import MagicMock


def test_ac_21_gmail_threaded_draft(h: SimpleNamespace) -> None:
    """AC-21: GmailMailbox.create_draft makes a threaded reply draft and every execute() uses num_retries=3."""
    from src.gmail import GmailMailbox

    service = MagicMock()
    drafts = service.users.return_value.drafts.return_value
    drafts.create.return_value.execute.return_value = {"id": "d-77"}
    source = h.email(1, subject="Order #1042 not arrived")
    draft_id = GmailMailbox(service).create_draft(source, "body text")
    assert draft_id == "d-77"
    kwargs = drafts.create.call_args.kwargs
    assert kwargs["userId"] == "me"
    assert kwargs["body"]["message"]["threadId"] == "t1"
    mime = email_lib.message_from_bytes(
        base64.urlsafe_b64decode(kwargs["body"]["message"]["raw"])
    )
    assert email_lib.utils.parseaddr(mime["To"])[1] == "c1@customer.example"
    assert (
        str(
            email_lib.header.make_header(
                email_lib.header.decode_header(mime["Subject"])
            )
        )
        == "Re: Order #1042 not arrived"
    )
    assert mime["In-Reply-To"] == source.rfc822_message_id
    assert mime["References"] == source.rfc822_message_id
    executes = [c for c in service.mock_calls if c[0].endswith("execute")]
    assert executes
    assert all(c.kwargs.get("num_retries") == 3 for c in executes)
