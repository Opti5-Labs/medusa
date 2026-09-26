"""Tests for agents/granite.py using an in-memory transport (no network)."""

import json

import httpx
import pytest
from pydantic import BaseModel

from app.agents import granite


class _Answer(BaseModel):
    ok: bool
    n: int


def _install(monkeypatch, replies: list[tuple[int, str]]):
    """Serve IAM tokens, then *replies* for successive chat calls. Returns call log."""
    calls: list[str] = []
    queue = list(replies)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if "identity/token" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "access_token": "test-access-token-0123456789",
                    "expires_in": 3600,
                },
            )
        status, content = queue.pop(0)
        if status == 200:
            body = {"choices": [{"message": {"content": content}}]}
        else:
            body = json.loads(content) if content else {}
        return httpx.Response(status, json=body)

    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(granite.httpx, "AsyncClient", factory)
    monkeypatch.setattr("app.config.WATSONX_API_KEY", "test-key")
    monkeypatch.setattr("app.config.WATSONX_PROJECT_ID", "test-project")
    monkeypatch.setattr(granite, "_token", None)
    monkeypatch.setattr(granite, "_cache", granite.OrderedDict())
    return calls


async def test_valid_json_is_parsed(monkeypatch):
    _install(monkeypatch, [(200, json.dumps({"ok": True, "n": 3}))])
    result = await granite.chat_json("sys", "user", _Answer)
    assert result == _Answer(ok=True, n=3)


async def test_code_fenced_json_is_accepted(monkeypatch):
    _install(monkeypatch, [(200, '```json\n{"ok": true, "n": 1}\n```')])
    assert (await granite.chat_json("s", "u", _Answer)).n == 1


async def test_invalid_json_retried_once_then_fails(monkeypatch):
    calls = _install(monkeypatch, [(200, "not json"), (200, '{"ok": "x"}')])
    with pytest.raises(granite.GraniteError, match="invalid JSON"):
        await granite.chat_json("s", "u", _Answer)
    assert sum(1 for c in calls if c.endswith("/text/chat")) == 2


async def test_retry_recovers(monkeypatch):
    _install(monkeypatch, [(500, ""), (200, '{"ok": false, "n": 2}')])
    assert (await granite.chat_json("s", "u", _Answer)).n == 2


async def test_authorisation_failure_is_not_retried(monkeypatch):
    calls = _install(monkeypatch, [(403, "")])
    with pytest.raises(granite.GraniteError, match="not authorised"):
        await granite.chat_json("s", "u", _Answer)
    assert sum(1 for c in calls if c.endswith("/text/chat")) == 1


async def test_not_configured_raises():
    assert not granite.is_configured()
    with pytest.raises(granite.GraniteError, match="not configured"):
        await granite.chat_json("s", "u", _Answer)


def test_error_messages_never_contain_the_key(monkeypatch):
    monkeypatch.setattr("app.config.WATSONX_API_KEY", "secret-key-1234567890")
    assert "secret-key-1234567890" not in granite._scrub(
        "failed with secret-key-1234567890"
    )


async def test_rate_limit_waits_and_retries(monkeypatch):
    monkeypatch.setattr(granite, "_RATE_LIMIT_WAITS_S", (0, 0))
    calls = _install(monkeypatch, [(429, ""), (429, ""), (200, '{"ok": true, "n": 5}')])
    assert (await granite.chat_json("s", "u", _Answer)).n == 5
    assert sum(1 for c in calls if c.endswith("/text/chat")) == 3


async def test_rate_limit_gives_up_after_waits(monkeypatch):
    monkeypatch.setattr(granite, "_RATE_LIMIT_WAITS_S", (0,))
    _install(monkeypatch, [(429, ""), (429, "")])
    with pytest.raises(granite.GraniteError, match="rate limit"):
        await granite.chat_json("s", "u", _Answer)


class _Drifty(granite.LenientModel):
    text: str
    items: list[str]
    confidence: float


def test_lenient_model_absorbs_shape_drift():
    m = _Drifty.model_validate({"text": ["a", "b"], "items": "one", "confidence": 90})
    assert (m.text, m.items, m.confidence) == ("a; b", ["one"], 0.9)


async def test_quota_exhausted_is_reported_and_not_retried(monkeypatch):
    quota = json.dumps({"errors": [{"code": "token_quota_reached", "message": "x"}]})
    calls = _install(monkeypatch, [(403, quota)])
    with pytest.raises(granite.GraniteError, match="token quota"):
        await granite.chat_json("s", "u", _Answer)
    assert sum(1 for c in calls if c.endswith("/text/chat")) == 1


async def test_identical_prompts_are_answered_from_cache(monkeypatch):
    calls = _install(monkeypatch, [(200, '{"ok": true, "n": 1}')])
    first = await granite.chat_json("s", "u", _Answer)
    second = await granite.chat_json("s", "u", _Answer)
    assert first == second
    assert sum(1 for c in calls if c.endswith("/text/chat")) == 1


async def test_other_errors_include_watsonx_reason(monkeypatch):
    body = json.dumps(
        {"errors": [{"code": "project_not_found", "message": "no such project"}]}
    )
    _install(monkeypatch, [(404, body), (404, body)])
    with pytest.raises(
        granite.GraniteError, match="404 \\(project_not_found: no such project\\)"
    ):
        await granite.chat_json("s", "u", _Answer)
