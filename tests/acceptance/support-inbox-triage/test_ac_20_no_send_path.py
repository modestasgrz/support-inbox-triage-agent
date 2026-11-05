from types import SimpleNamespace

FORBIDDEN = [
    ".send(",
    "send_message",
    '"send"',
    "forward",
    ".trash(",
    ".delete(",
    "batchDelete",
    "untrash",
]
FORBIDDEN_SCOPES = ["gmail.send", "gmail.compose", "mail.google.com"]


def test_ac_20_no_send_path(h: SimpleNamespace) -> None:
    """AC-20: src/ has no send, forward, trash or delete call, and asks only for the gmail.modify scope."""
    from src.gmail import SCOPES

    assert SCOPES == ["https://www.googleapis.com/auth/gmail.modify"]
    files = sorted((h.repo / "src").rglob("*.py"))
    assert files
    for path in files:
        text = path.read_text()
        for needle in [*FORBIDDEN, *FORBIDDEN_SCOPES]:
            assert needle not in text, f"{path.relative_to(h.repo)} contains {needle!r}"
