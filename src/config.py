from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

DEMO_DIR = Path("~/.config/portfolio-demos").expanduser()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRIAGE_")

    model: str = "claude-sonnet-5-5"
    confidence_threshold: float = 0.70
    body_max_chars: int = 8000
    gmail_client_secret_path: Path = DEMO_DIR / "client_secret.json"
    gmail_token_path: Path = DEMO_DIR / "token-support-inbox.json"
    demo_inbox: str | None = None
    faq_path: Path = Path("data/faq.md")
