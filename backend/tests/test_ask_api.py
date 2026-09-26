"""
End-to-end API tests for Ask Medusa, including the SSE stream.

Granite and Bob are faked at the pipeline's module attributes; no model, network
or executable is ever started.
"""

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

from app import config
from app.agents import granite
from app.agents.bob import BobAnswer
from app.main import app
from app.pipelines.ask import BobQA

WHISPER = "optilearn/app/services/whisper_client.py"
MODEL_QUESTION = "what does whisper_client do"
FAKE_WATSONX_KEY = "not-a-real-watsonx-secret-" * 3
FAKE_BOB_KEY = "not-a-real-bob-secret-" * 3


@pytest.fixture
async def client():
    from app.api import ask as ask_module
    from app.api import runs as runs_module
    from app.api import scan as scan_module
    from app.ratelimit import RateLimiter
    from app.store import RunStore

    store = RunStore()
    scan_module.set_store(store)
    runs_module.set_store(store)
    ask_module.set_store(store)
    ask_module._ask_limiter = RateLimiter(
        max_calls=100, window_seconds=600, what="questions"
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    await store.stop()


@pytest.fixture
def limiter():
    """Replace the ask limiter with one allowing *max_calls* questions."""

    def install(max_calls: int) -> None:
        from app.api import ask as ask_module
        from app.ratelimit import RateLimiter

        ask_module._ask_limiter = RateLimiter(
            max_calls=max_calls, window_seconds=600, what="questions"
        )

    return install


class FakeGranite:
    def __init__(self, monkeypatch, pieces=("It reads ", "the model id.")) -> None:
        self.calls = 0
        self.pieces = pieces
        self.gate: asyncio.Event | None = None
        self.raises: Exception | None = None
        monkeypatch.setattr("app.pipelines.ask.granite.is_configured", lambda: True)
        monkeypatch.setattr("app.pipelines.ask.granite.chat_text_stream", self.stream)

    async def stream(self, system, user, **kwargs):
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.raises is not None:
            raise self.raises
        for piece in self.pieces:
            yield piece


@pytest.fixture
def fake_granite(monkeypatch):
    return FakeGranite(monkeypatch)


def _parse(text: str) -> list[tuple[str, dict]]:
    events, name = [], None
    for line in text.splitlines():
        if line.startswith("event:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            events.append((name, json.loads(line.split(":", 1)[1].strip())))
    return events


async def _stream(client: AsyncClient, ask_id: str) -> tuple[list[dict], dict]:
    """Read an answer stream to the end; return (token events, done payload)."""
    resp = await client.get(f"/api/ask/{ask_id}/events")
    assert resp.status_code == 200
    events = _parse(resp.text)
    done = [d for n, d in events if n == "done"]
    assert len(done) == 1, "stream must end with exactly one done event"
    return [d for n, d in events if n == "token"], done[0]


async def _scan(client: AsyncClient) -> str:
    resp = await client.post("/api/scan", json={"source": "demo"})
    return resp.json()["scan_id"]


async def _ask(client: AsyncClient, scan_id: str, question: str, **extra):
    return await client.post(
        f"/api/scan/{scan_id}/ask", json={"question": question, **extra}
    )


async def _ask_done(client, scan_id, question, **extra) -> dict:
    resp = await _ask(client, scan_id, question, **extra)
    assert resp.status_code == 200, resp.text
    _, done = await _stream(client, resp.json()["ask_id"])
    return done


# ── Status ────────────────────────────────────────────────────────────────────


async def test_status_all_available(client, monkeypatch):
    monkeypatch.setattr("app.pipelines.ask.granite.is_configured", lambda: True)
    monkeypatch.setattr("app.pipelines.ask.bob.live_unavailable_reason", lambda: None)
    resp = await client.get("/api/ask/status")
    assert resp.status_code == 200
    assert resp.json() == {
        "enabled": True,
        "granite_available": True,
        "bob_available": True,
        "bob_reason": None,
    }


async def test_status_disabled(client, monkeypatch):
    monkeypatch.setattr(config, "ASK_ENABLED", False)
    assert (await client.get("/api/ask/status")).json()["enabled"] is False


async def test_status_granite_unconfigured(client, monkeypatch):
    monkeypatch.setattr("app.pipelines.ask.granite.is_configured", lambda: False)
    body = (await client.get("/api/ask/status")).json()
    assert body["granite_available"] is False


async def test_status_bob_unavailable_with_reason(client, monkeypatch):
    monkeypatch.setattr(
        "app.pipelines.ask.bob.live_unavailable_reason",
        lambda: "BOB_API_KEY is not set",
    )
    body = (await client.get("/api/ask/status")).json()
    assert body["bob_available"] is False
    assert body["bob_reason"] == "BOB_API_KEY is not set"


# ── Answers ───────────────────────────────────────────────────────────────────


async def test_instant_answer_uses_no_model_and_is_free(client, fake_granite, limiter):
    limiter(1)
    scan_id = await _scan(client)
    for _ in range(2):
        done = await _ask_done(client, scan_id, "what are the issues?")
        assert done["answered_by"] == "scan"
        assert done["grounding"] == "scan_data"
    assert fake_granite.calls == 0


async def test_model_question_streams_tokens_then_done(client, fake_granite):
    scan_id = await _scan(client)
    resp = await _ask(client, scan_id, MODEL_QUESTION)
    tokens, done = await _stream(client, resp.json()["ask_id"])
    assert "".join(t["text"] for t in tokens) == "It reads the model id."
    assert done["answered_by"] == "granite"
    assert WHISPER in done["files_read"]
    assert done["error"] is None


async def test_granite_quota_falls_back_to_bob(client, fake_granite, monkeypatch):
    fake_granite.raises = granite.GraniteUnavailable("token quota reached")
    monkeypatch.setattr("app.pipelines.ask.bob.live_unavailable_reason", lambda: None)

    async def fake_bob(prompt, files, schema, timeout_s=None):
        return BobAnswer("ok", data=BobQA(answer="Bob says hi."), cost=0.01)

    monkeypatch.setattr("app.pipelines.ask.bob.ask", fake_bob)
    scan_id = await _scan(client)
    done = await _ask_done(client, scan_id, MODEL_QUESTION)
    assert done["answered_by"] == "bob"
    assert done["answer"] == "Bob says hi."
    assert "quota" in done["notice"]


async def test_stream_replays_identically(client, fake_granite):
    scan_id = await _scan(client)
    ask_id = (await _ask(client, scan_id, MODEL_QUESTION)).json()["ask_id"]
    first = await client.get(f"/api/ask/{ask_id}/events")
    second = await client.get(f"/api/ask/{ask_id}/events")
    assert first.status_code == second.status_code == 200
    assert _parse(first.text) == _parse(second.text)


# ── Errors ────────────────────────────────────────────────────────────────────


async def test_unknown_scan_is_404(client):
    resp = await _ask(client, "no-such-scan", "hello there")
    assert resp.status_code == 404
    assert "expired or was not found" in resp.json()["detail"]


async def test_unknown_issue_is_404(client):
    scan_id = await _scan(client)
    resp = await _ask(client, scan_id, MODEL_QUESTION, issue_id="nope")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "That issue is not part of this scan."


async def test_unknown_answer_is_404(client):
    resp = await client.get("/api/ask/nope/events")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Answer not found or expired."


@pytest.mark.parametrize("question", ["", "   ", "x" * 1001])
async def test_bad_question_is_422_with_plain_message(client, question):
    scan_id = await _scan(client)
    resp = await _ask(client, scan_id, question)
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Type a question (up to 1000 characters)."


async def test_extra_field_is_422(client):
    scan_id = await _scan(client)
    resp = await _ask(client, scan_id, "hello there", surprise=1)
    assert resp.status_code == 422


async def test_disabled_is_503(client, monkeypatch):
    monkeypatch.setattr(config, "ASK_ENABLED", False)
    scan_id = await _scan(client)
    resp = await _ask(client, scan_id, MODEL_QUESTION)
    assert resp.status_code == 503
    assert resp.json()["detail"] == "Questions are switched off on this server."


async def test_second_question_while_running_is_409(client, fake_granite):
    fake_granite.gate = asyncio.Event()
    scan_id = await _scan(client)
    first = await _ask(client, scan_id, MODEL_QUESTION)
    assert first.status_code == 200
    resp = await _ask(client, scan_id, "and what about the settings?")
    assert resp.status_code == 409
    assert "previous question" in resp.json()["detail"]

    fake_granite.gate.set()
    await _stream(client, first.json()["ask_id"])
    assert (await _ask(client, scan_id, MODEL_QUESTION)).status_code == 200


async def test_rate_limit_applies_to_model_questions(client, fake_granite, limiter):
    limiter(1)
    scan_id = await _scan(client)
    await _ask_done(client, scan_id, MODEL_QUESTION)
    resp = await _ask(client, scan_id, MODEL_QUESTION)
    assert resp.status_code == 429
    assert "questions" in resp.json()["detail"]


# ── Secrets and docs ──────────────────────────────────────────────────────────


async def test_keys_never_reach_the_client(client, fake_granite, monkeypatch):
    monkeypatch.setattr(config, "WATSONX_API_KEY", FAKE_WATSONX_KEY)
    monkeypatch.setattr(config, "BOB_API_KEY", FAKE_BOB_KEY)
    scan_id = await _scan(client)
    ask_id = (await _ask(client, scan_id, MODEL_QUESTION)).json()["ask_id"]
    texts = [
        (await client.get("/api/ask/status")).text,
        (await client.get(f"/api/ask/{ask_id}/events")).text,
        (await _ask(client, scan_id, "   ")).text,
    ]
    for text in texts:
        assert FAKE_WATSONX_KEY not in text
        assert FAKE_BOB_KEY not in text


def test_routes_are_in_openapi():
    paths = app.openapi()["paths"]
    assert "get" in paths["/api/ask/status"]
    assert "post" in paths["/api/scan/{scan_id}/ask"]
    assert "get" in paths["/api/ask/{ask_id}/events"]
