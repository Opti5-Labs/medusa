"""
Reproduce and debug API.

    POST /api/issues/{issue_id}/repro              -> ReproAttempt (status "running")
    GET  /api/repro/{attempt_id}/events            -> SSE: log events, then done (ReproAttempt)
    POST /api/issues/{issue_id}/debug              -> DebugSession
    GET  /api/debug/{session_id}/events            -> SSE: log events, then done (DebugDone)
    GET  /api/debug/{session_id}/download?candidate_id=  -> zip with the fix applied
    GET  /api/debug/{session_id}/patch?candidate_id=     -> the fix as a git-applyable .patch

Starting a run is rate limited per IP. Streams replay from the start, so a
reconnecting browser gets the full log.
"""

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app import config
from app.demo import optilearn
from app.errors import MedusaError
from app.models.contracts import DebugSession, ReproAttempt
from app.pipelines.debug import start_debug
from app.pipelines.repro import is_sandboxed, start_repro
from app.ratelimit import RateLimiter
from app.store import RunStore, StoreFullError
from app.streaming import EventChannel

router = APIRouter()

_run_limiter = RateLimiter(
    max_calls=config.RATE_REPRO_DEBUG_PER_WINDOW,
    window_seconds=config.RATE_WINDOW_SECONDS,
    what="reproduce or debug runs",
)

_store: RunStore | None = None


def set_store(store: RunStore) -> None:
    global _store
    _store = store


def _get_store() -> RunStore:
    assert _store is not None, "RunStore not initialised"
    return _store


async def _find_issue(issue_id: str):
    found = await _get_store().find_issue(issue_id)
    if found is None:
        raise MedusaError(
            404, "Issue not found or the scan has expired. Please run the scan again."
        )
    return found


class DebugRequest(BaseModel):
    model_config = {"extra": "forbid"}

    candidates: int | None = Field(
        default=None, ge=config.DEBUG_MIN_CANDIDATES, le=config.DEBUG_MAX_CANDIDATES
    )


def _sse(channel: EventChannel) -> EventSourceResponse:
    async def events() -> AsyncIterator[dict]:
        async for name, data in channel.stream():
            yield {"event": name, "data": data}

    return EventSourceResponse(events(), ping=15, send_timeout=30)


@router.post("/api/issues/{issue_id}/repro", response_model=ReproAttempt)
async def repro(issue_id: str, request: Request) -> ReproAttempt:
    record, issue = await _find_issue(issue_id)
    _run_limiter.check(request)
    try:
        run = await start_repro(_get_store(), record, issue)
    except StoreFullError as exc:
        raise MedusaError(503, str(exc)) from exc
    return run.attempt


@router.get("/api/repro/{attempt_id}/events")
async def repro_events(attempt_id: str) -> EventSourceResponse:
    run = _get_store().repro_runs.get(attempt_id)
    if run is None:
        raise MedusaError(404, "Reproduce run not found or expired.")
    return _sse(run.channel)


@router.post("/api/issues/{issue_id}/debug", response_model=DebugSession)
async def debug(
    issue_id: str, request: Request, body: DebugRequest | None = None
) -> DebugSession:
    record, issue = await _find_issue(issue_id)
    sandboxed = is_sandboxed(record, issue)
    requested = body.candidates if body else None
    if sandboxed:
        n = requested or config.DEBUG_DEFAULT_CANDIDATES_DEMO
    else:
        # General repos default to, and are capped at, the general default.
        n = min(
            requested or config.DEBUG_DEFAULT_CANDIDATES_GENERAL,
            config.DEBUG_DEFAULT_CANDIDATES_GENERAL,
        )
    _run_limiter.check(request)
    try:
        run = await start_debug(_get_store(), record, issue, n)
    except StoreFullError as exc:
        raise MedusaError(503, str(exc)) from exc
    return run.session


@router.get("/api/debug/{session_id}/events")
async def debug_events(session_id: str) -> EventSourceResponse:
    run = _get_store().debug_runs.get(session_id)
    if run is None:
        raise MedusaError(404, "Debug session not found or expired.")
    return _sse(run.channel)


@router.get("/api/debug/{session_id}/download")
async def download(session_id: str, candidate_id: str) -> Response:
    run = _get_store().debug_runs.get(session_id)
    if run is None:
        raise MedusaError(404, "Debug session not found or expired.")
    if run.session.mode != "sandboxed":
        raise MedusaError(
            409,
            "Download is only available for fixes verified in the sandbox (the OptiLearn demo).",
        )
    candidate = next(
        (c for c in run.session.candidates if c.candidate_id == candidate_id), None
    )
    if candidate is None:
        raise MedusaError(404, "Unknown candidate.")
    if candidate.sandbox_status != "passed":
        raise MedusaError(
            409, "Only candidates that passed verification can be downloaded."
        )
    workdir = run.workdirs.get(candidate_id)
    if not run.workdirs:
        raise MedusaError(
            409,
            "A zip is only available for the OptiLearn demo. Download the .patch instead.",
        )
    if workdir is None or not workdir.exists():
        raise MedusaError(
            410, "This fix is no longer available. Please run debug again."
        )
    data = await asyncio.to_thread(optilearn.zip_tree, workdir)
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="optilearn-fix-{candidate_id}.zip"'
        },
    )


def _patch_header(run, candidate) -> str:
    """Comment lines placed before the diff; `git apply` ignores them."""
    lines = [f"Medusa fix candidate {candidate.candidate_id}: {candidate.approach}"]
    if candidate.origin:
        lines.append(f"Proposed by: {candidate.origin}")
    r = candidate.test_results
    if run.session.mode == "sandboxed" and r is not None:
        lines.append(
            f"Verified in Medusa's sandbox: reproducer fixed, {r.passed}/{r.total} "
            "checks passed, no regressions."
        )
    else:
        lines.append(
            "NOT executed or tested: this patch was proposed by reading the code only. "
            "Review it and run your tests before applying."
        )
    lines.append(
        "Apply from the repository root with: git apply <this file> "
        "(if git refuses it, patch -p1 < <this file> is more forgiving)"
    )
    return "".join(f"# {line}\n" for line in lines) + "\n"


@router.get("/api/debug/{session_id}/patch")
async def download_patch(session_id: str, candidate_id: str) -> Response:
    run = _get_store().debug_runs.get(session_id)
    if run is None:
        raise MedusaError(404, "Debug session not found or expired.")
    candidate = next(
        (c for c in run.session.candidates if c.candidate_id == candidate_id), None
    )
    if candidate is None:
        raise MedusaError(404, "Unknown candidate.")
    if not candidate.patch:
        raise MedusaError(409, "This candidate has no patch to download.")
    if run.session.mode == "sandboxed" and candidate.sandbox_status != "passed":
        raise MedusaError(
            409, "Only candidates that passed verification can be downloaded."
        )
    patch = (
        candidate.patch if candidate.patch.endswith("\n") else candidate.patch + "\n"
    )
    verified = run.session.mode == "sandboxed"
    name = f"medusa-{candidate_id}{'' if verified else '-untested'}.patch"
    return Response(
        content=_patch_header(run, candidate) + patch,
        media_type="text/x-diff; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
