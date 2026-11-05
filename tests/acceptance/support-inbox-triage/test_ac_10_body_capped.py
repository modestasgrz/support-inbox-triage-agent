import re
from pathlib import Path
from types import SimpleNamespace


def test_ac_10_body_capped(h: SimpleNamespace, tmp_path: Path) -> None:
    """AC-10: a 20,000-char body reaches the model as its first 8,000 chars and the outcome says truncated; an empty body is still classified."""
    body = "".join(chr(ord("a") + i % 26) for i in range(20_000))
    emails = [
        h.email(1, subject="Long one", body=body),
        h.email(2, subject="Empty one", body=""),
    ]
    client = h.client(
        {
            "Long one": h.parsed(h.cls("other", 0.95)),
            "Empty one": h.parsed(h.cls("other", 0.95)),
        }
    )
    _, report = h.run(emails, client, tmp_path)
    long_call = next(
        c
        for c in client.beta.messages.parse.call_args_list
        if "Long one" in c.kwargs["messages"][0]["content"]
    )
    block = re.search(
        r"<(email-[0-9a-f]{16})>(.*?)</\1>",
        long_call.kwargs["messages"][0]["content"],
        re.DOTALL,
    )
    assert block is not None
    assert body[:8000] in block.group(2)
    assert body[:8001] not in block.group(2)
    assert h.outcome(report, "m1").truncated is True
    empty = h.outcome(report, "m2")
    assert empty.truncated is False
    assert empty.category == "other"
