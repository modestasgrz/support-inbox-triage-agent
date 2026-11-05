"""Generic parts of the kit's security tests: canary values and the fake model provider. Don't edit; wire the app in security_adapter.py."""

from typing import Any

import pytest
import security_adapter
from pydantic import BaseModel, ConfigDict

PII_EMAIL = "pii.canary@example.com"
PII_PHONE = "+370 600 12345"
PII_TEXT = "PII_CANARY my salary is 4321 EUR"
CANARY = "SECRET_CANARY_7f3a9c"


class LLMCall(BaseModel):
    system: str
    user: str


class FakeLLM(BaseModel):
    """Stands in for the model provider. security_adapter.install_fakes routes every model call of the app to respond()."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    reply: Any = None
    error: Exception | None = None
    calls: list[LLMCall] = []

    def respond(self, system: str, user: str) -> Any:
        self.calls.append(LLMCall(system=system, user=user))
        if self.error:
            raise self.error
        return self.reply


def need(check: str) -> None:
    """Skips a check the owner marked not applicable; marks a known, accepted-for-now hole as xfail. Both show their reason."""
    if check in security_adapter.NOT_APPLICABLE:
        pytest.skip(f"n/a: {security_adapter.NOT_APPLICABLE[check]}")
    if check in getattr(security_adapter, "KNOWN_OPEN", {}):
        pytest.xfail(f"KNOWN OPEN: {security_adapter.KNOWN_OPEN[check]}")
