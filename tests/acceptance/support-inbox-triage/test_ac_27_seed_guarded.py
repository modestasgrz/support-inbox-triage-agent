from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest


def test_ac_27_seed_guarded(
    h: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-27: seed inserts with dateHeader + INBOX/UNREAD only into the expected inbox and writes nothing else; the CLI refuses Gmail work without TRIAGE_DEMO_INBOX."""
    import src.cli
    import src.gmail
    from src.gmail import seed

    emails = [h.email(i) for i in range(3)]
    service = MagicMock()
    users = service.users.return_value
    users.getProfile.return_value.execute.return_value = {
        "emailAddress": "demo@example.com"
    }
    assert seed(service, emails, expected_inbox="demo@example.com") == 3
    insert = users.messages.return_value.insert
    assert insert.call_count == 3
    for call in insert.call_args_list:
        assert call.kwargs["internalDateSource"] == "dateHeader"
        assert call.kwargs["body"]["labelIds"] == ["INBOX", "UNREAD"]
    for write in (
        users.messages.return_value.send,
        users.messages.return_value.modify,
        users.drafts.return_value.create,
        users.drafts.return_value.send,
    ):
        write.assert_not_called()

    service = MagicMock()
    users = service.users.return_value
    users.getProfile.return_value.execute.return_value = {
        "emailAddress": "demo@example.com"
    }
    with pytest.raises(ValueError):
        seed(service, emails, expected_inbox="other@example.com")
    users.messages.return_value.insert.assert_not_called()

    def no_service(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("no Gmail service without TRIAGE_DEMO_INBOX")

    for module in (src.cli, src.gmail):
        if hasattr(module, "build_service"):
            monkeypatch.setattr(module, "build_service", no_service)
    assert src.cli.main(["run", "--source", "gmail"]) == 2
    assert src.cli.main(["seed"]) == 2
