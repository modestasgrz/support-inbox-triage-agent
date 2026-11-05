"""ADAPT: wire the kit's security tests (test_security.py) to this app. Owner-only, locked like tests/acceptance/.

Fill every function and constant marked ADAPT. A check that genuinely doesn't apply goes in NOT_APPLICABLE with
the reason; it then shows as skipped with that reason. Never use NOT_APPLICABLE to silence a failing check; a real hole the owner accepts
for now goes in KNOWN_OPEN (shows as xfail with the reason).
Check names: pii_logs, prompt_injection, model_output, step_skip, xff_spoof, cors, provider_error, body_cap, image_lock.
`make security` fails when a test listed in tests/security/EXPECTED_TESTS isn't collected by the full test run.
After adapting: uv run pytest tests/security --collect-only -q | grep '::' > tests/security/EXPECTED_TESTS
"""

import importlib.util
import tempfile
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient

if TYPE_CHECKING:
    from kit_security import FakeLLM

REPO = Path(__file__).resolve().parents[2]

NOT_APPLICABLE: dict[str, str] = {
    "step_skip": "batch CLI job, no multi-step web workflow a caller could skip into",
    "xff_spoof": "no HTTP endpoint and no rate-limited route; the job runs locally on a schedule",
    "cors": "no HTTP endpoint, nothing is called from a browser",
    "body_cap": "no HTTP endpoint; email body length is capped in code before it reaches the model (spec AC-10)",
    "image_lock": "no deploy image: runs locally / cron example only, never deployed",
}
# The app checks run through a test-only shim (app() below) that triages one email with the real pipeline.
# Until the pipeline exists they are xfail; they switch on by themselves once src.pipeline is importable.
_BUILT = (
    importlib.util.find_spec("src") is not None
    and importlib.util.find_spec("src.pipeline") is not None
)
KNOWN_OPEN: dict[str, str] = (
    {}
    if _BUILT
    else {
        check: "pipeline not built yet (spec support-inbox-triage, PART-A)"
        for check in ("pii_logs", "prompt_injection", "model_output", "provider_error")
    }
)

RATE_LIMIT = 5
CORS_PATH = "/triage"
ALLOWED_ORIGIN = "https://none.example"
BODY_CAP_PATH = "/triage"
REQUEST_ID_HEADER = "x-request-id"
USER_INPUT_TAG = "email"
IMAGE_FILES: list[Path] = []

_STATE: dict[str, Any] = {}


def _system_text(system: Any) -> str:
    if isinstance(system, str):
        return system
    return "\n".join(b["text"] if isinstance(b, dict) else b.text for b in system)


def _fake_client(llm: "FakeLLM") -> MagicMock:
    Classification = importlib.import_module("src.models").Classification

    def parse(**kwargs: Any) -> SimpleNamespace:
        reply = llm.respond(
            _system_text(kwargs["system"]), kwargs["messages"][0]["content"]
        )
        try:
            parsed = Classification.model_validate(reply)
        except ValidationError:
            return SimpleNamespace(
                stop_reason="end_turn",
                parsed_output=None,
                content=[SimpleNamespace(type="text", text=str(reply))],
            )
        return SimpleNamespace(stop_reason="end_turn", parsed_output=parsed, content=[])

    def create(**kwargs: Any) -> SimpleNamespace:
        llm.respond(_system_text(kwargs["system"]), kwargs["messages"][0]["content"])
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[
                SimpleNamespace(
                    type="text", text="Thanks for writing, we will sort this out today."
                )
            ],
        )

    client = MagicMock()
    client.beta.messages.parse.side_effect = parse
    client.beta.messages.create.side_effect = create
    return client


async def _triage(request: Request) -> Response:
    src = {
        name: importlib.import_module(f"src.{name}")
        for name in ("classifier", "config", "drafter", "mailbox", "models", "pipeline")
    }
    Classifier, Drafter, FakeMailbox = (
        src["classifier"].Classifier,
        src["drafter"].Drafter,
        src["mailbox"].FakeMailbox,
    )
    Email, Settings, run_triage = (
        src["models"].Email,
        src["config"].Settings,
        src["pipeline"].run_triage,
    )

    data = await request.json()
    email = Email(
        id="m1",
        thread_id="t1",
        rfc822_message_id="<m1@customer.example>",
        sender=data["email"] or "anon@customer.example",
        subject=data["text"][:200],
        body=data["text"],
        received_at=datetime.now(UTC),
    )
    settings = Settings()
    faq = "Lumenfold Candle Co. FAQ: shipping takes 3-5 working days."
    client = _fake_client(_STATE["llm"])
    box = FakeMailbox([email])
    _STATE["box"] = box
    report = run_triage(
        box,
        Classifier(client, settings),
        Drafter(client, settings, faq),
        faq,
        settings,
        Path(tempfile.mkdtemp()),
    )
    request_id = uuid.uuid4().hex
    if any(o.status == "failed" for o in report.outcomes):
        return JSONResponse(
            {"error": "triage failed", "request_id": request_id},
            status_code=500,
            headers={REQUEST_ID_HEADER: request_id},
        )
    return Response(
        report.model_dump_json(),
        media_type="application/json",
        headers={REQUEST_ID_HEADER: request_id},
    )


def app() -> Any:
    """Test-only shim: one POST route that triages one email with the real pipeline and a fake model."""
    return Starlette(routes=[Route("/triage", _triage, methods=["POST"])])


def install_fakes(monkeypatch: pytest.MonkeyPatch, llm: "FakeLLM") -> None:
    """The shim builds a FakeMailbox per request and routes both model calls to llm.respond; no network."""
    _STATE.clear()
    _STATE["llm"] = llm
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def good_reply() -> Any:
    return {
        "category": "support",
        "confidence": 0.9,
        "reason": "customer asks about an order",
        "evidence_quote": "my order",
        "injection_suspected": False,
        "injection_reason": None,
    }


def invalid_reply(canary: str) -> Any:
    return {"category": canary, "confidence": "high"}


def provider_error(canary: str) -> Exception:
    import anthropic
    import httpx2

    request = httpx2.Request("POST", "https://api.example/v1/messages")
    return anthropic.InternalServerError(
        f"provider 500: {canary}",
        response=httpx2.Response(500, request=request, text=canary),
        body={"error": canary},
    )


def send_visitor_text(http: TestClient, text: str, email: str) -> Any:
    """The email text goes in subject and body, the address in From."""
    return http.post("/triage", json={"text": text, "email": email})


def skip_step(http: TestClient) -> Any:
    raise NotImplementedError("n/a: step_skip")


def writes() -> list[Any]:
    box = _STATE.get("box")
    return list(box.drafts) if box else []


def call_rate_limited(http: TestClient, headers: dict[str, str]) -> Any:
    raise NotImplementedError("n/a: xff_spoof")


def _break_mailbox(monkeypatch: pytest.MonkeyPatch) -> None:
    from kit_security import PII_EMAIL

    FakeMailbox = importlib.import_module("src.mailbox").FakeMailbox

    def fail(self: Any, email_id: str, names: list[str]) -> None:
        raise RuntimeError(f"gmail rejected the change for {PII_EMAIL}")

    monkeypatch.setattr(FakeMailbox, "add_labels", fail)


BREAK_DEPENDENCY: dict[str, Callable[[pytest.MonkeyPatch], None]] = (
    {"mailbox": _break_mailbox} if _BUILT else {}
)
