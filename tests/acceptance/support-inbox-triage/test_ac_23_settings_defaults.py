from pathlib import Path

import pytest


def test_ac_23_settings_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-23: Settings defaults (shared OAuth paths outside the repo, model, threshold, cap, no demo inbox) and TRIAGE_* override."""
    from src.config import Settings

    s = Settings()
    assert (
        s.gmail_client_secret_path
        == Path("~/.config/portfolio-demos/client_secret.json").expanduser()
    )
    assert (
        s.gmail_token_path
        == Path("~/.config/portfolio-demos/token-support-inbox.json").expanduser()
    )
    assert s.model == "claude-sonnet-5-5"
    assert s.confidence_threshold == 0.70
    assert s.body_max_chars == 8000
    assert s.demo_inbox is None
    monkeypatch.setenv("TRIAGE_GMAIL_TOKEN_PATH", str(tmp_path / "t.json"))
    assert Settings().gmail_token_path == tmp_path / "t.json"
