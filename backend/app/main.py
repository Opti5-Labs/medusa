"""
FastAPI application entry point.

Registers routers, CORS, and global exception handlers.
All business logic lives in app/api/*.
"""

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.api import health as health_router
from app.api import scan as scan_router
from app.config import ALLOWED_ORIGIN
from app.errors import MedusaError, medusa_error_handler
from app.models.contracts import (
    DebugSession,
    FixAttempt,
    LogEvent,
    Recommendation,
    ReproAttempt,
)
from app.store import RunStore

log = logging.getLogger(__name__)

# ── Application store (singleton) ──────────────────────────────────────────────

store = RunStore()


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.start()
    scan_router.set_store(store)
    log.info("Medusa started — store TTL sweeper running")
    yield
    await store.stop()
    log.info("Medusa stopped")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Medusa", version="0.1.0", lifespan=lifespan)

# ── Exception handlers ─────────────────────────────────────────────────────────

app.add_exception_handler(MedusaError, medusa_error_handler)  # type: ignore[arg-type]

# ── CORS ───────────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_credentials=False,  # no cookies used
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Accept"],
)

# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(health_router.router)
app.include_router(scan_router.router)


# ── Stub routes for pipelines not yet built (Person 1/2) ─────────────────────
# These keep the frontend stubs working without changes.


@app.post("/api/issues/{issue_id}/repro", response_model=ReproAttempt)
async def start_repro(issue_id: str) -> ReproAttempt:
    return ReproAttempt(
        attempt_id=str(uuid.uuid4()),
        issue_id=issue_id,
        mode="reasoning",
        status="running",
        log=[],
    )


@app.get("/api/repro/{attempt_id}/events")
async def repro_events(attempt_id: str) -> StreamingResponse:
    async def event_stream():
        for msg in (
            "Repro stub started.",
            "(placeholder — real pipeline not implemented)",
            "Stub complete.",
        ):
            level = "result" if "complete" in msg else "info"
            ev = LogEvent(ts=time.time(), source="stub", level=level, message=msg)
            yield f"event: log\ndata: {ev.model_dump_json()}\n\n"
        final = ReproAttempt(
            attempt_id=attempt_id,
            issue_id="unknown",
            mode="reasoning",
            status="plausible",
            log=[],
            confidence=0.0,
        )
        yield f"event: done\ndata: {final.model_dump_json()}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/issues/{issue_id}/debug", response_model=DebugSession)
async def start_debug(issue_id: str, body: dict) -> DebugSession:
    n = max(2, min(6, int(body.get("candidates", 2))))
    candidates = [
        FixAttempt(
            candidate_id=f"c{i + 1}",
            approach=f"Stub candidate {i + 1}",
            sandbox_status="not_applicable",
            active=True,
        )
        for i in range(n)
    ]
    return DebugSession(
        session_id=str(uuid.uuid4()),
        issue_id=issue_id,
        mode="reasoning",
        candidates=candidates,
    )


@app.get("/api/debug/{session_id}/events")
async def debug_events(session_id: str) -> StreamingResponse:
    async def event_stream():
        import json

        ev = LogEvent(
            ts=time.time(),
            source="candidate:c1",
            level="info",
            message="Debug stub started.",
        )
        yield f"event: log\ndata: {ev.model_dump_json()}\n\n"
        session = DebugSession(
            session_id=session_id,
            issue_id="unknown",
            mode="reasoning",
            candidates=[
                FixAttempt(
                    candidate_id="c1",
                    approach="Stub candidate 1",
                    sandbox_status="not_applicable",
                    active=True,
                )
            ],
        )
        recommendation = Recommendation(
            candidate_id="c1",
            reason="Stub recommendation — debug pipeline not implemented yet.",
        )
        payload = {
            "session": session.model_dump(),
            "recommendation": recommendation.model_dump(),
        }
        yield f"event: done\ndata: {json.dumps(payload)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/debug/{session_id}/download")
async def debug_download(session_id: str, candidate_id: str):
    from fastapi import HTTPException

    raise HTTPException(status_code=501, detail="Download not implemented yet.")
