import json
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import anthropic
import pytest


def test_ac_24_cli_mock_run(
    h: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-24: `run --source mock` builds one Anthropic client (max_retries=3, timeout=60), uses the locked model call params, never touches Gmail, writes a run folder, returns 0; console script declared."""
    import src.cli
    import src.gmail

    data = tmp_path / "emails.json"
    data.write_text(
        json.dumps(
            [h.email(i, subject=f"Mail {i}").model_dump(mode="json") for i in range(50)]
        )
    )
    client = MagicMock()
    client.beta.messages.parse.return_value = h.parsed(h.cls("support", 0.9))
    client.beta.messages.create.return_value = h.message("Thanks, here is the answer.")
    built: list[dict[str, Any]] = []

    def factory(**kwargs: Any) -> MagicMock:
        built.append(kwargs)
        return client

    def no_gmail(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Gmail must not be used in a mock run")

    monkeypatch.setattr(anthropic, "Anthropic", factory)
    for module in (src.cli, src.gmail):
        for name in ("Anthropic", "GmailMailbox", "build_service"):
            if hasattr(module, name):
                monkeypatch.setattr(
                    module, name, factory if name == "Anthropic" else no_gmail
                )

    out = tmp_path / "runs"
    assert (
        src.cli.main(
            ["run", "--source", "mock", "--data", str(data), "--out", str(out)]
        )
        == 0
    )
    assert len(built) == 1
    assert built[0]["max_retries"] == 3 and built[0]["timeout"] == 60.0
    locked = {
        "model": "claude-sonnet-5-5",
        "betas": ["server-side-fallback-2026-07-01"],
        "fallbacks": "default",
    }
    assert client.beta.messages.parse.call_count == 50
    for call in client.beta.messages.parse.call_args_list:
        assert {k: call.kwargs[k] for k in locked} == locked and call.kwargs[
            "max_tokens"
        ] == 1024
    assert client.beta.messages.create.call_count == 50
    for call in client.beta.messages.create.call_args_list:
        assert {k: call.kwargs[k] for k in locked} == locked and call.kwargs[
            "max_tokens"
        ] == 2000
    runs = [p for p in out.iterdir() if p.is_dir()]
    assert len(runs) == 1
    assert {"report.json", "report.md", "report.html"} <= {
        p.name for p in runs[0].iterdir()
    }
    pyproject = tomllib.loads((h.repo / "pyproject.toml").read_text())
    assert pyproject["project"]["scripts"]["support-inbox-triage"] == "src.cli:main"
