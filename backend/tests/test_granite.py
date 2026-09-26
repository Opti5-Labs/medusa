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


async def test_non_retryable_failures_keep_their_type(monkeypatch):
    quota = json.dumps({"errors": [{"code": "token_quota_reached", "message": "x"}]})
    _install(monkeypatch, [(403, quota)])
    with pytest.raises(granite.GraniteUnavailable):
        await granite.chat_json("s", "u", _Answer)


def _sse(*pieces: str, done: bool = True) -> str:
    lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": p}}]}) for p in pieces
    ]
    if done:
        lines.append("data: [DONE]")
    return "\n\n".join(lines) + "\n\n"


class _DyingStream(httpx.AsyncByteStream):
    def __init__(self, first: str) -> None:
        self._first = first

    async def __aiter__(self):
        yield self._first.encode()
        raise httpx.ReadError("connection lost")


def _install_stream(monkeypatch, stream_replies: list, chat_replies=()):
    """Serve IAM tokens, then *stream_replies* for chat_stream calls and
    *chat_replies* for plain /text/chat calls. A stream reply is (status, body)
    where body is SSE text, a JSON string for errors, or an httpx byte stream."""
    calls: list[str] = []
    streams, chats = list(stream_replies), list(chat_replies)
    bodies: list[dict] = []

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
        bodies.append(json.loads(request.content))
        if request.url.path.endswith("/text/chat_stream"):
            status, body = streams.pop(0)
            if isinstance(body, str) and status == 200:
                return httpx.Response(
                    200, text=body, headers={"content-type": "text/event-stream"}
                )
            if isinstance(body, str):
                return httpx.Response(status, json=json.loads(body) if body else {})
            return httpx.Response(status, stream=body)
        status, content = chats.pop(0)
        return httpx.Response(
            status, json={"choices": [{"message": {"content": content}}]}
        )

    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(granite.httpx, "AsyncClient", factory)
    monkeypatch.setattr("app.config.WATSONX_API_KEY", "test-key")
    monkeypatch.setattr("app.config.WATSONX_PROJECT_ID", "test-project")
    monkeypatch.setattr("app.config.ASK_STREAMING", True)
    monkeypatch.setattr(granite, "_token", None)
    monkeypatch.setattr(granite, "_cache", granite.OrderedDict())
    return calls, bodies


async def _collect(*args, **kwargs) -> list[str]:
    return [p async for p in granite.chat_text_stream(*args, **kwargs)]


async def test_chat_text_returns_text_without_response_format(monkeypatch):
    bodies: list[dict] = []
    calls = _install(monkeypatch, [(200, "plain answer")])
    real_factory = granite.httpx.AsyncClient

    def spying(*args, **kwargs):
        client = real_factory(*args, **kwargs)
        original = client._transport.handle_async_request

        async def handle(request):
            if request.url.path.endswith("/text/chat"):
                bodies.append(json.loads(request.content))
            return await original(request)

        client._transport.handle_async_request = handle
        return client

    monkeypatch.setattr(granite.httpx, "AsyncClient", spying)
    assert await granite.chat_text("s", "u") == "plain answer"
    assert len(bodies) == 1
    assert "response_format" not in bodies[0]
    assert bodies[0]["temperature"] == 0
    assert sum(1 for c in calls if c.endswith("/text/chat")) == 1


async def test_chat_text_is_answered_from_cache(monkeypatch):
    calls = _install(monkeypatch, [(200, "once")])
    assert await granite.chat_text("s", "u") == "once"
    assert await granite.chat_text("s", "u") == "once"
    assert sum(1 for c in calls if c.endswith("/text/chat")) == 1


async def test_chat_text_empty_answer_is_an_error(monkeypatch):
    _install(monkeypatch, [(200, "  "), (200, "")])
    with pytest.raises(granite.GraniteError, match="empty answer"):
        await granite.chat_text("s", "u")


async def test_stream_yields_pieces_in_order(monkeypatch):
    calls, bodies = _install_stream(monkeypatch, [(200, _sse("Hel", "lo ", "world"))])
    assert await _collect("s", "u") == ["Hel", "lo ", "world"]
    assert "response_format" not in bodies[0]
    assert sum(1 for c in calls if c.endswith("/text/chat_stream")) == 1


async def test_stream_result_is_cached(monkeypatch):
    calls, _ = _install_stream(monkeypatch, [(200, _sse("a", "b"))])
    assert await _collect("s", "u") == ["a", "b"]
    assert await _collect("s", "u") == ["ab"]
    assert sum(1 for c in calls if c.endswith("/text/chat_stream")) == 1


async def test_stream_rate_limit_waits_and_retries(monkeypatch):
    monkeypatch.setattr(granite, "_RATE_LIMIT_WAITS_S", (0, 0))
    calls, _ = _install_stream(monkeypatch, [(429, ""), (429, ""), (200, _sse("fine"))])
    assert await _collect("s", "u") == ["fine"]
    assert sum(1 for c in calls if c.endswith("/text/chat_stream")) == 3


async def test_stream_quota_failure_is_not_retried(monkeypatch):
    quota = json.dumps({"errors": [{"code": "token_quota_reached", "message": "x"}]})
    calls, _ = _install_stream(monkeypatch, [(403, quota)])
    with pytest.raises(granite.GraniteUnavailable, match="token quota"):
        await _collect("s", "u")
    assert sum(1 for c in calls if c.endswith("/text/chat_stream")) == 1


async def test_stream_404_falls_back_to_plain_chat(monkeypatch):
    calls, _ = _install_stream(monkeypatch, [(404, "")], [(200, "whole answer")])
    assert await _collect("s", "u") == ["whole answer"]
    assert sum(1 for c in calls if c.endswith("/text/chat_stream")) == 1
    assert sum(1 for c in calls if c.endswith("/text/chat")) == 1


async def test_stream_cut_off_after_first_piece_is_not_retried(monkeypatch):
    calls, _ = _install_stream(
        monkeypatch, [(200, _DyingStream(_sse("Hi", done=False)))]
    )
    got: list[str] = []
    with pytest.raises(granite.GraniteError, match="cut off"):
        async for piece in granite.chat_text_stream("s", "u"):
            got.append(piece)
    assert got == ["Hi"]
    assert sum(1 for c in calls if c.endswith("/text/chat_stream")) == 1


async def test_stream_network_error_before_first_piece_is_retried(monkeypatch):
    calls, _ = _install_stream(
        monkeypatch, [(200, _DyingStream("")), (200, _sse("ok"))]
    )
    assert await _collect("s", "u") == ["ok"]
    assert sum(1 for c in calls if c.endswith("/text/chat_stream")) == 2


async def test_streaming_off_makes_one_plain_call(monkeypatch):
    calls, _ = _install_stream(monkeypatch, [], [(200, "whole")])
    monkeypatch.setattr("app.config.ASK_STREAMING", False)
    assert await _collect("s", "u") == ["whole"]
    assert sum(1 for c in calls if c.endswith("/text/chat")) == 1
    assert not any(c.endswith("/text/chat_stream") for c in calls)


async def test_stream_errors_never_contain_the_key(monkeypatch):
    key = "".join(["sk", "-", "x" * 20])
    body = json.dumps({"errors": [{"code": "boom", "message": "bad"}]})
    _install_stream(monkeypatch, [(500, body), (500, body)])
    monkeypatch.setattr("app.config.WATSONX_API_KEY", key)
    with pytest.raises(granite.GraniteError) as excinfo:
        await _collect("s", "u")
    assert key not in str(excinfo.value)
    assert key not in granite._scrub(f"failed with {key}")
