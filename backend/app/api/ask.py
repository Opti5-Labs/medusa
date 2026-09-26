"""
Ask Medusa API.

    GET  /api/ask/status                -> AskStatus (no model calls)
    POST /api/scan/{scan_id}/ask        -> AskStart
    GET  /api/ask/{ask_id}/events       -> SSE: log and token events, then done (AskAnswer)

Questions that need a model have a tight per-IP limit; instant answers from scan
data have a much more generous one. Streams replay from the start, like the run
streams.
"""

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app import config
from app.agents import bob, granite
from app.api.runs import _sse
from app.errors import MedusaError
from app.models.contracts import AskStart, AskStatus
from app.pipelines import ask as pipeline
from app.pipelines.ask import start_ask
from app.ratelimit import RateLimiter
from app.store import RunStore, StoreFullError

router = APIRouter()

_ask_limiter = RateLimiter(
    max_calls=config.RATE_ASK_PER_WINDOW,
    window_seconds=config.RATE_WINDOW_SECONDS,
    what="questions",
)

_ask_instant_limiter = RateLimiter(
    max_calls=config.RATE_ASK_INSTANT_PER_WINDOW,
    window_seconds=config.RATE_WINDOW_SECONDS,
    what="questions",
)

_store: RunStore | None = None


def set_store(store: RunStore) -> None:
    global _store
    _store = store


def _get_store() -> RunStore:
    assert _store is not None, "RunStore not initialised"
    return _store


class AskRequest(BaseModel):
    model_config = {"extra": "forbid"}

    question: str
    issue_id: str | None = None


@router.get("/api/ask/status", response_model=AskStatus)
async def ask_status() -> AskStatus:
    reason = bob.live_unavailable_reason()
    return AskStatus(
        enabled=config.ASK_ENABLED,
        granite_available=granite.is_configured(),
        bob_available=reason is None,
        bob_reason=reason,
    )


@router.post("/api/scan/{scan_id}/ask", response_model=AskStart)
async def ask(scan_id: str, body: AskRequest, request: Request) -> AskStart:
    if not config.ASK_ENABLED:
        raise MedusaError(503, "Questions are switched off on this server.")
    question = body.question.strip()
    if not question or len(question) > config.ASK_MAX_QUESTION_CHARS:
        raise MedusaError(
            422, f"Type a question (up to {config.ASK_MAX_QUESTION_CHARS} characters)."
        )
    store = _get_store()
    record = await store.get(scan_id)
    if record is None:
        raise MedusaError(
            404, "This scan has expired or was not found. Please run the scan again."
        )
    issue = None
    if body.issue_id is not None:
        issue = next((i for i in record.result.issues if i.id == body.issue_id), None)
        if issue is None:
            raise MedusaError(404, "That issue is not part of this scan.")
    # No await between this check and store.add_ask (inside start_ask, before its
    # first await), so two requests cannot both pass the check.
    if store.ask_running_for(scan_id):
        raise MedusaError(
            409, "Still answering your previous question. Please wait a moment."
        )
    if pipeline.classify(question, body.issue_id) == "model":
        _ask_limiter.check(request)
    else:
        _ask_instant_limiter.check(request)
    try:
        run = await start_ask(store, record, question, issue)
    except StoreFullError as exc:
        raise MedusaError(503, str(exc)) from exc
    return AskStart(ask_id=run.ask_id)


@router.get("/api/ask/{ask_id}/events")
async def ask_events(ask_id: str) -> EventSourceResponse:
    run = _get_store().ask_runs.get(ask_id)
    if run is None:
        raise MedusaError(404, "Answer not found or expired.")
    return _sse(run.channel)
