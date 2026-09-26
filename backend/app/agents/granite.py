"""
Granite on watsonx.ai — the only place that talks to the model.

Uses the watsonx.ai REST API directly (IAM token + /ml/v1/text/chat) via httpx,
which keeps the dependency tree small and works on every Python we deploy on.

    result = await chat_json(system, user, MySchema)

Every call asks for JSON only and validates it with Pydantic. A call that times
out, fails, or returns invalid JSON is retried GRANITE_RETRIES times, then
raises GraniteError. Callers decide whether that is fatal.
"""

import asyncio
import hashlib
import json
import logging
import re
import time
import types
import typing
from collections import OrderedDict
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError, model_validator

from app import config

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_token: str | None = None
_token_expires_at: float = 0.0
# asyncio primitives are bound to one event loop, so create them per loop.
_loop_primitives: dict[int, tuple[asyncio.Semaphore, asyncio.Lock]] = {}


def _primitives() -> tuple[asyncio.Semaphore, asyncio.Lock]:
    key = id(asyncio.get_running_loop())
    if key not in _loop_primitives:
        _loop_primitives.clear()
        _loop_primitives[key] = (
            asyncio.Semaphore(config.GRANITE_MAX_CONCURRENT_CHUNKS),
            asyncio.Lock(),
        )
    return _loop_primitives[key]


def _accepts(annotation: Any, kind: type) -> bool:
    """True if *annotation* is *kind* or an Optional/Union containing it."""
    if annotation is kind:
        return True
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        return kind in typing.get_args(annotation)
    return False


def _is_str_list(annotation: Any) -> bool:
    return typing.get_origin(annotation) is list and typing.get_args(annotation) == (
        str,
    )


def _as_text(value: Any) -> str:
    if isinstance(value, dict):
        return "; ".join(f"{k}: {v}" for k, v in value.items())
    return str(value)


class LenientModel(BaseModel):
    """
    Base for model-output schemas. Absorbs common LLM shape drift before
    validation: a list where text is expected is joined, a single string where
    a list is expected is wrapped, and a 0-100 confidence is scaled to 0-1.
    Missing or unusable fields still fail validation.
    """

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for name, field in cls.model_fields.items():
            if name not in data or data[name] is None:
                continue
            value = data[name]
            if _accepts(field.annotation, str) and isinstance(value, list | dict):
                data[name] = (
                    "; ".join(_as_text(v) for v in value)
                    if isinstance(value, list)
                    else _as_text(value)
                )
            elif _is_str_list(field.annotation):
                if isinstance(value, str):
                    data[name] = [value]
                elif isinstance(value, list):
                    data[name] = [_as_text(v) for v in value]
            elif (
                name == "confidence"
                and isinstance(value, int | float)
                and 1 < value <= 100
            ):
                data[name] = value / 100
        return data


# Identical prompts (temperature 0) get the same answer, so recent answers are
# reused instead of spending watsonx.ai token quota on them again.
_CACHE_TTL_S = 6 * 3600
_CACHE_MAX = 500
_cache: OrderedDict[str, tuple[float, BaseModel]] = OrderedDict()


def _cache_key(system: str, user: str, schema: type[BaseModel], max_tokens: int) -> str:
    raw = json.dumps(
        [config.GRANITE_MODEL_ID, system, user, schema.__qualname__, max_tokens]
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_get(key: str) -> BaseModel | None:
    hit = _cache.get(key)
    if hit is None or time.monotonic() - hit[0] > _CACHE_TTL_S:
        _cache.pop(key, None)
        return None
    _cache.move_to_end(key)
    return hit[1]


def _cache_put(key: str, value: BaseModel) -> None:
    _cache[key] = (time.monotonic(), value)
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)


class GraniteError(Exception):
    """A Granite call failed after retries. The message is safe to show users."""

    retryable = True


class GraniteUnavailable(GraniteError):
    """Retrying cannot help: not authorised, or the token quota is used up."""

    retryable = False


# Waits before retrying a rate-limited call (429); these do not use up GRANITE_RETRIES.
_RATE_LIMIT_WAITS_S = (1.0, 2.0, 4.0, 8.0)


class GraniteRateLimited(GraniteError):
    """watsonx.ai answered 429; worth waiting and retrying."""


def is_configured() -> bool:
    return bool(config.WATSONX_API_KEY and config.WATSONX_PROJECT_ID)


def _scrub(text: str) -> str:
    for secret in (config.WATSONX_API_KEY, _token or ""):
        if len(secret) >= 16:  # real keys and tokens are long; avoid mangling words
            text = text.replace(secret, "***")
    return text


async def _get_token(client: httpx.AsyncClient) -> str:
    global _token, _token_expires_at
    async with _primitives()[1]:
        if _token and time.time() < _token_expires_at - 60:
            return _token
        resp = await client.post(
            config.IBM_IAM_URL,
            data={
                "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
                "apikey": config.WATSONX_API_KEY,
            },
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            raise GraniteError(f"IBM Cloud sign-in failed (status {resp.status_code}).")
        body = resp.json()
        _token = body["access_token"]
        _token_expires_at = time.time() + float(body.get("expires_in", 3600))
        return _token


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _extract_json(text: str) -> object:
    """Parse the model's reply, tolerating code fences or text around one JSON object."""
    cleaned = _FENCE_RE.sub("", text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(cleaned[start : end + 1])


def _errors(resp: httpx.Response) -> list[dict]:
    try:
        body = resp.json()
    except ValueError:
        return []
    errors = body.get("errors") if isinstance(body, dict) else None
    return (
        [e for e in errors if isinstance(e, dict)] if isinstance(errors, list) else []
    )


def _reason(resp: httpx.Response) -> str:
    """watsonx.ai's own error code and message, e.g. " (project_not_found: ...)"."""
    errors = _errors(resp)
    if not errors:
        return ""
    first = errors[0]
    message = str(first.get("message", ""))[:200]
    return f" ({first.get('code', 'error')}: {message})"


async def _chat_once(
    client: httpx.AsyncClient, system: str, user: str, max_tokens: int
) -> str:
    token = await _get_token(client)
    resp = await client.post(
        f"{config.WATSONX_URL}/ml/v1/text/chat",
        params={"version": config.WATSONX_API_VERSION},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        json={
            "model_id": config.GRANITE_MODEL_ID,
            "project_id": config.WATSONX_PROJECT_ID,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        },
    )
    if resp.status_code in (401, 403):
        codes = {e.get("code") for e in _errors(resp)}
        if "token_quota_reached" in codes:
            raise GraniteUnavailable(
                "the watsonx.ai token quota for this project is used up; Granite is "
                "unavailable until it resets or the plan is upgraded"
            )
        raise GraniteUnavailable(
            "watsonx.ai refused the request (not authorised for this project). "
            "Check that the API key's user is a member of WATSONX_PROJECT_ID."
        )
    if resp.status_code == 429:
        raise GraniteRateLimited(
            "watsonx.ai rate limit reached. Please try again shortly."
        )
    if resp.status_code != 200:
        raise GraniteError(
            f"watsonx.ai returned status {resp.status_code}{_reason(resp)}"
        )
    choices = resp.json().get("choices") or []
    if not choices:
        raise GraniteError("watsonx.ai returned no answer.")
    return choices[0].get("message", {}).get("content") or ""


async def chat_json(
    system: str, user: str, schema: type[T], *, max_tokens: int = 1500
) -> T:
    """Ask Granite for JSON matching *schema*. Raises GraniteError on failure."""
    if not is_configured():
        raise GraniteError("Granite is not configured on this server.")
    key = _cache_key(system, user, schema, max_tokens)
    if (cached := _cache_get(key)) is not None:
        log.info("granite: reusing cached %s answer", schema.__name__)
        return cached.model_copy(deep=True)  # type: ignore[return-value]

    last_error = "unknown error"
    attempts = 0  # failures other than rate limiting
    waits = iter(_RATE_LIMIT_WAITS_S)
    async with (
        _primitives()[0],
        httpx.AsyncClient(timeout=config.GRANITE_TIMEOUT_S) as client,
    ):
        while attempts <= config.GRANITE_RETRIES:
            text = ""
            try:
                async with asyncio.timeout(config.GRANITE_TIMEOUT_S):
                    text = await _chat_once(client, system, user, max_tokens)
                result = schema.model_validate(_extract_json(text))
                _cache_put(key, result)
                return result.model_copy(deep=True)
            except GraniteRateLimited as exc:
                last_error = str(exc)
                wait = next(waits, None)
                if wait is None:
                    break
                log.info("granite: rate limited, retrying in %.0f s", wait)
                await asyncio.sleep(wait)
                continue  # does not count as a failed attempt
            except GraniteError as exc:
                last_error = str(exc)
                if not exc.retryable:
                    break
            except TimeoutError:
                last_error = f"no answer within {config.GRANITE_TIMEOUT_S} s"
            except httpx.HTTPError as exc:
                last_error = f"network error ({type(exc).__name__})"
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_error = "the model returned invalid JSON"
                if isinstance(exc, ValidationError):
                    detail = "; ".join(
                        f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
                        for e in exc.errors()[:3]
                    )
                else:
                    detail = f"{type(exc).__name__} at char {getattr(exc, 'pos', '?')} of {len(text)}"
                log.warning(
                    "granite: %s response rejected: %s", schema.__name__, detail
                )
            attempts += 1
            log.warning("granite: attempt %d failed: %s", attempts, _scrub(last_error))
    raise GraniteError(f"Granite call failed: {_scrub(last_error)}.")
