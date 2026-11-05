"""Fixtures for the kit's security tests. Generic, don't edit; the app wiring is in security_adapter.py."""

import logging
from collections.abc import Callable, Iterator

import pytest
import security_adapter
from kit_security import FakeLLM
from loguru import logger
from starlette.testclient import TestClient


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM(reply=security_adapter.good_reply())


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch, llm: FakeLLM) -> TestClient:
    security_adapter.install_fakes(monkeypatch, llm)
    return TestClient(security_adapter.app(), raise_server_exceptions=False)


@pytest.fixture
def logs(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> Iterator[Callable[[], str]]:
    """Everything the app logged or printed: loguru (with tracebacks and variable values, like its default handler), stdlib logging, stdout, stderr."""
    caplog.set_level(logging.DEBUG)
    lines: list[str] = []
    sink = logger.add(
        lambda message: lines.append(str(message)),
        level=0,
        format="{message} | {extra} | {exception}",
        backtrace=True,
        diagnose=True,
    )

    def read() -> str:
        out = capsys.readouterr()
        lines.extend([out.out, out.err])
        return "\n".join([*lines, caplog.text])

    yield read
    logger.remove(sink)
